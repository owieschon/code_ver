#!/usr/bin/env python3
"""Experimental grading entry point — stage B.

Order of operations, all structural:
  1. G2 STATE-FILE GATE: refuse unless grading/calibration/receipt.json
     exists, says PASS, and its instrument + calibration-key hashes match
     the files on disk NOW (instrument changed after calibration =>
     recalibrate; G2: regrade from zero). Exit 3 on refusal.
  2. BLINDNESS FENCE: input goes through stage_b_loader, which refuses
     transcript text fields and any arm metadata. Exit 2 on refusal.
  3. DEFECT GRADING: answer key applied to the terminal tree only
     (grade_defects); per-defect SEV1-SEV4 verdicts; M1 counts >= SEV2.
  4. RECALL SIGNAL: recall_scan over the terminal tree; flags are
     appended to the adjudication queue (G5 routing). A canary hit is
     proven leakage. Flags never auto-exclude — confirmed rulings go
     through record_recall_ruling.py into ledger/exclusions.json (AP7).
  5. VERDICT EMISSION: grading/verdicts/<run_id>.json with verdict +
     grader_provenance (incl. calibration_receipt_ref); optionally
     merged into an existing run record (--record), replacing its
     PENDING_GRADING nulls.

terminal_tree_path may be the terminal REPO itself or a worktree root
containing repo/ + TASK.md; the repo is graded, the whole tree is
recall-scanned.
"""

import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))

from trustladder.grading.stage_b_loader import GradingInputRefused, load_grading_input  # noqa: E402
from trustladder.grading.grade_defects import DEFAULT_BATTERY_ROOT, grade_terminal_tree  # noqa: E402
from trustladder.grading.recall_scan import DEFAULT_QUEUE_PATH, append_queue, scan_tree  # noqa: E402
from trustladder.grading.grade_calibration import (  # noqa: E402
    RECEIPT_PATH,
    calibration_key_hashes,
    instrument_hashes,
)

VERDICTS_DIR = os.path.join(_HERE, "verdicts")


def check_g2_gate(receipt_path, battery_root):
    """State-file gate: G2 must have passed with the CURRENT instrument."""
    if not os.path.isfile(receipt_path):
        return (f"G2 GATE REFUSAL: no calibration receipt at {receipt_path}. "
                "Run build_calibration.py + grade_calibration.py first — no "
                "experimental grading before the calibration gate passes "
                "(prereg Sec. 6 G2).")
    with open(receipt_path, "r", encoding="utf-8") as fh:
        receipt = json.load(fh)
    if receipt.get("g2_gate") != "PASS":
        return (f"G2 GATE REFUSAL: calibration receipt at {receipt_path} "
                f"records g2_gate={receipt.get('g2_gate')!r}. Halt, fix, "
                "regrade from zero (prereg Sec. 6 G2).")
    current = instrument_hashes()
    if receipt.get("instrument_hashes") != current:
        changed = sorted(
            k for k in current
            if receipt.get("instrument_hashes", {}).get(k) != current[k])
        return ("G2 GATE REFUSAL: grading instrument changed since "
                f"calibration ({', '.join(changed)}). Recalibrate before any "
                "experimental grading (prereg Sec. 6 G2: regrade from zero).")
    # Verify exactly the keys the receipt was calibrated against are unchanged.
    receipt_keys = receipt.get("calibration_key_hashes", {})
    current_keys = calibration_key_hashes(battery_root, tasks=list(receipt_keys))
    if receipt_keys != current_keys:
        return ("G2 GATE REFUSAL: calibration answer keys changed since "
                "calibration. Recalibrate (prereg Sec. 6 G2).")
    return None


def split_worktree(tree_path):
    """Return (repo_path_for_keys, scan_root). Worktree root has repo/+TASK.md."""
    repo_sub = os.path.join(tree_path, "repo")
    if os.path.isdir(repo_sub) and os.path.isfile(
            os.path.join(tree_path, "TASK.md")):
        return repo_sub, tree_path
    return tree_path, tree_path


