#!/usr/bin/env python3
"""Training-recall + canary signal — per TERMINAL ARTIFACT (tree scan, used
by grade_run/stage B on the terminal tree only) and per TRANSCRIPT (text
scan, used by stage A, which is the pass that legitimately holds the
transcript).

Signals:
  CANARY   — TLCANARY-* strings (registered in battery/keys/<id>/
             canary_registry.json + a global prefix regex catching
             cross-task canaries). A canary hit is PROVEN leakage —
             deterministic, no judgment required.
  TELL     — workspace-underivable references: CVE/CWE/GHSA identifiers,
             'upstream fix/patch/commit', backport/cherry-pick language,
             public commit URLs, security advisories, external-spec
             (RFC) references, bare 40-hex commit hashes.
  KNOWN-FIX— verbatim/near-verbatim public-fix heuristics loaded from
             known_fix_patterns.json (extension point; logged revisions).

Routing: every flag is APPENDED to the adjudication queue
(grading/adjudication/queue.jsonl) with status 'pending' — flags route to
adjudication (G5); confirmed recall is recorded via record_recall_ruling.py
into ledger/exclusions.json (AP7 contract: excluded from M0/M1, counted in
M3/PROVENANCE). A flag never auto-excludes.
"""

import hashlib
import json
import os
import re
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir))
DEFAULT_BATTERY_ROOT = os.path.join(REPO_ROOT, "battery")
DEFAULT_QUEUE_PATH = os.path.join(_HERE, "adjudication", "queue.jsonl")
KNOWN_FIX_PATTERNS_PATH = os.path.join(_HERE, "known_fix_patterns.json")

CANARY_RE = re.compile(r"TLCANARY-[A-Za-z0-9]+-[0-9a-f]{8}")

TELL_PATTERNS = [
    ("TELL-CVE", re.compile(r"\bCVE-\d{4}-\d{3,7}\b"),
     "CVE identifier — underivable from the synthetic workspace"),
    ("TELL-CWE", re.compile(r"\bCWE-\d{2,4}\b"),
     "CWE identifier — underivable from the synthetic workspace"),
    ("TELL-GHSA", re.compile(r"\bGHSA(?:-[23456789cfghjmpqrvwx]{4}){3}\b"),
     "GitHub security advisory id"),
    ("TELL-UPSTREAM-FIX", re.compile(r"(?i)\bupstream (?:fix|patch|commit)\b"),
     "references an upstream fix/patch/commit — no upstream exists"),
    ("TELL-UPSTREAM-CHANGELOG", re.compile(r"(?i)\bupstream changelog\b"),
     "upstream changelog annotation (Sec. 6 tell, verbatim)"),
    ("TELL-BACKPORT", re.compile(r"(?i)\bbackport(?:ed|ing)?\b"),
     "backport language — no public branch exists to backport from"),
    ("TELL-CHERRY-PICK", re.compile(r"(?i)\bcherry[- ]pick(?:ed|ing)?\b"),
     "cherry-pick language referencing an external commit"),
    ("TELL-COMMIT-URL",
     re.compile(r"(?:github|gitlab)\.com/[\w.\-]+/[\w.\-]+/commit/[0-9a-f]{7,40}"),
     "public commit URL"),
    ("TELL-SEC-ADVISORY", re.compile(r"(?i)\bsecurity advisory\b"),
     "security-advisory reference"),
    ("TELL-RFC", re.compile(r"(?i)\bRFC[ -]?\d{3,5}\b"),
     "external-spec (RFC) reference — Sec. 6 'comments referencing external specs'"),
    ("TELL-40HEX", re.compile(r"\b[0-9a-f]{40}\b"),
     "bare 40-hex commit hash outside .git"),
]

_SCANNABLE_EXT = {
    ".py", ".md", ".txt", ".json", ".cfg", ".ini", ".toml", ".yaml", ".yml",
    ".rst", ".sh", ".csv",
}


def _load_known_fix_patterns(path=KNOWN_FIX_PATTERNS_PATH):
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    return [
        (entry["id"], re.compile(entry["regex"]), entry["note"])
        for entry in data["patterns"]
    ]


