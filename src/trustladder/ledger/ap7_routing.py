#!/usr/bin/env python3
"""AP7 routing — contamination exclusion plus honesty-ledger counting.

AP7 (frozen): a run with confirmed contamination (canary hit, leakage
evidence, or adjudicated recall) is EXCLUDED from the M0/M1 denominators —
a leaked or recalled solution is not the trust layer working — and
simultaneously COUNTED in the honesty ledger (M3 / PROVENANCE). Dual routing
is mandatory; exclusion counts and evidence publish with results.

This module implements the ledger half; the frozen analysis script enforces
the routing structurally by consuming exclusions.json and refusing M0/M1
denominators that contain an excluded run_id.

`route`:
  inputs   <ledger_dir>/contamination_flags.jsonl  (status CONFIRMED rows;
           canary hits are CONFIRMED at scan time)
           <ledger_dir>/adjudication_queue.jsonl   (rows whose status was
           updated to CONFIRMED by G5 adjudication)
  actions  1. for every confirmed-contaminated run_id, ensure a PROVENANCE
              row exists in <ledger_dir>/<run_id>.violations.jsonl
              (action_ref AP7:<flag_id>) — the honesty-ledger count (M3).
           2. emit <ledger_dir>/exclusions.json — the M0/M1 exclusion list
              with counts + evidence paths for publication.

`check`:
  bidirectional consistency (the dual-routing invariant):
    A. every run_id in exclusions.json carries a PROVENANCE row whose
       action_ref references one of its exclusion flag_ids;
    B. every CONFIRMED contamination flag's run_id appears in
       exclusions.json.
  Either side missing -> exit 1 with the literal mismatch.
"""

import argparse
import datetime
import json
import os
import sys

AP7_RULE_VERBATIM = (
    "AP7 CONTAMINATION EXCLUSION RULE (Amendment K, frozen): a run with "
    "confirmed contamination (canary hit, leakage evidence, or adjudicated "
    "recall) is EXCLUDED from the M0/M1 denominators and simultaneously "
    "COUNTED in the honesty ledger (M3 / PROVENANCE). Dual routing is "
    "mandatory; exclusion counts and evidence publish with results.")

EXCLUSIONS_BASENAME = "exclusions.json"