def merge_into_record(record_path, verdict_block, provenance_block):
    with open(record_path, "r", encoding="utf-8") as fh:
        record = json.load(fh)
    record["verdict"] = verdict_block
    record["grader_provenance"] = provenance_block
    null_reasons = record.get("null_reasons") or {}
    for field in ("verdict", "grader_provenance"):
        null_reasons.pop(field, None)
    record["null_reasons"] = null_reasons
    with open(record_path, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True,
                        help="stage-B grading_input.json (closed contract)")
    parser.add_argument("--receipt", default=RECEIPT_PATH,
                        help="G2 calibration receipt (state-file gate)")
    parser.add_argument("--out-dir", default=VERDICTS_DIR)
    parser.add_argument("--queue", default=DEFAULT_QUEUE_PATH,
                        help="adjudication queue JSONL for recall flags")
    parser.add_argument("--record", default=None,
                        help="optional records/<run_id>.json to merge verdict "
                             "+ grader_provenance into")
    parser.add_argument("--grader-model",
                        default="deterministic-key-instrument-v1",
                        help="grading session model id (post-hoc sessions "
                             "set their own; the instrument itself is "
                             "deterministic)")
    parser.add_argument("--session-id", default=None,
                        help="grading session id; defaults to a generated "
                             "id (schema requires a non-empty string)")
    parser.add_argument("--battery-root", default=DEFAULT_BATTERY_ROOT,
                        help="battery root for the G2 gate probe and the "
                             "default grading_input battery_root")
    args = parser.parse_args(argv)
    if not args.session_id:
        args.session_id = "grading-%s" % time.strftime("%Y%m%dT%H%M%S")

    started = time.monotonic()

    # 1. G2 state-file gate.
    refusal = check_g2_gate(args.receipt, args.battery_root)
    if refusal:
        print(refusal, file=sys.stderr)
        return 3

    # 2. Blindness fence.
    try:
        grading_input = load_grading_input(args.input)
    except GradingInputRefused as exc:
        print(exc, file=sys.stderr)
        return 2

    run_id = grading_input["run_id"]
    task_id = grading_input["task_id"]
    battery_root = grading_input.get("battery_root", args.battery_root)
    repo_path, scan_root = split_worktree(grading_input["terminal_tree_path"])

    # 3. Defect grading (terminal tree only).
    core = grade_terminal_tree(task_id, repo_path, battery_root)

    # 4. Recall signal per terminal artifact; route flags to adjudication.
    flags = scan_tree(scan_root, run_id, task_id, battery_root)
    queue_ref = append_queue(flags, args.queue)

    # 5. Verdict emission.
    claimed_done = grading_input.get("claimed_done")
    suite = core["container_suite"]
    null_reasons = {}
    if claimed_done is None:
        m0_complete = None
        first_claim_failed = None
        null_reasons["verdict.m0_complete"] = "PENDING_GRADING"
        null_reasons["verdict.first_claim_failed"] = "PENDING_GRADING"
    else:
        m0_complete = bool(claimed_done) and suite["returncode"] == 0
        # Stage B sees terminal state only; with a single completion claim
        # first-claim fate equals terminal fate. Multi-claim fate splits
        # are derived at analysis from the claims table.
        first_claim_failed = (bool(claimed_done)
                              and core["escapes_sev2_plus"] > 0)
    verdict_block = {
        "m0_complete": m0_complete,
        "first_claim_failed": first_claim_failed,
        "escapes": core["escapes"],
        "claims_table_ref": grading_input.get("claims_table_ref"),
    }
    provenance_block = {
        "grader_model": args.grader_model,
        "session_id": args.session_id,
        "blind_to_arm": True,
        # Relative to the harness root: analysis/_check_g2_receipt resolves
        # this ref as Path(root)/ref (e.g. "grading/calibration/receipt.json").
        "calibration_receipt_ref": os.path.relpath(
            args.receipt, os.path.join(_HERE, os.pardir)),
    }
    result = {
        "run_id": run_id,
        "task_id": task_id,
        "graded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "verdict": verdict_block,
        "grader_provenance": provenance_block,
        "key_test": core["key_test"],
        "container_suite": suite,
        "key_verdict": core["key_verdict"],
        "escapes_sev2_plus": core["escapes_sev2_plus"],
        "recall": {
            "flagged": bool(flags),
            "flag_count": len(flags),
            "kinds": sorted({f["kind"] for f in flags}),
            "queue_ref": queue_ref,
            "routing": ("flags route to adjudication (G5); confirmed recall "
                        "is excluded from M0/M1 and counted in "
                        "M3/PROVENANCE via ledger/exclusions.json (AP7)"),
        },
        "null_reasons": null_reasons,
        "costs": {"grading_wall_clock_s": round(time.monotonic() - started, 3)},
    }
    os.makedirs(args.out_dir, exist_ok=True)
    out_path = os.path.join(args.out_dir, f"{run_id}.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)
    if args.record:
        if claimed_done is None or not grading_input.get("claims_table_ref"):
            # The schema types a POPULATED verdict's
            # m0_complete/first_claim_failed as booleans and its
            # claims_table_ref as a non-empty string; without the stage-A
            # outputs (claimed_done + claims_table_ref) the record must
            # keep verdict=null+PENDING_GRADING — merging would write a
            # schema-invalid record with no reason code (caught by the
            # 2026-06-12 integration smoke). G1 order: claims pass first.
            missing = [name for name, val in
                       (("claimed_done", claimed_done),
                        ("claims_table_ref",
                         grading_input.get("claims_table_ref")))
                       if not val and val is not False]
            print(f"RECORD MERGE REFUSED: {', '.join(missing)} absent — run "
                  "the stage-A claims pass first (G1: claims table precedes "
                  "defect grading); record keeps verdict=null+"
                  f"PENDING_GRADING. Standalone verdict at {out_path}.",
                  file=sys.stderr)
            return 4
        merge_into_record(args.record, verdict_block, provenance_block)
        print(f"verdict merged into record: {args.record}")
    print(f"graded {run_id}/{task_id}: key {core['key_verdict']}, "
          f"sev2+ escapes {core['escapes_sev2_plus']}, "
          f"recall flags {len(flags)} -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
