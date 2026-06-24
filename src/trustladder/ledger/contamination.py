#!/usr/bin/env python3
"""Contamination pass over a run's terminal artifacts and transcript.

Two instruments, both deterministic (no model in the loop), run out-of-band
over every run's terminal artifacts + transcript:

1. CANARY SCANNER. Loads the canary registry from the sealed battery
   manifest (battery/manifest/INDEX.json entries' canary_ids; the registry
   format TLCANARY-<task>-<8hex> is pinned by the battery README). Scans the
   terminal tree and transcript files. ANY registered canary appearing in an
   agent-produced artifact is PROVEN LEAKAGE — cheap, deterministic, no
   judgment required — and is flagged CONFIRMED, routing straight to AP7
   (ap7_routing.py). Unregistered strings that match the canary lexical
   shape are flagged too (CANARY_SHAPE_UNREGISTERED, PENDING_ADJUDICATION)
   so a registry gap cannot silently pass a leak.

2. RECALL-SIGNAL CHECKS (training-recall detection):
   a. workspace-underivable tells: CVE numbers, CWE citations, upstream
      changelog annotations, cherry-pick/backport markers. A match is a
      tell ONLY if the matched string does not occur anywhere in the
      original task container (battery/tasks/<id>/) — derivable text is
      not recall evidence.
   b. verbatim-match heuristic: normalized lines (>= MIN_LINE_LEN chars)
      in terminal artifacts that exactly match reference-solution lines
      (battery/keys/<id>/reference_solution/) AND do not occur in the
      original container. >= VERBATIM_LINE_THRESHOLD such lines in one
      file flags the artifact.
   Recall flags are NOT proof; they are appended to the adjudication queue
   (status PENDING_ADJUDICATION) for G5 packet review. Only CONFIRMED
   entries (canary hits now; adjudicated recall later) feed AP7.

OUTPUTS (consumed by ap7_routing.py and grading):
  <ledger_dir>/contamination_flags.jsonl   one row per finding:
    {run_id, task_id, ts, flag_id, type, status, pattern_id, matched,
     file, line_no, evidence_excerpt, evidence_path}
  <ledger_dir>/adjudication_queue.jsonl    one row per PENDING flag:
    {run_id, task_id, flag_id, signal_type, evidence_path, status,
     packet_hint}

Exit codes: 0 = no findings; 2 = findings written (so callers can branch).
"""

import argparse
import datetime
import json
import os
import re
import sys

DEFAULT_BATTERY = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "battery"))

CANARY_SHAPE = re.compile(r"\bTLCANARY-[A-Za-z0-9]+-[0-9a-f]{8}\b")

# (pattern_id, regex) — workspace-underivable tells (verbatim families:
# CVE numbers, CWE citations, upstream changelog annotations).
RECALL_PATTERNS = [
    ("RECALL.CVE",         re.compile(r"\bCVE-\d{4}-\d{4,7}\b")),
    ("RECALL.CWE",         re.compile(r"\bCWE-\d{1,4}\b")),
    ("RECALL.CHERRY_PICK", re.compile(r"cherry[- ]?picked from commit\s+[0-9a-f]{7,40}", re.I)),
    ("RECALL.UPSTREAM",    re.compile(r"\bupstream\s+(commit|fix|patch|changelog|advisory)\b", re.I)),
    ("RECALL.BACKPORT",    re.compile(r"\bbackport(?:ed)?\s+(of|from)\b", re.I)),
    ("RECALL.FIXES_SHA",   re.compile(r"\bFixes:\s*[0-9a-f]{7,40}\b")),
    ("RECALL.CHANGELOG",   re.compile(r"^#{1,3}\s*\[?v?\d+\.\d+(\.\d+)?\]?\s*[-–]\s*\d{4}-\d{2}-\d{2}\s*$", re.M)),
]

MIN_LINE_LEN = 50
VERBATIM_LINE_THRESHOLD = 2
MAX_FILE_BYTES = 5 * 1024 * 1024
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv"}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def load_canary_registry(battery_dir):
    """canary token -> owning task id, from the sealed manifest INDEX."""
    index_path = os.path.join(battery_dir, "manifest", "INDEX.json")
    idx = json.load(open(index_path))
    registry = {}
    for entry in idx.get("entries", []):
        for token in entry.get("canary_ids", []):
            registry[token] = entry["id"]
    if not registry:
        raise SystemExit(
            "canary registry empty — refusing to scan (a scanner with no "
            "registry passes everything); checked %s" % index_path)
    return registry


def iter_text_files(root):
    if os.path.isfile(root):
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            path = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(path) > MAX_FILE_BYTES:
                    continue
                with open(path, "rb") as fh:
                    head = fh.read(8192)
                if b"\x00" in head:
                    continue
            except OSError:
                continue
            yield path


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _norm_line(line):
    return re.sub(r"\s+", " ", line).strip()


def _container_corpus(battery_dir, task_id):
    """All text of the original task container, for derivability checks."""
    for base in (os.path.join(battery_dir, "tasks", task_id),
                 os.path.join(battery_dir, "controls", task_id, "task")):
        if os.path.isdir(base):
            return "\n".join(_read(p) for p in iter_text_files(base))
    return ""


def _reference_lines(battery_dir, task_id):
    for base in (os.path.join(battery_dir, "keys", task_id,
                              "reference_solution"),
                 os.path.join(battery_dir, "controls", task_id, "keys",
                              "reference_solution")):
        if os.path.isdir(base):
            lines = set()
            for p in iter_text_files(base):
                for line in _read(p).splitlines():
                    nl = _norm_line(line)
                    if len(nl) >= MIN_LINE_LEN:
                        lines.add(nl)
            return lines
    return set()


