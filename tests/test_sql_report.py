"""The SQL report must agree with the Python metric definitions.

This is what keeps the SQL layer honest: it is a re-expression of the same
metrics the registered analysis computes, not a second, drifting source of
truth. We compute the per-arm completion and escape rates two ways — once in
SQL (sql_report) and once directly in Python (the analysis definitions) — and
require them to match exactly.
"""

import subprocess
import sys

from trustladder.analysis import sql_report
from trustladder.analysis.analysis import is_m0_complete, is_m1_escape, load_records


def _dummy_workspace(tmp_path):
    ws = tmp_path / "ws"
    r = subprocess.run(
        [sys.executable, "-m", "trustladder.analysis.analysis", "dummy",
         "--workspace", str(ws), "--scenario", "confirmed"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return ws


def _python_per_arm(ws):
    """The metrics computed straight from the analysis definitions."""
    records = [r for r in load_records(str(ws / "records")) if r.get("batch") == "primary"]
    by_arm = {}
    for r in records:
        by_arm.setdefault(r["arm"], []).append(r)
    out = {}
    for arm, rows in by_arm.items():
        n = len(rows)
        out[arm] = {
            "n": n,
            "completion_pct": round(100.0 * sum(is_m0_complete(r) for r in rows) / n, 1),
            "escape_pct": round(100.0 * sum(is_m1_escape(r) for r in rows) / n, 1),
        }
    return out


def test_sql_per_arm_matches_python(tmp_path):
    ws = _dummy_workspace(tmp_path)
    conn = sql_report.load_workspace_db(str(ws))
    sql_rows = {row["arm"]: row for row in sql_report.per_arm_summary(conn)}
    expected = _python_per_arm(ws)

    assert set(sql_rows) == set(expected)
    for arm, exp in expected.items():
        got = sql_rows[arm]
        assert got["n"] == exp["n"], arm
        assert got["completion_pct"] == exp["completion_pct"], arm
        assert got["escape_pct"] == exp["escape_pct"], arm


def test_sql_report_runs_as_a_command(tmp_path):
    ws = _dummy_workspace(tmp_path)
    r = subprocess.run(
        [sys.executable, "-m", "trustladder.analysis.sql_report", "--workspace", str(ws)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "Per arm" in r.stdout and "escape_pct" in r.stdout


def test_only_primary_batch_counts(tmp_path):
    """Controls (non-'primary' batches) must be excluded from M0/M1, matching
    the registered analysis."""
    ws = _dummy_workspace(tmp_path)
    conn = sql_report.load_workspace_db(str(ws))
    # every arm in the per-arm summary should have n == 18 (the primary battery),
    # not the larger count that would result from including controls.
    for row in sql_report.per_arm_summary(conn):
        assert row["n"] == 18, row
