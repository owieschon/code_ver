#!/usr/bin/env python3
"""emit_record.py — TrustLadder Stage 1 run-record constructor + validator.

Constructs and validates the per-run record. Field names are frozen.

- Validates against schema/run_record.schema.json via TWO lanes:
  a deterministic hand validator (always runs; also enforces the
  null_reasons coverage rule and the verdict_events item shape, which
  plain JSON Schema cannot express) and, when the `jsonschema` package
  is importable, the library validator as a second lane. Both must pass.
- Unpopulatable fields: emit null AND register the dot-path in the
  top-level null_reasons map with a code from schema/reason_codes.json.
  Below-floor measures emit the string "<FLOOR" (e.g. "<0.0001") and
  register BELOW_MEASUREMENT_FLOOR. Omission without a reason code is
  a loosening; validation REFUSES it.
- emit() REFUSES on any schema violation and REFUSES to overwrite an
  existing records/<run_id>.json (records/ is append-only).
- Signed receipts (VALUABLE tier, BUILT — schema/signing/): emit(...,
  sign=True) populates the four receipt fields via signing/receipt.py
  (record_hash, prev_record_hash chain, Ed25519 signature,
  signer_key_id; key at install OUTSIDE the agent-writable surface,
  ~/.trustladder/keys/). Write ordering: validate -> sign -> re-validate
  -> write record -> advance receipts/chain_head.json. The unsigned
  lane (null + DEFERRED_UNSIGNED, skeleton() default) stays legal. No
  network, no clock reads: all timestamps are caller-supplied
  (deterministic).

Demonstrated-red receipts (house discipline):
  receipts/emit_record_red_missing_claim.txt
  receipts/emit_record_red_null_without_reason.txt
  receipts/emit_record_red_duplicate_run_id.txt
  receipts/signing_red_no_key.txt          (sign requested, no key)
  receipts/chain_red.txt                   (record altered after signing)
"""

import json
import os
import re
import sys

SCHEMA_DIR = os.path.dirname(os.path.abspath(__file__))
HARNESS_DIR = os.path.dirname(SCHEMA_DIR)
RECORD_SCHEMA_PATH = os.path.join(SCHEMA_DIR, "run_record.schema.json")
EVENT_SCHEMA_PATH = os.path.join(SCHEMA_DIR, "telemetry_event.schema.json")
REASON_CODES_PATH = os.path.join(SCHEMA_DIR, "reason_codes.json")
DEFAULT_RECORDS_DIR = os.path.join(HARNESS_DIR, "records")

BELOW_FLOOR_RE = re.compile(r"^<[0-9]")

# Dot-paths at which a measure may legally be a below-floor string.
# (Array interiors — gate_decisions[*] and costs.verdict_events[*] —
# are matched by prefix in _collect_flagged_paths.)
_MEASURE_KEYS = {"latency_ms", "tokens", "dollars", "tokens_in",
                 "tokens_out", "wall_clock_s"}


def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_record_schema():
    return _load_json(RECORD_SCHEMA_PATH)


def load_event_schema():
    return _load_json(EVENT_SCHEMA_PATH)


def load_reason_codes():
    return set(_load_json(REASON_CODES_PATH)["codes"].keys())


# ---------------------------------------------------------------------------
# Hand validator: deterministic interpreter for the JSON Schema subset
# actually used by schema/ files (type, enum, required, properties,
# items, anyOf, pattern, minimum, minLength). Data-driven from the
# schema file itself so the two lanes cannot silently diverge.
# ---------------------------------------------------------------------------

def _type_ok(value, tname):
    if tname == "null":
        return value is None
    if tname == "boolean":
        return isinstance(value, bool)
    if tname == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if tname == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if tname == "string":
        return isinstance(value, str)
    if tname == "object":
        return isinstance(value, dict)
    if tname == "array":
        return isinstance(value, list)
    raise ValueError("unsupported type keyword in schema: %r" % tname)


