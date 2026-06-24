#!/usr/bin/env python3
"""Adjudication-packet generator.

Every disagreement reaches the adjudicator as ONE pre-assembled file —
the adjudicator never assembles evidence. Packet contents (per the G5
adjudication protocol):
  1. TASK SPEC        — the task's TASK.md, verbatim.
  2. DIFF             — seeded container repo -> disputed terminal tree.
  3. GRADER OUTPUTS   — both graders' verdicts side by side, plus a
                        field-level disagreement table.
  4. ANSWER-KEY TEST  — captured RED/GREEN: pinned red-vs-seeded and
                        green-vs-reference outputs from the task's
                        provenance pins, PLUS a live run of the key
                        against the disputed terminal tree.

Output: packets/<run_id>_<issue>.md (one file per disagreement).
Input: a disagreement JSON:
  {run_id, task_id, issue, terminal_tree_path,
   grader_a: {name, verdict_ref|verdict}, grader_b: {...}, notes?}
Missing evidence sources REFUSE packet generation — an incomplete
packet would silently violate "the adjudicator never assembles
evidence" (demonstrated red in receipts).
"""

import json
import os
import subprocess
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir))
DEFAULT_BATTERY_ROOT = os.path.join(REPO_ROOT, "battery")

from trustladder.grading.grade_defects import find_keys_dir, run_key_tests  # noqa: E402

G5_AI_USE_POLICY = (
    "AI-use policy (prereg Sec. 6 G5, pre-stated): the adjudicator MAY use "
    "AI tools for comprehension and evidence-gathering (summarizing diffs, "
    "explaining code, locating evidence). The adjudicator may NOT put the "
    "verdict question to a model; rulings are his own, recorded with a 1-2 "
    "line rationale in his own words citing specific packet evidence."
)


class PacketInputsMissing(Exception):
    pass


def _task_paths(battery_root, task_id):
    task_dir = os.path.join(battery_root, "tasks", task_id)
    if not os.path.isdir(task_dir):
        task_dir = os.path.join(battery_root, "controls", task_id, "task")
    return (os.path.join(task_dir, "TASK.md"), os.path.join(task_dir, "repo"))


