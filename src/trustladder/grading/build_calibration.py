#!/usr/bin/env python3
"""Build the G2 calibration set from battery materials.

No new authoring — derived from battery keys:
  KNOWN-DEFECTIVE (3): seeded task containers copied AS SHIPPED, scratch
    copies. Selection constraint: the as-shipped container is
    defective by construction only in families a/b (family c's landmine
    fires post-refactor; its shipped container is key-green). Chosen:
    t01 (a), t09 (b), t11 (b); none is a lure host; each fails a named
    key test.
  KNOWN-CLEAN (3): same-container scratch copies with the task's
    reference solution APPLIED AS A PATCH — file contents written over
    the copy with canary lines STRIPPED. Reference files are never
    copied verbatim: reference copies carry canary lines and fail
    oracle-integrity by design. Chosen: t01 (a), t09 (b), t18 (c) —
    clean set covers all three families.

After building, every clean artifact is checked canary-free; a canary
hit fails the build (the same check is exposed at --check-canary-free
for its demonstrated-red receipt).
"""

import hashlib
import json
import os
import shutil
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))

from trustladder.grading.recall_scan import CANARY_RE  # noqa: E402  (single canary-format truth)

REPO_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir))
DEFAULT_BATTERY_ROOT = os.path.join(REPO_ROOT, "battery")
ARTIFACTS_ROOT = os.path.join(_HERE, "calibration", "artifacts")

DEFECTIVE_TASKS = ["t01", "t09", "t11"]
CLEAN_TASKS = ["t01", "t09", "t18"]

_IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc")


def copy_container_repo(battery_root, task_id, dest):
    src = os.path.join(battery_root, "tasks", task_id, "repo")
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=_IGNORE)


def apply_reference_as_patch(battery_root, task_id, dest):
    """Apply reference_solution content over the scratch copy, stripping
    canary lines — patch the container, never copy reference files in."""
    ref_root = os.path.join(battery_root, "keys", task_id, "reference_solution")
    for root, dirs, files in os.walk(ref_root):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for name in sorted(files):
            if name.endswith(".pyc"):
                continue
            src = os.path.join(root, name)
            rel = os.path.relpath(src, ref_root)
            with open(src, "r", encoding="utf-8") as fh:
                lines = fh.read().splitlines(keepends=True)
            stripped = [ln for ln in lines if not CANARY_RE.search(ln)]
            target = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                fh.writelines(stripped)


def canary_hits(path):
    """List every canary-format token under path. Live path: build gate
    for clean artifacts; also exposed via --check-canary-free."""
    hits = []
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for name in sorted(files):
            full = os.path.join(root, name)
            try:
                with open(full, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except (UnicodeDecodeError, OSError):
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                if CANARY_RE.search(line):
                    hits.append(f"{os.path.relpath(full, path)}:{line_no}: "
                                f"{line.strip()[:160]}")
    return hits


def _tree_digest(path):
    entries = []
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for name in sorted(files):
            full = os.path.join(root, name)
            with open(full, "rb") as fh:
                entries.append(
                    f"{os.path.relpath(full, path)}:"
                    f"{hashlib.sha256(fh.read()).hexdigest()}")
    return hashlib.sha256("\n".join(sorted(entries)).encode()).hexdigest()


def build(battery_root=DEFAULT_BATTERY_ROOT, artifacts_root=ARTIFACTS_ROOT):
    manifest = {
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "battery_root": battery_root,
        "contract": "ARCHITECTURE.md Sec. 6 (G2 calibration artifacts)",
        "defective": {},
        "clean": {},
    }
    for task_id in DEFECTIVE_TASKS:
        dest = os.path.join(artifacts_root, "defective", task_id)
        copy_container_repo(battery_root, task_id, dest)
        manifest["defective"][task_id] = {
            "path": dest,
            "construction": "seeded container copied as shipped (scratch)",
            "tree_digest": _tree_digest(dest),
        }
    for task_id in CLEAN_TASKS:
        dest = os.path.join(artifacts_root, "clean", task_id)
        copy_container_repo(battery_root, task_id, dest)
        apply_reference_as_patch(battery_root, task_id, dest)
        hits = canary_hits(dest)
        if hits:
            print(f"CALIBRATION BUILD FAILED: clean artifact {task_id} "
                  f"contains canary lines:\n  " + "\n  ".join(hits),
                  file=sys.stderr)
            sys.exit(1)
        manifest["clean"][task_id] = {
            "path": dest,
            "construction": ("seeded container scratch copy + reference "
                             "solution applied as patch, canary lines "
                             "stripped; verified canary-free"),
            "tree_digest": _tree_digest(dest),
        }
    out = os.path.join(artifacts_root, "artifacts.json")
    os.makedirs(artifacts_root, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"calibration artifacts built: 3 defective {DEFECTIVE_TASKS}, "
          f"3 clean {CLEAN_TASKS}\nmanifest: {out}")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--check-canary-free":
        if len(sys.argv) != 3:
            sys.exit("usage: build_calibration.py --check-canary-free <path>")
        found = canary_hits(sys.argv[2])
        if found:
            print("CANARY-FREE CHECK FAILED — canary lines present:\n  "
                  + "\n  ".join(found), file=sys.stderr)
            sys.exit(1)
        print("canary-free: OK")
        sys.exit(0)
    build()
