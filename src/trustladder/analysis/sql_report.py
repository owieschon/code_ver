"""Descriptive reporting over a run workspace, expressed in SQL.

This is a *reporting* layer, not the registered analysis. The run-records and the
violation ledger are naturally relational — one row per run (arm, stratum,
completion, escape) plus a violations table to join — so the descriptive
aggregations (per-arm completion and escape rates, by-stratum breakdowns,
violations by class) are written as plain SQL against an in-memory SQLite
database. No new dependency: `sqlite3` ships with Python.

The line is deliberate. *Descriptive* aggregation belongs in SQL and lives here.
*Inferential* statistics — the BCa bootstrap, Newcombe paired intervals, the
confirmatory three-outcome decision — are not expressible in SQL and stay in
`analysis.py` / `stats.py`, which is the registered, auditable path. This module
never makes a confirmatory claim; it summarises.

`tests/test_sql_report.py` asserts these SQL aggregates match an independent
Python computation over the same records, so the SQL is a verified re-expression
of the metric definitions, not a second source of truth.
"""

import argparse
import glob
import json
import os
import sqlite3
import sys

SEV_RANK = [("SEV1", 1), ("SEV2", 2), ("SEV3", 3), ("SEV4", 4)]

_SQL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sql")


def _sql(name):
    """Load a query from analysis/sql/. The queries live in their own .sql files
    so they read top-to-bottom and stay editable as SQL, not buried in Python."""
    with open(os.path.join(_SQL_DIR, name), encoding="utf-8") as fh:
        return fh.read()


def _records(workspace):
    for path in sorted(glob.glob(os.path.join(workspace, "records", "*.json"))):
        with open(path, encoding="utf-8") as fh:
            yield json.load(fh)


def _violations(workspace):
    pattern = os.path.join(workspace, "ledger", "*.violations.jsonl")
    for path in sorted(glob.glob(pattern)):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)


def load_workspace_db(workspace):
    """Normalise the nested run-records + ledger into a small relational schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_sql("schema.sql"))
    conn.executemany("INSERT INTO sev_rank VALUES (?, ?)", SEV_RANK)

    for rec in _records(workspace):
        verdict = rec.get("verdict") or {}
        completion = verdict.get("m0_complete")
        conn.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?)",
            (rec.get("run_id"), rec.get("task_id"), rec.get("arm"),
             rec.get("stratum"), rec.get("batch"),
             None if completion is None else int(bool(completion))),
        )
        for e in verdict.get("escapes", []):
            conn.execute(
                "INSERT INTO escapes VALUES (?, ?, ?, ?)",
                (rec.get("run_id"), e.get("defect_id"), e.get("sev"),
                 int(bool(e.get("escaped")))),
            )
    for v in _violations(workspace):
        conn.execute(
            "INSERT INTO violations VALUES (?, ?, ?)",
            (v.get("run_id"), v.get("class"), int(bool(v.get("survived_to_done")))),
        )
    conn.commit()
    return conn


# Queries live in analysis/sql/*.sql so they read as SQL, not embedded strings.
PER_ARM_SQL = _sql("per_arm.sql")
BY_STRATUM_SQL = _sql("by_stratum.sql")
VIOLATIONS_BY_CLASS_SQL = _sql("violations_by_class.sql")


def query(conn, sql):
    return [dict(row) for row in conn.execute(sql).fetchall()]


def per_arm_summary(conn):
    return query(conn, PER_ARM_SQL)


def by_stratum(conn):
    return query(conn, BY_STRATUM_SQL)


def violations_by_class(conn):
    return query(conn, VIOLATIONS_BY_CLASS_SQL)


def _print_table(title, rows):
    print("\n%s" % title)
    if not rows:
        print("  (no rows)")
        return
    cols = list(rows[0])
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    print("  " + "  ".join(c.ljust(widths[c]) for c in cols))
    for r in rows:
        print("  " + "  ".join(str(r[c]).ljust(widths[c]) for c in cols))


def main(argv=None):
    parser = argparse.ArgumentParser(description="SQL descriptive report over a run workspace.")
    parser.add_argument("--workspace", required=True,
                        help="a run workspace (records/ + ledger/), e.g. from "
                             "`trustladder-analyze dummy --workspace ...`")
    args = parser.parse_args(argv)
    conn = load_workspace_db(args.workspace)
    print("Descriptive report (SQL over SQLite) — NOT the registered confirmatory analysis.")
    _print_table("Per arm (M0 completion, M1 escape at SEV2+):", per_arm_summary(conn))
    _print_table("Escape rate by arm x stratum:", by_stratum(conn))
    _print_table("Policy violations by arm x class:", violations_by_class(conn))
    return 0


if __name__ == "__main__":
    sys.exit(main())