def _check(value, schema, path, errors):
    if "anyOf" in schema:
        branch_errs = []
        for branch in schema["anyOf"]:
            errs = []
            _check(value, branch, path, errs)
            if not errs:
                return
            branch_errs.append(errs)
        errors.append("%s: matched no anyOf branch (closest: %s)"
                      % (path, "; ".join(branch_errs[0][:2])))
        return
    if "enum" in schema:
        if value not in schema["enum"]:
            errors.append("%s: %r not in enum %r" % (path, value, schema["enum"]))
        return
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(value, t) for t in types):
            errors.append("%s: expected type %s, got %r"
                          % (path, "/".join(types), type(value).__name__))
            return
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append("%s: %r does not match pattern %r"
                          % (path, value, schema["pattern"]))
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append("%s: string shorter than minLength %d"
                          % (path, schema["minLength"]))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append("%s: %r below minimum %r"
                          % (path, value, schema["minimum"]))
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errors.append("%s: missing required field '%s'" % (path, req))
        for key, sub in schema.get("properties", {}).items():
            if key in value:
                _check(value[key], sub, "%s.%s" % (path, key) if path else key,
                       errors)
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            _check(item, schema["items"], "%s[%d]" % (path, i), errors)


def hand_validate(instance, schema):
    """Return a list of error strings (empty == valid)."""
    errors = []
    _check(instance, schema, "", errors)
    return errors


def validate_event_dict(event):
    """Validate one verdict-telemetry event against telemetry_event.schema.json.
    Shared entry point: telemetry.py (append), derive.py (read), and this
    module (costs.verdict_events items) all call it. Returns error list."""
    return hand_validate(event, load_event_schema())


# ---------------------------------------------------------------------------
# Null-reason coverage (frozen convention)
# ---------------------------------------------------------------------------

def _collect_flagged_paths(record):
    """Dot-paths whose value is null or a below-floor string.
    Walks top-level and one nested-object level (turn_budget, costs,
    claim, verdict, grader_provenance, disagreement). Array elements are
    schema-constrained non-null and are not reason-tracked individually."""
    flagged = {}

    def visit(value, path):
        if value is None:
            flagged[path] = "null"
        elif isinstance(value, str) and BELOW_FLOOR_RE.match(value):
            key = path.rsplit(".", 1)[-1]
            if key in _MEASURE_KEYS:
                flagged[path] = "below_floor"
        elif isinstance(value, dict):
            for k, v in value.items():
                visit(v, "%s.%s" % (path, k) if path else k)

    for k, v in record.items():
        if k == "null_reasons":
            continue
        visit(v, k)
    return flagged


def check_null_reasons(record, reason_codes):
    errors = []
    null_reasons = record.get("null_reasons")
    if not isinstance(null_reasons, dict):
        return ["null_reasons: missing or not an object"]
    flagged = _collect_flagged_paths(record)
    for path, kind in sorted(flagged.items()):
        if path not in null_reasons:
            errors.append(
                "null_reasons: field '%s' is %s but carries no reason code "
                "(omission without a reason code is a loosening)" % (path, kind))
        elif kind == "below_floor" and null_reasons[path] != "BELOW_MEASUREMENT_FLOOR":
            errors.append(
                "null_reasons: field '%s' is a below-floor string but reason is "
                "%r, expected BELOW_MEASUREMENT_FLOOR" % (path, null_reasons[path]))
    for path, code in sorted(null_reasons.items()):
        if code not in reason_codes:
            errors.append("null_reasons: '%s' carries unknown reason code %r "
                          "(enum: schema/reason_codes.json)" % (path, code))
        if path not in flagged:
            errors.append("null_reasons: stale entry '%s' — field is neither "
                          "null nor below-floor" % path)
    return errors


# ---------------------------------------------------------------------------
# Record validation + emission
# ---------------------------------------------------------------------------

# Dummy runs deviate from the experimental schema in exactly two enum fields
# BY DESIGN (a non-battery mechanics task is neither TYPICAL/NOVEL nor in a
# registered batch). Any OTHER validation error still refuses the record.
# Single source for every dummy-tolerant consumer (runner/dispatch.py emit
# lane; schema/eval_export/export_eval.py) so the tolerance cannot drift
# between consumers; dispatch aliases it.
DUMMY_EXPECTED_ERRORS = ("stratum:", "jsonschema lane: None is not one of "
                         "['TYPICAL', 'NOVEL']", "batch:",
                         "null_reasons: field 'stratum'")


