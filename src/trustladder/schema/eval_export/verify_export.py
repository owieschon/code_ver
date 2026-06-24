#!/usr/bin/env python3
"""Fidelity check for the Inspect-compatible .eval export.

Verifies, stdlib-only, that records/<run_id>.eval is a faithful derivative
of its canonical native record:

  V1  zip members are exactly the characterized set (header, sample,
      summaries, journal) — no missing/extra members.
  V2  header.eval.run_id / task_id / model match the native record.
  V3  the embedded canonical record (eval.metadata.trustladder_record) is
      BYTE-FAITHFUL: canonical-JSON hash equals the native record's.
  V4  null+reason convention holds IN THE EXPORT: trustladder_export.
      null_reasons is present, every code is in the live enum
      (schema/reason_codes.json), and the standing unconvertible positions
      (config.message_limit, target, events) are registered; pending
      verdict => samples[0].scores registered (or scores populated).
  V5  usage totals in the sample's model_usage equal the native record's
      costs.tokens_in / costs.tokens_out.
  V6  the final assistant message text equals the native claim.text.
  V7  summaries.json agrees with the sample (id, epoch, message_count).
  V8  determinism: re-running the exporter reproduces the .eval
      byte-for-byte (requires source record + transcript on disk).

Usage: python3 verify_export.py <record.json> [<export.eval>]
Exit 0 = all green; exit 1 = at least one check failed (failures listed).
"""

import hashlib
import json
import os
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_DIR = os.path.dirname(HERE)
from trustladder.schema import emit_record
from trustladder.schema.eval_export import export_eval

EXPECTED_FIXED_MEMBERS = {"header.json", "summaries.json",
                          "_journal/start.json", "_journal/summaries/1.json"}


def canonical_hash(obj):
    return hashlib.sha256(json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()).hexdigest()


