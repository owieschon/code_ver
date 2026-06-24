#!/usr/bin/env python3
"""Record an adjudication ruling on a grading recall/canary flag
(G5 -> AP7 wire).

A flag in grading/adjudication/queue.jsonl is 'pending' until the
adjudicator rules (rulings are the adjudicator's own, with a 1-2 line
rationale). This tool records the ruling and wires confirmed
contamination into the CANONICAL AP7 pipeline:

  ledger/exclusions.json has exactly ONE writer — ledger/ap7_routing.py,
  which regenerates it wholesale from
  ledger/contamination_flags.jsonl + ledger/adjudication_queue.jsonl
  (rows with status CONFIRMED) and appends the PROVENANCE honesty rows
  (dual routing). Grading therefore never writes exclusions.json
  directly: a confirmed ruling here (a) updates the grading queue,
  (b) UPSERTS the flag into ledger/adjudication_queue.jsonl in
  ap7_routing's row schema with status CONFIRMED + ruling metadata,
  and (c) invokes ap7_routing.route() so exclusions.json and the
  M3/PROVENANCE rows are produced by the single canonical writer.

  --ruling cleared: grading queue updated (and any matching ledger
  queue row marked CLEARED); nothing routes, nothing excluded.
"""

import argparse
import importlib.util
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir))
DEFAULT_QUEUE_PATH = os.path.join(_HERE, "adjudication", "queue.jsonl")
DEFAULT_LEDGER_DIR = os.path.join(REPO_ROOT, "harness", "ledger")


def _load_ap7(ledger_dir):
    path = os.path.join(ledger_dir, "ap7_routing.py")
    if not os.path.isfile(path):
        # Scratch ledger dirs (demos/tests) borrow the canonical module.
        path = os.path.join(DEFAULT_LEDGER_DIR, "ap7_routing.py")
    spec = importlib.util.spec_from_file_location("ap7_routing", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_jsonl(path):
    entries = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    return entries


def _write_jsonl(entries, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True)
                     + "\n")


def upsert_ledger_queue_row(ledger_dir, flag, status, ruling_meta):
    """Upsert a grading flag into ledger/adjudication_queue.jsonl using
    ap7_routing's row schema (consumed by ap7_routing)."""
    queue_path = os.path.join(ledger_dir, "adjudication_queue.jsonl")
    rows = _read_jsonl(queue_path)
    row = {
        "run_id": flag["run_id"],
        "task_id": flag["task_id"],
        "flag_id": flag["flag_id"],
        "signal_type": ("CANARY_HIT" if flag["kind"] == "canary"
                        else "RECALL_SIGNAL"),
        "pattern_id": "GRADING.%s" % flag["signal_id"],
        "evidence_path": flag["source"],
        "evidence_excerpt": flag["excerpt"],
        "status": status,
        "packet_hint": "packets/%s_%s.md" % (flag["run_id"],
                                             flag["flag_id"]),
        "ruling": ruling_meta,
        "origin": "grading/recall_scan",
    }
    replaced = False
    for i, existing in enumerate(rows):
        if existing.get("flag_id") == flag["flag_id"]:
            rows[i] = row
            replaced = True
    if not replaced:
        rows.append(row)
    _write_jsonl(rows, queue_path)
    return queue_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flag-id", required=True)
    parser.add_argument("--ruling", required=True,
                        choices=["confirmed", "cleared"])
    parser.add_argument("--rationale", required=True,
                        help="adjudicator's 1-2 line rationale, his own "
                             "words (G5)")
    parser.add_argument("--adjudicator", default="independent methodologist")
    parser.add_argument("--queue", default=DEFAULT_QUEUE_PATH,
                        help="grading adjudication queue (JSONL)")
    parser.add_argument("--ledger-dir", default=DEFAULT_LEDGER_DIR,
                        help="ledger dir holding adjudication_queue.jsonl; "
                             "ap7_routing regenerates exclusions.json here")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.queue):
        print(f"RULING REFUSED: no grading adjudication queue at "
              f"{args.queue}", file=sys.stderr)
        return 2
    entries = _read_jsonl(args.queue)
    matched = [e for e in entries if e.get("flag_id") == args.flag_id]
    if not matched:
        print(f"RULING REFUSED: flag_id {args.flag_id} not in queue "
              f"{args.queue}", file=sys.stderr)
        return 2

    ruled_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    ruling_meta = {
        "ruling": args.ruling,
        "rationale": args.rationale,
        "adjudicator": args.adjudicator,
        "ruled_at": ruled_at,
    }
    new_status = ("CONFIRMED" if args.ruling == "confirmed" else "CLEARED")
    for entry in matched:
        entry["status"] = new_status.lower()
        entry["ruling"] = ruling_meta
    _write_jsonl(entries, args.queue)

    flag = matched[0]
    ledger_queue = upsert_ledger_queue_row(
        args.ledger_dir, flag, new_status, ruling_meta)

    if args.ruling == "confirmed":
        ap7 = _load_ap7(args.ledger_dir)
        out, doc = ap7.route(args.ledger_dir)
        print(f"flag {args.flag_id}: CONFIRMED — wired into {ledger_queue}; "
              f"canonical AP7 routing regenerated {out} "
              f"(excluded run_ids: {doc['excluded_run_ids']}; dual routing: "
              "out of M0/M1, PROVENANCE row in the M3 honesty ledger)")
    else:
        print(f"flag {args.flag_id}: cleared (benign); grading queue + "
              f"{ledger_queue} updated, nothing routed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
