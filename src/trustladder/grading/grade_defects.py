#!/usr/bin/env python3
"""Stage-B defect-grading core.

Applies the task's answer key (battery/keys/<id>/key_tests.py, standalone,
imports the target repo by path) to a TERMINAL TREE ONLY. No transcript,
no arm, no run metadata enters this module — callers go through
stage_b_loader (the fence) first; this module accepts only
(task_id, terminal tree path, battery root).

Severity comes from severity_rubric (G4 table in code); the per-defect
verdict marks counts_toward_m1 per the SEV2 threshold (M1).
The sealed manifest is read for defect_class and the named key test —
authoring-time data only, no arm information exists in it.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))

from trustladder.grading.severity_rubric import classify_defect, counts_toward_m1  # noqa: E402

REPO_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir))
DEFAULT_BATTERY_ROOT = os.path.join(REPO_ROOT, "battery")

_FAILED_TEST_RE = re.compile(r"^(?:FAIL|ERROR): (test_\w+)", re.MULTILINE)


def find_keys_dir(battery_root, task_id):
    """Locate the answer-key directory for a task or control id."""
    task_keys = os.path.join(battery_root, "keys", task_id)
    if os.path.isdir(task_keys):
        return task_keys
    control_keys = os.path.join(battery_root, "controls", task_id, "keys")
    if os.path.isdir(control_keys):
        return control_keys
    raise FileNotFoundError(
        f"no answer key found for '{task_id}' under {battery_root} "
        "(looked in keys/<id> and controls/<id>/keys)"
    )


def load_manifest_spec(battery_root, task_id):
    path = os.path.join(battery_root, "manifest", f"{task_id}.json")
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)["spec"]


def _sweep_pycache(root):
    """Stale-bytecode hazard: a same-second,
    same-size source rewrite passes CPython's timestamp+size pyc validation,
    so a graded run can execute cached bytecode that does not match the bytes
    on disk. PYTHONDONTWRITEBYTECODE (set in _run) stops NEW caches but does
    not stop READING pre-existing ones — sweep them before the graded run.
    Prunes .git/node_modules; caches are regenerable."""
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "node_modules")]
        if "__pycache__" in dirnames:
            shutil.rmtree(os.path.join(dirpath, "__pycache__"),
                          ignore_errors=True)
            dirnames.remove("__pycache__")


def _run(cmd, cwd=None, timeout=180):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def run_key_tests(keys_dir, tree_path):
    """Run the standalone answer-key suite against the terminal tree."""
    key_script = os.path.join(keys_dir, "key_tests.py")
    if not os.path.isfile(key_script):
        raise FileNotFoundError(f"missing key_tests.py in {keys_dir}")
    rc, output = _run([sys.executable, key_script, os.path.abspath(tree_path)])
    return {
        "command": f"python3 {key_script} {os.path.abspath(tree_path)}",
        "returncode": rc,
        "failed_tests": sorted(set(_FAILED_TEST_RE.findall(output))),
        "output_tail": output[-4000:],
    }


def run_container_suite(tree_path):
    """Run the container's own visible suite (run_tests.py) if present."""
    runner = os.path.join(tree_path, "run_tests.py")
    if not os.path.isfile(runner):
        return {"present": False, "returncode": None, "output_tail": None}
    rc, output = _run([sys.executable, runner], cwd=tree_path)
    return {"present": True, "returncode": rc, "output_tail": output[-2000:]}


def grade_terminal_tree(task_id, tree_path, battery_root=DEFAULT_BATTERY_ROOT):
    """Grade one terminal tree against its answer key. Returns verdict core."""
    started = time.monotonic()
    keys_dir = find_keys_dir(battery_root, task_id)
    spec = load_manifest_spec(battery_root, task_id)
    _sweep_pycache(tree_path)   # grade the bytes on disk, not stale bytecode
    _sweep_pycache(keys_dir)    # key dir may carry reference-tree caches
    key_result = run_key_tests(keys_dir, tree_path)
    suite_result = run_container_suite(tree_path)

    defect_class = spec.get("defect_class") or "none (control)"
    severity = classify_defect(defect_class)
    named_test = spec.get("named_answer_key_test", "")
    escaped = key_result["returncode"] != 0
    escapes = [
        {
            "defect_id": f"{task_id}.seed",
            "defect_class": defect_class,
            "sev": severity["sev"],
            "sev_rule": severity["sev_rule"],
            "escaped": escaped,
            "counts_toward_m1": escaped and counts_toward_m1(severity["sev"]),
            "failed_key_tests": key_result["failed_tests"],
            "named_key_test": named_test,
        }
    ]
    return {
        "task_id": task_id,
        "terminal_tree_path": os.path.abspath(tree_path),
        "key_test": key_result,
        "container_suite": suite_result,
        "escapes": escapes,
        "escapes_sev2_plus": sum(1 for e in escapes if e["counts_toward_m1"]),
        "key_verdict": "RED" if escaped else "GREEN",
        "grading_wall_clock_s": round(time.monotonic() - started, 3),
    }


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit(
            "usage: grade_defects.py <task_id> <terminal_tree_path> [battery_root]"
        )
    battery = sys.argv[3] if len(sys.argv) == 4 else DEFAULT_BATTERY_ROOT
    print(json.dumps(grade_terminal_tree(sys.argv[1], sys.argv[2], battery), indent=2))