def verify(record_path, eval_path):
    failures = []

    def ok(label):
        print("  PASS %s" % label)

    def bad(label, msg):
        failures.append("%s: %s" % (label, msg))
        print("  FAIL %s: %s" % (label, msg))

    record = json.load(open(record_path))
    z = zipfile.ZipFile(eval_path)
    names = set(z.namelist())

    # V1 members
    sample_members = {n for n in names if n.startswith("samples/")}
    expected = EXPECTED_FIXED_MEMBERS | {"samples/%s_epoch_1.json" % record["task_id"]}
    if names == expected and len(sample_members) == 1:
        ok("V1 zip members")
    else:
        bad("V1 zip members", "got %s expected %s" % (sorted(names), sorted(expected)))
        return failures  # structure broken; later checks would be noise

    header = json.loads(z.read("header.json"))
    sample = json.loads(z.read("samples/%s_epoch_1.json" % record["task_id"]))
    summaries = json.loads(z.read("summaries.json"))
    spec = header["eval"]

    # V2 identity
    ident_ok = (spec.get("run_id") == record["run_id"]
                and spec.get("task_id") == record["task_id"]
                and spec.get("model") == "anthropic/%s" % record["model_id"])
    if ident_ok:
        ok("V2 identity (run_id/task_id/model)")
    else:
        bad("V2 identity", "header.eval=%r vs record run_id=%s task_id=%s model_id=%s"
            % ({k: spec.get(k) for k in ("run_id", "task_id", "model")},
               record["run_id"], record["task_id"], record["model_id"]))

    # V3 embedded canonical record byte-faithful
    embedded = spec.get("metadata", {}).get("trustladder_record")
    if embedded is None:
        bad("V3 embedded record", "eval.metadata.trustladder_record missing")
    elif canonical_hash(embedded) == canonical_hash(record):
        ok("V3 embedded canonical record hash")
    else:
        bad("V3 embedded record", "canonical-JSON hash mismatch: embedded=%s native=%s"
            % (canonical_hash(embedded), canonical_hash(record)))

    # V4 null+reason convention in the export
    meta = spec.get("metadata", {}).get("trustladder_export", {})
    nr = meta.get("null_reasons")
    if not isinstance(nr, dict):
        bad("V4 null_reasons", "trustladder_export.null_reasons missing")
    else:
        valid = emit_record.load_reason_codes()  # live enum, returned as a set
        bad_codes = {k: v for k, v in nr.items() if v not in valid}
        missing = [p for p in ("eval.config.message_limit", "samples[0].target",
                               "samples[0].events") if p not in nr]
        if record.get("verdict") is None and "scores" not in sample \
                and "samples[0].scores" not in nr:
            missing.append("samples[0].scores (verdict pending, scores absent)")
        if bad_codes:
            bad("V4 null_reasons", "codes outside the live enum: %r" % bad_codes)
        elif missing:
            bad("V4 null_reasons", "unregistered null position(s): %s" % missing)
        else:
            ok("V4 null+reason convention (codes valid, positions registered)")

    # V5 usage totals (an absent model_usage entry counts as 0/0 — the
    # dry-run lane has no result event, so the export carries no usage)
    mu = sample.get("model_usage", {}).get(record["model_id"], {})
    if (mu.get("input_tokens", 0) == record["costs"]["tokens_in"]
            and mu.get("output_tokens", 0) == record["costs"]["tokens_out"]):
        ok("V5 usage totals (tokens_in/tokens_out)")
    else:
        bad("V5 usage totals", "export %r vs record in=%s out=%s"
            % (mu, record["costs"]["tokens_in"], record["costs"]["tokens_out"]))

    # V6 final assistant text == claim.text
    final_text = None
    for m in reversed(sample.get("messages", [])):
        if m.get("role") == "assistant":
            blocks = m.get("content")
            if isinstance(blocks, str):
                final_text = blocks
            else:
                texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
                final_text = "\n".join(texts)
            break
    claim_text = (record.get("claim") or {}).get("text")
    if claim_text is None:
        # claim is null in the record (dry-run / pending lane); the null
        # carries its reason code in the record itself — nothing to compare.
        ok("V6 claim text (record claim null; comparison vacuous)")
    elif final_text == claim_text:
        ok("V6 final assistant message == claim.text")
    else:
        bad("V6 claim text", "export terminal text %r != record claim.text %r"
            % (final_text, claim_text))

    # V7 summaries agree
    s0 = summaries[0] if summaries else {}
    if (s0.get("id") == sample.get("id") and s0.get("epoch") == sample.get("epoch")
            and s0.get("message_count") == len(sample.get("messages", []))):
        ok("V7 summaries consistent with sample")
    else:
        bad("V7 summaries", "summary %r vs sample id=%s epoch=%s message_count=%d"
            % ({k: s0.get(k) for k in ("id", "epoch", "message_count")},
               sample.get("id"), sample.get("epoch"), len(sample.get("messages", []))))

    # V8 determinism: re-export, byte-compare
    try:
        with tempfile.TemporaryDirectory() as td:
            re_path = os.path.join(td, "re.eval")
            export_eval.export(record_path, re_path)
            a = open(eval_path, "rb").read()
            b = open(re_path, "rb").read()
        if a == b:
            ok("V8 determinism (re-export byte-identical)")
        else:
            bad("V8 determinism", "re-export differs from shipped export "
                "(sha256 %s vs %s)" % (hashlib.sha256(a).hexdigest(),
                                       hashlib.sha256(b).hexdigest()))
    except export_eval.ExportError as e:
        bad("V8 determinism", "re-export refused: %s" % e)

    return failures


def main(argv):
    if len(argv) not in (1, 2):
        print(__doc__)
        return 2
    record_path = argv[0]
    if len(argv) == 2:
        eval_path = argv[1]
    else:
        eval_path = os.path.splitext(record_path)[0] + ".eval"
    print("verify_export: record=%s export=%s" % (record_path, eval_path))
    failures = verify(record_path, eval_path)
    if failures:
        print("VERIFY FAILED (%d):" % len(failures))
        for f in failures:
            print("  - %s" % f)
        return 1
    print("VERIFY OK: export is a faithful derivative of the canonical record")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
