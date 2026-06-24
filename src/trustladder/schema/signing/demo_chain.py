#!/usr/bin/env python3
"""demo_chain.py — live-path demonstration data for the chain check.

Builds three deterministic, schema-valid synthetic records via the
SHIPPED constructors (emit_record.skeleton -> emit(..., sign=True) ->
signing/receipt.py) into a scratch directory with its own chain head —
the REAL chain (receipts/chain_head.json) is never touched by demos.

Used by the demonstrated-red ceremony:
  emit 3 -> verify green -> modify record 2 -> verify RED
  (receipts/chain_red.txt) -> restore -> verify green
  (receipts/chain_green.txt).

usage: demo_chain.py <out_dir> [--unsigned] [--key-dir DIR]
Synthetic data only; no model, no network, deterministic content.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_DIR = os.path.dirname(HERE)
from trustladder.schema import emit_record

ARMS = ["L0", "L1", "L3"]


def build(i):
    run_id = "DEMO-CHAIN-%02d" % i
    arm = ARMS[i - 1]
    rec = emit_record.skeleton(
        run_id=run_id, task_id="demo-task-%02d" % i, arm=arm,
        stratum="TYPICAL", family="demo", batch="primary",
        model_id="claude-sonnet-4-6", cli_version="0.0.0-demo",
        started_at="2026-06-12T00:0%d:00Z" % i,
        ended_at="2026-06-12T00:0%d:30Z" % i,
        turn_budget_limit=40, turn_budget_used=i,
        tree_hash="%064d" % i,
        transcript_ref="demo/transcript_%02d.jsonl" % i,
        ls_audit_ref="demo/ls_audit_%02d.txt" % i,
        claim={"claimed_done": True, "text": "demo claim %d" % i,
               "ts": "2026-06-12T00:0%d:30Z" % i, "subtype": "success"},
        evidence_refs=["demo/transcript_%02d.jsonl" % i],
        costs_tokens_in=100 * i, costs_tokens_out=10 * i,
        costs_dollars=0.001 * i, costs_wall_clock_s=30.0,
        verdict_events=[],
        gate_decisions=None if arm in ("L0", "L1") else [],
        policy_fingerprint=None if arm in ("L0", "L1") else "demo-fp",
        policy_proof_ref=None if arm in ("L0", "L1") else "demo-proof",
    )
    return rec


def main(argv):
    if not argv:
        sys.stderr.write(__doc__)
        return 2
    out_dir = os.path.abspath(argv[0])
    unsigned = "--unsigned" in argv
    key_dir = None
    if "--key-dir" in argv:
        key_dir = argv[argv.index("--key-dir") + 1]
    chain_head = os.path.join(out_dir, "chain_head.json")
    os.makedirs(out_dir, exist_ok=True)
    for i in (1, 2, 3):
        rec = build(i)
        path = emit_record.emit(rec, records_dir=out_dir,
                                sign=not unsigned, key_dir=key_dir,
                                chain_head_path=chain_head)
        print("emitted%s: %s" % ("" if unsigned else " (signed)", path))
    if not unsigned:
        print("chain head: %s" % chain_head)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
