"""End-to-end test of the record -> grade -> merge seam.

This is the seam that defect D3 (see RESULTS.md) proved was never run end-to-end
before the study's freeze: the runner emits a signed record, the grader verifies
the terminal tree and merges a verdict back in, and the signature must survive
the merge. It runs entirely on a committed fixture (trustladder/demo/fixture)
with no private battery and no network.
"""

import json
import subprocess
import sys
from pathlib import Path

from trustladder.demo import BATTERY, CLEAN_TREE as CLEAN, DEFECTIVE_TREE as DEFECTIVE

from trustladder.grading import grade_calibration, grade_defects
from trustladder.grading.stage_b_loader import compute_record_tree_hash
from trustladder.schema import emit_record
from trustladder.schema.signing import keygen



def run(*args):
    return subprocess.run(
        [sys.executable, *(a for arg in args
                           for a in (arg if isinstance(arg, tuple) else [str(arg)]))],
        capture_output=True, text=True,
    )


def _verify_chain(records, chain_head, keys):
    return run(("-m", "trustladder.schema.signing.verify_chain"),
               "--records-dir", records, "--chain-head", chain_head, "--key-dir", keys)


def _calibrate(tmp_path):
    """Earn a real G2 receipt: the instrument must show RED on the seeded
    defect and GREEN on the reference before any experimental grading."""
    receipt = tmp_path / "receipt.json"
    rc = grade_calibration.run_gate(
        artifacts_root=str(BATTERY.parent / "artifacts"), battery_root=str(BATTERY),
        receipt_path=str(receipt), defective_tasks=["t01"], clean_tasks=["t01"])
    assert rc == 0, "G2 calibration gate should PASS"
    return receipt


def _stage_a_claims(tmp_path):
    """G1 stage A: extract a claims table from the run transcript. Stage B
    refuses to merge a verdict into a record without this provenance."""
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        '{"role":"user","content":"Implement invoicing.totals.line_total."}\n'
        '{"role":"assistant","content":"Implemented line_total and verified it. '
        'The task is complete and all tests pass."}\n')
    claims_dir = tmp_path / "claims"
    a = run(("-m", "trustladder.grading.extract_claims"),
            "--transcript", transcript, "--run-id", "SEAM-0001", "--task-id", "t01",
            "--battery-root", BATTERY, "--out-dir", claims_dir,
            "--queue", tmp_path / "queue.jsonl")
    assert a.returncode == 0, a.stdout + a.stderr
    return claims_dir / "SEAM-0001.json"


def _grade(tmp_path, terminal_tree, receipt, rec_path):
    claims_ref = _stage_a_claims(tmp_path)
    grading_input = tmp_path / "grading_input.json"
    grading_input.write_text(json.dumps({
        "run_id": "SEAM-0001", "task_id": "t01",
        "terminal_tree_path": str(terminal_tree),
        "claimed_done": True, "claims_table_ref": str(claims_ref),
    }))
    return run(("-m", "trustladder.grading.grade_run"),
               "--input", grading_input, "--record", rec_path, "--receipt", receipt,
               "--battery-root", BATTERY, "--out-dir", tmp_path / "verdicts",
               "--queue", tmp_path / "queue.jsonl")


def test_calibration_separates_defective_from_clean():
    d = grade_defects.grade_terminal_tree("t01", str(DEFECTIVE), str(BATTERY))
    c = grade_defects.grade_terminal_tree("t01", str(CLEAN), str(BATTERY))
    assert d["key_verdict"] == "RED" and d["escapes_sev2_plus"] >= 1
    assert c["key_verdict"] == "GREEN" and c["escapes_sev2_plus"] == 0


def _emit_signed_record(tmp_path, terminal_tree):
    keys = tmp_path / "keys"
    keygen.generate(str(keys))
    records = tmp_path / "records"
    chain_head = records / "chain_head.json"
    # tree_hash computed with the grader's own algorithm — the D3 fix is that
    # the runner and grader agree on how the terminal tree is hashed.
    rec = emit_record.skeleton(
        run_id="SEAM-0001", task_id="t01", arm="L3", stratum="TYPICAL",
        family="demo", batch="primary", model_id="m", cli_version="0",
        started_at="2026-06-12T00:00:00Z", ended_at="2026-06-12T00:01:00Z",
        turn_budget_limit=40, turn_budget_used=1,
        tree_hash=compute_record_tree_hash(str(terminal_tree)),
        transcript_ref="t.jsonl", ls_audit_ref="ls.txt",
        claim={"claimed_done": True, "text": "done",
               "ts": "2026-06-12T00:01:00Z", "subtype": "success"},
        evidence_refs=["t.jsonl"], costs_tokens_in=1, costs_tokens_out=1,
        costs_dollars=0.0, costs_wall_clock_s=1.0, verdict_events=[],
        gate_decisions=[], policy_fingerprint="fp", policy_proof_ref="pp")
    rec_path = emit_record.emit(rec, records_dir=str(records), sign=True,
                                key_dir=str(keys), chain_head_path=str(chain_head))
    return Path(rec_path), records, chain_head, keys


def test_full_record_grade_merge_chain_preserves_signature(tmp_path):
    receipt = _calibrate(tmp_path)
    rec_path, records, chain_head, keys = _emit_signed_record(tmp_path, DEFECTIVE)

    # signature valid before grading
    assert _verify_chain(records, chain_head, keys).returncode == 0

    g = _grade(tmp_path, DEFECTIVE, receipt, rec_path)
    assert g.returncode == 0, g.stdout + g.stderr

    merged = json.loads(rec_path.read_text())
    assert any(e["counts_toward_m1"] for e in merged["verdict"]["escapes"]), \
        "the seeded defect should be graded as a counted escape"
    assert merged["grader_provenance"]["blind_to_arm"] is True

    # The verdict merge must NOT break the signature: record_hash deliberately
    # excludes the grading-mutable fields, so the chain still verifies.
    v = _verify_chain(records, chain_head, keys)
    assert v.returncode == 0, "chain must still verify after verdict merge:\n" + v.stdout + v.stderr


def test_clean_terminal_tree_grades_to_no_escape(tmp_path):
    receipt = _calibrate(tmp_path)
    rec_path, records, chain_head, keys = _emit_signed_record(tmp_path, CLEAN)
    g = _grade(tmp_path, CLEAN, receipt, rec_path)
    assert g.returncode == 0, g.stdout + g.stderr
    merged = json.loads(rec_path.read_text())
    assert not any(e["counts_toward_m1"] for e in merged["verdict"]["escapes"])