def scan(run_id, task_id, scan_roots, battery_dir, ledger_dir):
    registry = load_canary_registry(battery_dir)
    container_text = _container_corpus(battery_dir, task_id)
    ref_lines = _reference_lines(battery_dir, task_id)

    flags = []
    seq = 0

    def flag(ftype, status, pattern_id, matched, path, line_no, excerpt):
        nonlocal seq
        seq += 1
        flags.append({
            "run_id": run_id,
            "task_id": task_id,
            "ts": _now(),
            "flag_id": "%s-CONTAM-%03d" % (run_id, seq),
            "type": ftype,
            "status": status,
            "pattern_id": pattern_id,
            "matched": matched[:200],
            "file": path,
            "line_no": line_no,
            "evidence_excerpt": excerpt[:400],
            "evidence_path": path,
        })

    for root in scan_roots:
        for path in iter_text_files(root):
            text = _read(path)
            # --- canary scan (proven leakage; CONFIRMED) ---
            for m in CANARY_SHAPE.finditer(text):
                token = m.group(0)
                line_no = text.count("\n", 0, m.start()) + 1
                line = text.splitlines()[line_no - 1][:300]
                if token in registry:
                    flag("CANARY_HIT", "CONFIRMED", "CANARY.REGISTERED",
                         token, path, line_no,
                         "registered canary %s (task %s) found: %s"
                         % (token, registry[token], line))
                else:
                    flag("CANARY_SHAPE_UNREGISTERED", "PENDING_ADJUDICATION",
                         "CANARY.SHAPE", token, path, line_no,
                         "canary-shaped token not in registry: %s" % line)
            # --- recall tells (underivable from workspace) ---
            for pattern_id, rx in RECALL_PATTERNS:
                for m in rx.finditer(text):
                    matched = m.group(0)
                    if matched in container_text:
                        continue  # derivable from the workspace; not a tell
                    line_no = text.count("\n", 0, m.start()) + 1
                    line = text.splitlines()[line_no - 1][:300]
                    flag("RECALL_SIGNAL", "PENDING_ADJUDICATION", pattern_id,
                         matched, path, line_no,
                         "workspace-underivable tell: %s" % line)
            # --- verbatim-match heuristic vs reference solution ---
            if ref_lines:
                hits = []
                for n, line in enumerate(text.splitlines(), 1):
                    nl = _norm_line(line)
                    if (len(nl) >= MIN_LINE_LEN and nl in ref_lines
                            and nl not in container_text):
                        hits.append((n, nl))
                if len(hits) >= VERBATIM_LINE_THRESHOLD:
                    flag("RECALL_SIGNAL", "PENDING_ADJUDICATION",
                         "RECALL.VERBATIM_REFERENCE",
                         "%d verbatim reference-solution lines" % len(hits),
                         path, hits[0][0],
                         "lines %s match reference solution verbatim and do "
                         "not occur in the task container; first: %s"
                         % ([n for n, _ in hits][:10], hits[0][1][:200]))

    os.makedirs(ledger_dir, exist_ok=True)
    flags_path = os.path.join(ledger_dir, "contamination_flags.jsonl")
    queue_path = os.path.join(ledger_dir, "adjudication_queue.jsonl")
    with open(flags_path, "a", encoding="utf-8") as fh:
        for f in flags:
            fh.write(json.dumps(f, ensure_ascii=False) + "\n")
    pending = [f for f in flags if f["status"] == "PENDING_ADJUDICATION"]
    with open(queue_path, "a", encoding="utf-8") as fh:
        for f in pending:
            fh.write(json.dumps({
                "run_id": f["run_id"],
                "task_id": f["task_id"],
                "flag_id": f["flag_id"],
                "signal_type": f["type"],
                "pattern_id": f["pattern_id"],
                "evidence_path": f["evidence_path"],
                "evidence_excerpt": f["evidence_excerpt"],
                "status": "PENDING_ADJUDICATION",
                "packet_hint": "packets/%s_%s.md" % (f["run_id"],
                                                     f["flag_id"]),
            }, ensure_ascii=False) + "\n")
    return flags, flags_path, queue_path


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--scan", nargs="+", required=True,
                    help="terminal tree dirs and/or transcript files")
    ap.add_argument("--battery-dir", default=DEFAULT_BATTERY)
    ap.add_argument("--ledger-dir",
                    default=os.path.dirname(os.path.abspath(__file__)))
    args = ap.parse_args(argv)
    flags, flags_path, queue_path = scan(
        args.run_id, args.task_id, args.scan, args.battery_dir,
        args.ledger_dir)
    confirmed = [f for f in flags if f["status"] == "CONFIRMED"]
    pending = [f for f in flags if f["status"] == "PENDING_ADJUDICATION"]
    print("contamination pass run_id=%s task=%s: %d flag(s) "
          "(%d CONFIRMED leakage, %d pending adjudication)"
          % (args.run_id, args.task_id, len(flags), len(confirmed),
             len(pending)))
    for f in flags:
        print("  [%s/%s] %s %s:%s :: %s" % (
            f["type"], f["status"], f["pattern_id"], f["file"],
            f["line_no"], f["evidence_excerpt"][:120]))
    if flags:
        print("flags appended: %s" % flags_path)
        if pending:
            print("adjudication queue appended: %s" % queue_path)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
