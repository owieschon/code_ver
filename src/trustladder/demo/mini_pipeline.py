"""Run the apparatus end-to-end on a stub agent.

The registered study needs the live agent, the private governance kit, and the
full task battery, none of which ship here. This driver substitutes a *stub
agent* — for each run it deterministically leaves either the defective or the
reference terminal tree from the demo fixture — and then runs the real machinery
over those runs:

    stub agent -> sign run-record -> stage-A claims -> G2-gated grade
               -> merge verdict -> aggregate per-arm escape rates (SQL)

Everything except the agent is the production code path. The point is that a
reviewer can watch records get produced, graded, and summarised, and see the
aggregation recover the planted signal (L3 << L1) from grading that actually
happened, rather than from fabricated numbers.

This is an illustration on synthetic data, not a result. Run:

    trustladder-mini-run --workspace /tmp/mini
"""

import argparse
import json
import os
import sys
import tempfile

from trustladder.demo import BATTERY, CLEAN_TREE, DEFECTIVE_TREE
from trustladder.grading import grade_calibration, grade_run
from trustladder.grading.extract_claims import extract_claims
from trustladder.grading.stage_b_loader import compute_record_tree_hash
from trustladder.schema import emit_record
from trustladder.schema.signing import keygen, verify_chain

# Stub agent behaviour: how many of each arm's runs ship the seeded defect.
# This encodes the study's hypotheses for the demo — L3's gate blocks most
# defects (H1), and the placebo SHAM behaves like instruction-only L1 (H1a).
RUNS_PER_ARM = 8
DEFECTS_BY_ARM = {"L0": 7, "L1": 6, "SHAM": 6, "L3": 1}


def _stub_plan():
    """A fixed, deterministic schedule of (run_id, arm, ships_defect)."""
    plan = []
    for arm, n_defective in DEFECTS_BY_ARM.items():
        for i in range(RUNS_PER_ARM):
            ships_defect = i < n_defective
            plan.append(("RUN-%s-%02d" % (arm, i), arm, ships_defect))
    return plan


def _emit_record(run_id, arm, terminal_tree, records_dir, key_dir, chain_head):
    """Sign a run-record for what the stub agent left, using the grader's own
    tree-hash algorithm (the runner and grader must agree — this is D3)."""
    rec = emit_record.skeleton(
        run_id=run_id, task_id="t01", arm=arm, stratum="TYPICAL",
        family="demo", batch="primary", model_id="stub-agent", cli_version="0",
        started_at="2026-06-12T00:00:00Z", ended_at="2026-06-12T00:01:00Z",
        turn_budget_limit=40, turn_budget_used=1,
        tree_hash=compute_record_tree_hash(str(terminal_tree)),
        transcript_ref="%s.transcript.jsonl" % run_id, ls_audit_ref="ls.txt",
        claim={"claimed_done": True, "text": "done",
               "ts": "2026-06-12T00:01:00Z", "subtype": "success"},
        evidence_refs=["%s.transcript.jsonl" % run_id],
        costs_tokens_in=1, costs_tokens_out=1, costs_dollars=0.0,
        costs_wall_clock_s=1.0, verdict_events=[],
        gate_decisions=[] if arm in ("SHAM", "L3") else None,
        policy_fingerprint="fp" if arm in ("SHAM", "L3") else None,
        policy_proof_ref="pp" if arm in ("SHAM", "L3") else None)
    return emit_record.emit(rec, records_dir=str(records_dir), sign=True,
                            key_dir=str(key_dir), chain_head_path=str(chain_head))


def run(workspace, log=print):
    os.makedirs(workspace, exist_ok=True)
    records_dir = os.path.join(workspace, "records")
    key_dir = os.path.join(workspace, "keys")
    claims_dir = os.path.join(workspace, "claims")
    verdicts_dir = os.path.join(workspace, "verdicts")
    queue = os.path.join(workspace, "adjudication_queue.jsonl")
    chain_head = os.path.join(records_dir, "chain_head.json")
    os.makedirs(records_dir, exist_ok=True)

    log("1. signing key")
    keygen.generate(key_dir)

    log("2. G2 calibration gate (RED on the defect, GREEN on the reference)")
    receipt = os.path.join(workspace, "calibration_receipt.json")
    rc = grade_calibration.run_gate(
        artifacts_root=str(BATTERY.parent / "artifacts"), battery_root=str(BATTERY),
        receipt_path=receipt, defective_tasks=["t01"], clean_tasks=["t01"])
    if rc != 0:
        log("   calibration FAILED — aborting")
        return 1

    log("3. stub agent -> sign -> stage-A claims -> G2-gated grade -> merge")
    for run_id, arm, ships_defect in _stub_plan():
        tree = DEFECTIVE_TREE if ships_defect else CLEAN_TREE
        rec_path = _emit_record(run_id, arm, tree, records_dir, key_dir, chain_head)

        transcript = os.path.join(workspace, "%s.transcript.jsonl" % run_id)
        with open(transcript, "w", encoding="utf-8") as fh:
            fh.write('{"role":"assistant","content":"Implemented it. '
                     'The task is complete and tests pass."}\n')
        table = extract_claims(transcript, run_id, "t01")
        os.makedirs(claims_dir, exist_ok=True)
        claims_ref = os.path.join(claims_dir, "%s.json" % run_id)
        with open(claims_ref, "w", encoding="utf-8") as fh:
            json.dump(table, fh)

        grading_input = os.path.join(workspace, "%s.grading_input.json" % run_id)
        with open(grading_input, "w", encoding="utf-8") as fh:
            json.dump({"run_id": run_id, "task_id": "t01",
                       "terminal_tree_path": str(tree),
                       "claimed_done": True, "claims_table_ref": claims_ref}, fh)
        code = grade_run.main([
            "--input", grading_input, "--record", rec_path, "--receipt", receipt,
            "--battery-root", str(BATTERY), "--out-dir", verdicts_dir,
            "--queue", queue, "--session-id", "stub-%s" % run_id])
        if code != 0:
            log("   grade_run refused %s (exit %d) — aborting" % (run_id, code))
            return 1

    log("4. verify the signed record chain")
    vc = verify_chain.main(["--records-dir", records_dir,
                            "--chain-head", chain_head, "--key-dir", key_dir])
    if vc != 0:
        log("   chain verification FAILED")
        return 1

    log("5. aggregate per-arm escape rate from the graded records (SQL)")
    from trustladder.analysis import sql_report
    conn = sql_report.load_workspace_db(workspace)
    rows = sql_report.per_arm_summary(conn)
    log("")
    log("   arm   n   completion%   escape%   (escape = graded SEV2+ defect)")
    for r in rows:
        log("   %-5s %-3d %-12s %s" % (r["arm"], r["n"], r["completion_pct"], r["escape_pct"]))
    log("")
    rates = {r["arm"]: r["escape_pct"] for r in rows}
    log("Signal recovered through real grading: L3 escape %.1f%% vs L1 %.1f%%."
        % (rates["L3"], rates["L1"]))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the apparatus end-to-end on a stub agent.")
    parser.add_argument("--workspace", default=None,
                        help="output dir (default: a fresh temp dir)")
    args = parser.parse_args(argv)
    workspace = args.workspace or tempfile.mkdtemp(prefix="trustladder-mini-")
    print("workspace: %s\n" % workspace)
    return run(workspace)


if __name__ == "__main__":
    sys.exit(main())