def validate_record(record):
    """Full validation. Returns list of error strings (empty == valid)."""
    if not isinstance(record, dict):
        return ["record: not a JSON object"]
    schema = load_record_schema()
    errors = hand_validate(record, schema)

    # Lane 2: jsonschema library, when importable (stdlib-preferred; the
    # hand lane above is authoritative and always runs).
    try:
        import jsonschema  # type: ignore
        try:
            jsonschema.validate(instance=record, schema=schema)
        except jsonschema.ValidationError as e:  # pragma: no cover - mirrors lane 1
            errors.append("jsonschema lane: %s (at %s)"
                          % (e.message, "/".join(str(p) for p in e.absolute_path)))
    except ImportError:
        pass

    # Conventions plain JSON Schema cannot express:
    errors += check_null_reasons(record, load_reason_codes())
    event_schema = load_event_schema()
    costs = record.get("costs")
    if isinstance(costs, dict) and isinstance(costs.get("verdict_events"), list):
        for i, ev in enumerate(costs["verdict_events"]):
            for err in hand_validate(ev, event_schema):
                errors.append("costs.verdict_events[%d]: %s" % (i, err))
            if isinstance(ev, dict) and ev.get("run_id") != record.get("run_id"):
                errors.append("costs.verdict_events[%d]: run_id %r does not "
                              "match record run_id %r"
                              % (i, ev.get("run_id"), record.get("run_id")))
    return errors


def _signing():
    """Lazy import of the signing layer, kept inside the function so the
    unsigned record path never imports it (and never touches `cryptography`,
    which `receipt` itself only imports when actually signing)."""
    from trustladder.schema.signing import receipt
    return receipt


def emit(record, records_dir=DEFAULT_RECORDS_DIR, sign=False,
         key_dir=None, chain_head_path=None):
    """Validate then write records/<run_id>.json. REFUSES on schema
    violation and on overwrite (append-only store). With sign=True the
    four receipt fields are populated (signing/receipt.py): validate ->
    sign -> re-validate -> write -> advance the chain head (chain state
    advances only after the record file is durably written). Returns
    the written path."""
    errors = validate_record(record)
    if errors:
        raise ValueError(
            "REFUSED: record violates run_record.schema.json / null-reason "
            "convention; nothing written.\n  - " + "\n  - ".join(errors))
    out_path = os.path.join(records_dir, "%s.json" % record["run_id"])
    if os.path.exists(out_path):
        raise ValueError(
            "REFUSED: %s already exists; records/ is append-only "
            "(ARCHITECTURE.md Sec. 1) — emitting a second record for run_id "
            "%r would overwrite history." % (out_path, record["run_id"]))
    receipt = None
    if sign:
        receipt = _signing()
        record = receipt.sign_record(record, key_dir=key_dir,
                                     chain_head_path=chain_head_path)
        errors = validate_record(record)
        if errors:
            raise ValueError(
                "REFUSED: SIGNED record violates run_record.schema.json / "
                "null-reason convention; nothing written.\n  - "
                + "\n  - ".join(errors))
    os.makedirs(records_dir, exist_ok=True)
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp_path, out_path)
    if sign:
        receipt.advance_chain_head(record, chain_head_path=chain_head_path)
    return out_path


