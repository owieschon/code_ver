#!/usr/bin/env python3
"""derive.py — TrustLadder Stage 1 derived telemetry columns, per arm.

Derived columns: cost-per-verified-completion and
cost-per-claimed-completion, computed per arm at analysis time; per-run
raw stays in the record.

Definitions (deterministic, of record):
  claimed completion   record with claim != null and claim.claimed_done
                       == true
  verified completion  record with verdict != null and verdict
                       .m0_complete == true
  cost_per_claimed_completion(arm)  = sum(costs.dollars over arm runs)
                                      / n_claimed(arm)
  cost_per_verified_completion(arm) = sum(costs.dollars over arm runs)
                                      / n_verified(arm)
  Zero denominator -> null with an explicit note (never a silent 0).
  Below-floor strings ("<FLOOR") contribute 0 to sums — totals are
  LOWER BOUNDS — and are counted per field in below_floor_counts.
Per-mechanism latency/tokens/dollars are aggregated from
records/telemetry.jsonl, joined to records by run_id;
arm is taken from the record only (single source — events carry no arm
field, so the join cannot contradict the record).

REFUSALS (never compute over bad input):
  - any malformed telemetry event line (validated via
    telemetry.read_events -> telemetry_event.schema.json);
  - any record failing emit_record.validate_record;
  - any orphan telemetry event whose run_id has no record.

Demonstrated-red receipts (house discipline):
  receipts/derive_red_malformed_event.txt
  receipts/derive_red_orphan_event.txt
Green demonstration on synthetic events:
  receipts/derive_green_synthetic.txt (via demo_derive_synthetic.py)
"""

import glob
import json
import os
import sys

from trustladder.schema.emit_record import (BELOW_FLOOR_RE, DEFAULT_RECORDS_DIR,
                         validate_record)
from trustladder.schema.telemetry import DEFAULT_TELEMETRY_PATH, read_events

ARMS = ["L0", "L1", "SHAM", "L3"]
MECHANISMS = ["hook", "verifier", "judge"]


def _num(value, below_floor_counts, field):
    """Numeric value of a measure; below-floor strings count 0 (lower
    bound) and increment below_floor_counts[field]."""
    if isinstance(value, str) and BELOW_FLOOR_RE.match(value):
        below_floor_counts[field] = below_floor_counts.get(field, 0) + 1
        return 0
    return value


def load_records(records_dir=DEFAULT_RECORDS_DIR):
    """Load + re-validate every records/*.json. REFUSES on the first
    invalid record (consumers never aggregate unvalidated records)."""
    records = {}
    for path in sorted(glob.glob(os.path.join(records_dir, "*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            record = json.load(f)
        errors = validate_record(record)
        if errors:
            raise ValueError(
                "REFUSED: %s fails run-record validation:\n  - %s"
                % (path, "\n  - ".join(errors)))
        records[record["run_id"]] = record
    return records


def derive(records_dir=DEFAULT_RECORDS_DIR,
           telemetry_path=DEFAULT_TELEMETRY_PATH):
    """Compute per-arm derived columns + per-mechanism telemetry
    aggregates. Returns a deterministic dict (sorted keys downstream)."""
    records = load_records(records_dir)
    events = read_events(telemetry_path)

    orphans = sorted({e["run_id"] for e in events if e["run_id"] not in records})
    if orphans:
        raise ValueError(
            "REFUSED: telemetry events reference run_ids with no record in "
            "%s (cannot assign an arm): %s" % (records_dir, ", ".join(orphans)))

    out = {"records_dir": records_dir, "telemetry_path": telemetry_path,
           "arms": {}}
    for arm in ARMS:
        arm_records = [r for r in records.values() if r["arm"] == arm]
        bf = {}
        n_claimed = sum(
            1 for r in arm_records
            if r["claim"] is not None and r["claim"]["claimed_done"] is True)
        n_verified = sum(
            1 for r in arm_records
            if r["verdict"] is not None and r["verdict"]["m0_complete"] is True)
        total_dollars = sum(
            _num(r["costs"]["dollars"], bf, "costs.dollars")
            for r in arm_records)

        mech = {}
        arm_run_ids = {r["run_id"] for r in arm_records}
        for m in MECHANISMS:
            m_events = [e for e in events
                        if e["run_id"] in arm_run_ids and e["mechanism"] == m]
            latency = sum(_num(e["latency_ms"], bf, "latency_ms")
                          for e in m_events)
            mech[m] = {
                "n_events": len(m_events),
                "total_latency_ms": round(latency, 6),
                "mean_latency_ms": (round(latency / len(m_events), 6)
                                    if m_events else None),
                "total_tokens": sum(_num(e["tokens"], bf, "tokens")
                                    for e in m_events),
                "total_dollars": round(sum(_num(e["dollars"], bf, "dollars")
                                           for e in m_events), 6),
                "verdict_counts": {
                    v: sum(1 for e in m_events if e["verdict"] == v)
                    for v in ["allow", "block", "shadow_would_block",
                              "pass", "fail"]},
            }

        out["arms"][arm] = {
            "n_runs": len(arm_records),
            "n_claimed_completions": n_claimed,
            "n_verified_completions": n_verified,
            "total_dollars": round(total_dollars, 6),
            "cost_per_claimed_completion":
                round(total_dollars / n_claimed, 6) if n_claimed else None,
            "cost_per_verified_completion":
                round(total_dollars / n_verified, 6) if n_verified else None,
            "notes": ([] if n_claimed and n_verified else
                      [n for n, missing in
                       [("cost_per_claimed_completion null: zero claimed "
                         "completions in arm", not n_claimed),
                        ("cost_per_verified_completion null: zero verified "
                         "completions in arm", not n_verified)] if missing]),
            "per_mechanism": mech,
            "below_floor_counts": bf,
        }
    return out


def main(argv):
    records_dir = DEFAULT_RECORDS_DIR
    telemetry_path = DEFAULT_TELEMETRY_PATH
    out_path = None
    args = list(argv)
    while args:
        flag = args.pop(0)
        if flag == "--records-dir":
            records_dir = args.pop(0)
        elif flag == "--telemetry-path":
            telemetry_path = args.pop(0)
        elif flag == "--out":
            out_path = args.pop(0)
        else:
            sys.stderr.write(
                "usage: derive.py [--records-dir DIR] [--telemetry-path PATH]"
                " [--out FILE]\n")
            return 2
    try:
        result = derive(records_dir, telemetry_path)
    except ValueError as e:
        sys.stderr.write(str(e) + "\n")
        return 1
    text = json.dumps(result, indent=2, sort_keys=True)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