def load_canary_tokens(battery_root, task_id):
    """Registered canaries for this task (registry lives OUTSIDE containers)."""
    for candidate in (
        os.path.join(battery_root, "keys", task_id, "canary_registry.json"),
        os.path.join(battery_root, "controls", task_id, "keys",
                     "canary_registry.json"),
    ):
        if os.path.isfile(candidate):
            with open(candidate, "r", encoding="utf-8") as fh:
                return set(json.load(fh).keys())
    return set()


def _flag(run_id, task_id, kind, signal_id, source, line_no, excerpt, note):
    raw = f"{run_id}|{source}|{signal_id}|{line_no}|{excerpt}"
    return {
        "flag_id": hashlib.sha256(raw.encode()).hexdigest()[:16],
        "run_id": run_id,
        "task_id": task_id,
        "kind": kind,  # canary (proven leakage) | recall_tell | known_fix
        "signal_id": signal_id,
        "source": source,
        "line": line_no,
        "excerpt": excerpt[:300],
        "note": note,
        "status": "pending",
        "flagged_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def scan_text(text, source, run_id, task_id, canary_tokens=frozenset()):
    """Scan one text blob; returns flags. Shared by tree scan and stage A."""
    flags = []
    known_fix = _load_known_fix_patterns()
    for line_no, line in enumerate(text.splitlines(), 1):
        for match in CANARY_RE.finditer(line):
            token = match.group(0)
            registered = token in canary_tokens
            flags.append(_flag(
                run_id, task_id, "canary", "CANARY", source, line_no,
                line.strip(),
                ("registered canary for this battery — PROVEN key-artifact "
                 "leakage" if registered else
                 "canary-format token (cross-task or unregistered) — proven "
                 "leakage, verify registry"),
            ))
        for signal_id, pattern, note in TELL_PATTERNS:
            if pattern.search(line):
                flags.append(_flag(
                    run_id, task_id, "recall_tell", signal_id, source,
                    line_no, line.strip(), note))
        for signal_id, pattern, note in known_fix:
            if pattern.search(line):
                flags.append(_flag(
                    run_id, task_id, "known_fix", signal_id, source,
                    line_no, line.strip(), note))
    return flags


def scan_tree(tree_path, run_id, task_id, battery_root=DEFAULT_BATTERY_ROOT):
    """Recall signal per terminal artifact: scan every text file in the tree."""
    canary_tokens = load_canary_tokens(battery_root, task_id)
    flags = []
    for root, dirs, files in os.walk(tree_path):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for name in sorted(files):
            ext = os.path.splitext(name)[1].lower()
            full = os.path.join(root, name)
            if ext not in _SCANNABLE_EXT and ext != "":
                continue
            try:
                with open(full, "r", encoding="utf-8") as fh:
                    text = fh.read()
            except (UnicodeDecodeError, OSError):
                continue
            rel = os.path.relpath(full, tree_path)
            flags.extend(
                scan_text(text, rel, run_id, task_id, canary_tokens))
    return flags


def append_queue(flags, queue_path=DEFAULT_QUEUE_PATH):
    """Route flags to the adjudication queue (G5). Append-only JSONL."""
    if not flags:
        return None
    os.makedirs(os.path.dirname(queue_path), exist_ok=True)
    with open(queue_path, "a", encoding="utf-8") as fh:
        for flag in flags:
            fh.write(json.dumps(flag, sort_keys=True) + "\n")
    return queue_path


if __name__ == "__main__":
    if len(sys.argv) not in (4, 5):
        sys.exit("usage: recall_scan.py <tree_path> <run_id> <task_id> "
                 "[battery_root]")
    battery = sys.argv[4] if len(sys.argv) == 5 else DEFAULT_BATTERY_ROOT
    found = scan_tree(sys.argv[1], sys.argv[2], sys.argv[3], battery)
    print(json.dumps({"flag_count": len(found), "flags": found}, indent=2))
    sys.exit(1 if found else 0)