def skeleton(run_id, task_id, arm, stratum, family, batch, model_id,
             cli_version, started_at, ended_at, turn_budget_limit,
             turn_budget_used, tree_hash, transcript_ref, ls_audit_ref,
             claim, evidence_refs, costs_tokens_in, costs_tokens_out,
             costs_dollars, costs_wall_clock_s, verdict_events,
             gate_decisions=None, policy_fingerprint=None,
             policy_proof_ref=None):
    """Runner skeleton: identity + claim + costs + evidence_refs + gate
    fields populated; grading fields null + PENDING_GRADING; receipt
    fields null + DEFERRED_UNSIGNED (signing is a separate VALUABLE-tier
    step). For bare arms (L0/L1) the three gate fields default to null +
    NOT_APPLICABLE_ARM (contamination fence)."""
    bare = arm in ("L0", "L1")
    null_reasons = {
        "verdict": "PENDING_GRADING",
        "grader_provenance": "PENDING_GRADING",
        "disagreement": "PENDING_GRADING",
        "costs.cost_per_verified_completion": "PENDING_GRADING",
        "costs.cost_per_claimed_completion": "PENDING_GRADING",
        "record_hash": "DEFERRED_UNSIGNED",
        "prev_record_hash": "DEFERRED_UNSIGNED",
        "signature": "DEFERRED_UNSIGNED",
        "signer_key_id": "DEFERRED_UNSIGNED",
    }
    if bare:
        if gate_decisions is not None or policy_fingerprint is not None \
                or policy_proof_ref is not None:
            raise ValueError(
                "REFUSED: bare arm %r must not carry in-band gate fields "
                "(contamination fence, ARCHITECTURE.md Sec. 5)" % arm)
        null_reasons["gate_decisions"] = "NOT_APPLICABLE_ARM"
        null_reasons["policy_fingerprint"] = "NOT_APPLICABLE_ARM"
        null_reasons["policy_proof_ref"] = "NOT_APPLICABLE_ARM"
    record = {
        "run_id": run_id, "task_id": task_id, "arm": arm,
        "stratum": stratum, "family": family, "batch": batch,
        "model_id": model_id, "cli_version": cli_version,
        "started_at": started_at, "ended_at": ended_at,
        "turn_budget": {"limit": turn_budget_limit, "used": turn_budget_used},
        "tree_hash": tree_hash, "transcript_ref": transcript_ref,
        "ls_audit_ref": ls_audit_ref,
        "claim": claim, "evidence_refs": evidence_refs,
        "gate_decisions": gate_decisions,
        "policy_fingerprint": policy_fingerprint,
        "policy_proof_ref": policy_proof_ref,
        "costs": {
            "tokens_in": costs_tokens_in, "tokens_out": costs_tokens_out,
            "dollars": costs_dollars, "wall_clock_s": costs_wall_clock_s,
            "verdict_events": verdict_events,
            "cost_per_verified_completion": None,
            "cost_per_claimed_completion": None,
        },
        "verdict": None, "grader_provenance": None, "disagreement": None,
        "record_hash": None, "prev_record_hash": None,
        "signature": None, "signer_key_id": None,
        "null_reasons": null_reasons,
    }
    return record


def main(argv):
    if len(argv) >= 2 and argv[0] == "validate":
        record = _load_json(argv[1])
        errors = validate_record(record)
        if errors:
            sys.stderr.write(
                "REFUSED: record violates run_record.schema.json / "
                "null-reason convention:\n  - " + "\n  - ".join(errors) + "\n")
            return 1
        print("OK: %s validates against run_record.schema.json "
              "(both lanes + null-reason convention)" % argv[1])
        return 0
    if len(argv) >= 2 and argv[0] == "emit":
        records_dir, sign, key_dir, chain_head_path = \
            DEFAULT_RECORDS_DIR, False, None, None
        rest = argv[2:]
        while rest:
            if rest[0] == "--records-dir" and len(rest) >= 2:
                records_dir, rest = rest[1], rest[2:]
            elif rest[0] == "--sign":
                sign, rest = True, rest[1:]
            elif rest[0] == "--key-dir" and len(rest) >= 2:
                key_dir, rest = rest[1], rest[2:]
            elif rest[0] == "--chain-head" and len(rest) >= 2:
                chain_head_path, rest = rest[1], rest[2:]
            else:
                sys.stderr.write("unknown argument: %s\n" % rest[0])
                return 2
        record = _load_json(argv[1])
        try:
            path = emit(record, records_dir, sign=sign, key_dir=key_dir,
                        chain_head_path=chain_head_path)
        except ValueError as e:
            sys.stderr.write(str(e) + "\n")
            return 1
        print("EMITTED%s: %s" % (" (signed, chain advanced)" if sign else
                                 " (unsigned lane, DEFERRED_UNSIGNED)", path))
        return 0
    sys.stderr.write(
        "usage: emit_record.py validate <record.json>\n"
        "       emit_record.py emit <record.json> [--records-dir DIR]\n"
        "                      [--sign] [--key-dir DIR] [--chain-head PATH]\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
