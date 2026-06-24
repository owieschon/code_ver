#!/usr/bin/env python3
"""G2 calibration gate.

The grader must show RED on all 3 known-defective and GREEN on all 3
known-clean calibration artifacts BEFORE any experimental grading.
Grading runs through the SAME stage-B core (grade_defects) used for
experimental runs — the gate exercises the live instrument, not a copy.

Outcome:
  PASS -> writes grading/calibration/receipt.json with g2_gate="PASS",
          per-artifact results, instrument + key hashes (so grade_run
          can refuse if the instrument changed after calibration), and
          the calibration-set cost line item.
  FAIL -> receipt written with g2_gate="FAIL", exit 1. Per G2:
          halt, fix, regrade from zero.

grade_run.py consumes the receipt as a state-file gate and REFUSES to
grade experimentally without a passing, hash-current receipt.
"""

import hashlib
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))

from trustladder.grading.grade_defects import grade_terminal_tree, find_keys_dir  # noqa: E402
from trustladder.grading.build_calibration import (  # noqa: E402
    ARTIFACTS_ROOT,
    CLEAN_TASKS,
    DEFAULT_BATTERY_ROOT,
    DEFECTIVE_TASKS,
)

RECEIPT_PATH = os.path.join(_HERE, "calibration", "receipt.json")

INSTRUMENT_FILES = [
    "grade_defects.py",
    "severity_rubric.py",
    "stage_b_loader.py",
    "recall_scan.py",
    "known_fix_patterns.json",
    "grade_run.py",  # orchestrator computes verdict fields; pinned too
]


def _sha256_file(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def instrument_hashes():
    return {name: _sha256_file(os.path.join(_HERE, name))
            for name in INSTRUMENT_FILES}


def calibration_key_hashes(battery_root, tasks=None):
    """Hash the answer-key script of each calibration task. `tasks` defaults to
    the standard calibration set; the gate passes the receipt's own task set so
    it verifies exactly the keys that were calibrated against."""
    if tasks is None:
        tasks = sorted(set(DEFECTIVE_TASKS + CLEAN_TASKS))
    hashes = {}
    for task_id in sorted(set(tasks)):
        key_script = os.path.join(find_keys_dir(battery_root, task_id),
                                  "key_tests.py")
        hashes[task_id] = _sha256_file(key_script)
    return hashes


def run_gate(artifacts_root=ARTIFACTS_ROOT,
             battery_root=DEFAULT_BATTERY_ROOT,
             receipt_path=RECEIPT_PATH,
             defective_tasks=None,
             clean_tasks=None):
    if defective_tasks is None:
        defective_tasks = DEFECTIVE_TASKS
    if clean_tasks is None:
        clean_tasks = CLEAN_TASKS
    started = time.monotonic()
    checks = []
    expectations = (
        [("defective", t, "RED") for t in defective_tasks]
        + [("clean", t, "GREEN") for t in clean_tasks]
    )
    for kind, task_id, expected in expectations:
        artifact = os.path.join(artifacts_root, kind, task_id)
        verdict = grade_terminal_tree(task_id, artifact, battery_root)
        observed = verdict["key_verdict"]
        checks.append({
            "kind": kind,
            "task_id": task_id,
            "artifact": artifact,
            "expected": expected,
            "observed": observed,
            "ok": observed == expected,
            "failed_key_tests": verdict["key_test"]["failed_tests"],
            "key_output_tail": verdict["key_test"]["output_tail"][-1200:],
        })
    passed = all(c["ok"] for c in checks)
    receipt = {
        "g2_gate": "PASS" if passed else "FAIL",
        # Additive boolean consumed by analysis/analysis.py VG4
        # (_check_g2_receipt expects g2_pass is True); same fact as g2_gate.
        "g2_pass": passed,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "contract": ("prereg Sec. 6 G2: red on all 3 known-defective, green "
                     "on all 3 known-clean, before any experimental grading"),
        "artifacts_root": artifacts_root,
        "checks": checks,
        "instrument_hashes": instrument_hashes(),
        "calibration_key_hashes": calibration_key_hashes(
            battery_root, sorted(set(defective_tasks + clean_tasks))),
        "calibration_cost": {
            "wall_clock_s": round(time.monotonic() - started, 3),
            "note": "calibration-set cost logged as its own line item "
                    "(prereg 7A)",
        },
    }
    os.makedirs(os.path.dirname(receipt_path), exist_ok=True)
    with open(receipt_path, "w", encoding="utf-8") as fh:
        json.dump(receipt, fh, indent=2)
    for c in checks:
        print(f"  [{'ok ' if c['ok'] else 'BAD'}] {c['kind']}/{c['task_id']}: "
              f"expected {c['expected']}, observed {c['observed']}")
    if passed:
        print(f"G2 CALIBRATION GATE: PASS — receipt at {receipt_path}")
        return 0
    print("G2 CALIBRATION GATE: FAIL — halt, fix, regrade from zero "
          f"(prereg Sec. 6 G2). Receipt at {receipt_path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    args = sys.argv[1:]
    artifacts = ARTIFACTS_ROOT
    receipt = RECEIPT_PATH
    battery = DEFAULT_BATTERY_ROOT
    while args:
        flag = args.pop(0)
        if flag == "--artifacts-root":
            artifacts = args.pop(0)
        elif flag == "--receipt":
            receipt = args.pop(0)
        elif flag == "--battery-root":
            battery = args.pop(0)
        else:
            sys.exit(f"unknown flag {flag}; usage: grade_calibration.py "
                     "[--artifacts-root P] [--receipt P] [--battery-root P]")
    sys.exit(run_gate(artifacts, battery, receipt))