def _load_verdict(grader, label):
    if "verdict" in grader:
        return grader["verdict"]
    ref = grader.get("verdict_ref")
    if not ref or not os.path.isfile(ref):
        raise PacketInputsMissing(
            f"PACKET REFUSED: grader {label} ({grader.get('name')}) has no "
            f"inline verdict and verdict_ref {ref!r} is unreadable — the "
            "adjudicator never assembles evidence (G5); fix the "
            "disagreement record."
        )
    with open(ref, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _diff(seeded_repo, terminal_tree):
    proc = subprocess.run(
        ["diff", "-ruN", "-x", ".git", "-x", "__pycache__", "-x", "*.pyc",
         seeded_repo, terminal_tree],
        capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        raise PacketInputsMissing(
            f"PACKET REFUSED: diff failed ({proc.stderr.strip()[:200]})")
    return proc.stdout or "(no differences)"


def _disagreement_table(va, vb, name_a, name_b):
    keys = sorted(set(va) | set(vb))
    rows = [f"| field | {name_a} | {name_b} | status |",
            "|---|---|---|---|"]
    for key in keys:
        a = json.dumps(va.get(key), sort_keys=True, default=str)
        b = json.dumps(vb.get(key), sort_keys=True, default=str)
        status = "MATCH" if a == b else "**DIFFER**"
        rows.append(f"| {key} | {a[:120]} | {b[:120]} | {status} |")
    return "\n".join(rows)


def generate_packet(disagreement_path, out_dir=_HERE,
                    battery_root=DEFAULT_BATTERY_ROOT):
    with open(disagreement_path, "r", encoding="utf-8") as fh:
        dis = json.load(fh)
    for field in ("run_id", "task_id", "issue", "terminal_tree_path",
                  "grader_a", "grader_b"):
        if field not in dis:
            raise PacketInputsMissing(
                f"PACKET REFUSED: disagreement record missing '{field}'.")
    run_id, task_id, issue = dis["run_id"], dis["task_id"], dis["issue"]
    terminal = dis["terminal_tree_path"]
    if not os.path.isdir(terminal):
        raise PacketInputsMissing(
            f"PACKET REFUSED: terminal tree '{terminal}' is not a directory.")
    task_md_path, seeded_repo = _task_paths(battery_root, task_id)
    if not os.path.isfile(task_md_path):
        raise PacketInputsMissing(
            f"PACKET REFUSED: no TASK.md for '{task_id}' "
            f"(looked at {task_md_path}).")
    keys_dir = find_keys_dir(battery_root, task_id)  # raises if missing

    with open(task_md_path, "r", encoding="utf-8") as fh:
        task_md = fh.read()
    verdict_a = _load_verdict(dis["grader_a"], "A")
    verdict_b = _load_verdict(dis["grader_b"], "B")
    name_a = dis["grader_a"].get("name", "grader A")
    name_b = dis["grader_b"].get("name", "grader B")
    diff_text = _diff(seeded_repo, terminal)

    pins_path = os.path.join(keys_dir, "provenance_pins.json")
    pinned_red = pinned_green = "(provenance pins unavailable)"
    if os.path.isfile(pins_path):
        with open(pins_path, "r", encoding="utf-8") as fh:
            pins = json.load(fh)
        outputs = pins.get("f2p_outputs", {})
        pinned_red = outputs.get("key_seeded", pinned_red)
        pinned_green = outputs.get("key_reference", pinned_green)
    live = run_key_tests(keys_dir, terminal)

    packet = f"""# ADJUDICATION PACKET — {run_id} / {task_id} / {issue}

Generated: {time.strftime('%Y-%m-%dT%H:%M:%S')} · Contract: prereg Sec. 6
G5 (pre-assembled packet; the adjudicator never assembles evidence) ·
ARCHITECTURE.md Sec. 5 (packets interface). This packet carries NO arm
label and no transcript prose.

> {G5_AI_USE_POLICY}

Disagreement: **{name_a}** vs **{name_b}**{(' — ' + dis['notes']) if dis.get('notes') else ''}

---

## 1. TASK SPEC (verbatim TASK.md)

{task_md}

---

## 2. DIFF — seeded container repo → disputed terminal tree

`diff -ruN -x .git -x __pycache__ {seeded_repo} {terminal}`

```diff
{diff_text}
```

---

## 3. GRADER OUTPUTS, SIDE BY SIDE

### Field-level disagreement table

{_disagreement_table(verdict_a, verdict_b, name_a, name_b)}

### {name_a} — full output

```json
{json.dumps(verdict_a, indent=2)}
```

### {name_b} — full output

```json
{json.dumps(verdict_b, indent=2)}
```

---

## 4. ANSWER-KEY TEST WITH CAPTURED RED/GREEN OUTPUT

The grading instrument is the task's standalone answer key
(`{os.path.relpath(os.path.join(keys_dir, 'key_tests.py'), REPO_ROOT)}`).
Its discriminance is pinned at authoring (A2/F2P) and reproduced here.

### Pinned RED — key vs seeded container (demonstrated at authoring)

```
{pinned_red}
```

### Pinned GREEN — key vs reference solution (demonstrated at authoring)

```
{pinned_green}
```

### LIVE — key vs THIS disputed terminal tree (captured at packet build)

`{live['command']}` → returncode {live['returncode']}

```
{live['output_tail']}
```
"""
    os.makedirs(out_dir, exist_ok=True)
    safe_issue = "".join(c if c.isalnum() or c in "-_" else "-" for c in issue)
    out_path = os.path.join(out_dir, f"{run_id}_{safe_issue}.md")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(packet)
    return out_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disagreement", required=True,
                        help="disagreement JSON record")
    parser.add_argument("--out-dir", default=_HERE)
    parser.add_argument("--battery-root", default=DEFAULT_BATTERY_ROOT)
    args = parser.parse_args()
    try:
        path = generate_packet(args.disagreement, args.out_dir,
                               args.battery_root)
    except (PacketInputsMissing, FileNotFoundError) as exc:
        print(exc, file=sys.stderr)
        sys.exit(2)
    print(f"packet: {path}")