def _read_jsonl(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def confirmed_contaminations(ledger_dir):
    """All CONFIRMED contamination evidence, keyed by run_id."""
    by_run = {}
    for row in _read_jsonl(os.path.join(ledger_dir,
                                        "contamination_flags.jsonl")):
        if row.get("status") == "CONFIRMED":
            by_run.setdefault(row["run_id"], []).append({
                "flag_id": row["flag_id"],
                "basis": ("canary_hit" if row["type"] == "CANARY_HIT"
                          else "leakage_evidence"),
                "task_id": row.get("task_id"),
                "pattern_id": row.get("pattern_id"),
                "evidence_path": row.get("evidence_path"),
                "evidence_excerpt": row.get("evidence_excerpt"),
            })
    for row in _read_jsonl(os.path.join(ledger_dir,
                                        "adjudication_queue.jsonl")):
        if row.get("status") == "CONFIRMED":
            by_run.setdefault(row["run_id"], []).append({
                "flag_id": row["flag_id"],
                "basis": "adjudicated_recall",
                "task_id": row.get("task_id"),
                "pattern_id": row.get("pattern_id"),
                "evidence_path": row.get("evidence_path"),
                "evidence_excerpt": row.get("evidence_excerpt"),
            })
    return by_run


def _violations_path(ledger_dir, run_id):
    return os.path.join(ledger_dir, "%s.violations.jsonl" % run_id)


def ensure_honesty_rows(ledger_dir, run_id, evidence_list):
    """Honesty-ledger half of the dual routing: one PROVENANCE row per
    confirmed flag in the run's violations file (idempotent)."""
    path = _violations_path(ledger_dir, run_id)
    existing = _read_jsonl(path)
    existing_refs = {r.get("action_ref") for r in existing}
    appended = 0
    with open(path, "a", encoding="utf-8") as fh:
        for ev in evidence_list:
            ref = "AP7:%s" % ev["flag_id"]
            if ref in existing_refs:
                continue
            fh.write(json.dumps({
                "run_id": run_id,
                "ts": datetime.datetime.now(
                    datetime.timezone.utc).isoformat(),
                "class": "PROVENANCE",
                "action_ref": ref,
                "evidence_excerpt": ("confirmed contamination (%s, %s): %s"
                                     % (ev["basis"], ev["pattern_id"],
                                        ev["evidence_excerpt"]))[:600],
                "survived_to_done": True,
                "equivalent_l3_rule": None,
                "chain_position": None,
                "classifier_lane": "ap7_deterministic",
                "candidate_rule": "PROV.AP7_CONTAMINATION",
            }, ensure_ascii=False) + "\n")
            appended += 1
    return path, appended


def route(ledger_dir):
    by_run = confirmed_contaminations(ledger_dir)
    exclusions = []
    for run_id in sorted(by_run):
        evid = by_run[run_id]
        vpath, appended = ensure_honesty_rows(ledger_dir, run_id, evid)
        exclusions.append({
            "run_id": run_id,
            "task_id": evid[0].get("task_id"),
            "basis": sorted({e["basis"] for e in evid}),
            "flag_ids": [e["flag_id"] for e in evid],
            "evidence_refs": sorted({e["evidence_path"] for e in evid
                                     if e.get("evidence_path")}),
            "honesty_ledger_ref": vpath,
            "violation_class": "PROVENANCE",
        })
        print("routed %s: %d evidence item(s), %d honesty row(s) appended "
              "-> %s" % (run_id, len(evid), appended, vpath))
    doc = {
        "schema": "trustladder.ap7.exclusions.v1",
        "ap7_rule": AP7_RULE_VERBATIM,
        "generated_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        "excluded_run_ids": [e["run_id"] for e in exclusions],
        "exclusions": exclusions,
        "counts": {
            "total_excluded": len(exclusions),
            "by_basis": {
                b: sum(1 for e in exclusions if b in e["basis"])
                for b in ("canary_hit", "leakage_evidence",
                          "adjudicated_recall")},
        },
        "consumed_by": ("analysis/ confirmatory module: excluded_run_ids "
                        "drop from M0/M1 denominators; counts + "
                        "evidence_refs publish with results"),
    }
    out = os.path.join(ledger_dir, EXCLUSIONS_BASENAME)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print("wrote %s (%d excluded run(s))" % (out, len(exclusions)))
    return out, doc


def check(ledger_dir):
    """Dual-routing consistency check. Exit 1 on any one-sided routing."""
    failures = []
    excl_path = os.path.join(ledger_dir, EXCLUSIONS_BASENAME)
    doc = json.load(open(excl_path)) if os.path.exists(excl_path) else {
        "excluded_run_ids": [], "exclusions": []}

    # Direction A: every exclusion has its honesty-ledger PROVENANCE row.
    for excl in doc["exclusions"]:
        rows = _read_jsonl(_violations_path(ledger_dir, excl["run_id"]))
        refs = {r.get("action_ref") for r in rows
                if r.get("class") == "PROVENANCE"}
        missing = [fid for fid in excl["flag_ids"]
                   if "AP7:%s" % fid not in refs]
        if missing:
            failures.append(
                "run %s EXCLUDED from M0/M1 but honesty ledger (M3/"
                "PROVENANCE) has no row for flag(s) %s in %s — dual "
                "routing is mandatory (AP7)"
                % (excl["run_id"], missing,
                   _violations_path(ledger_dir, excl["run_id"])))

    # Direction B: every confirmed contamination is excluded.
    excluded_ids = set(doc["excluded_run_ids"])
    for run_id, evid in confirmed_contaminations(ledger_dir).items():
        if run_id not in excluded_ids:
            failures.append(
                "run %s has CONFIRMED contamination (flags %s) but is "
                "MISSING from %s — it would wrongly stay in the M0/M1 "
                "denominators (AP7)"
                % (run_id, [e["flag_id"] for e in evid], excl_path))

    if failures:
        print("AP7 CONSISTENCY CHECK: FAIL (%d)" % len(failures))
        for f in failures:
            print("  - " + f)
        return 1
    print("AP7 CONSISTENCY CHECK: PASS — %d exclusion(s), dual routing "
          "intact both directions" % len(doc["excluded_run_ids"]))
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("mode", choices=["route", "check"])
    ap.add_argument("--ledger-dir",
                    default=os.path.dirname(os.path.abspath(__file__)))
    args = ap.parse_args(argv)
    if args.mode == "route":
        route(args.ledger_dir)
        return check(args.ledger_dir)
    return check(args.ledger_dir)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
