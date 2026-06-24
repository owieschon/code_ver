"""Shared data-model layer for the analysis package.

A few constants and small helpers that both the analysis pipeline and the
synthetic-data generator depend on. Kept in one place so neither imports the
other.
"""

from pathlib import Path


SEV_ORDER = {"SEV1": 1, "SEV2": 2, "SEV3": 3, "SEV4": 4}


ARMS = ["L0", "L1", "SHAM", "L3"]


def ws_paths(workspace):
    w = Path(workspace)
    return {
        "records": w / "records",
        "ledger": w / "ledger",
        "analysis": w / "analysis",
        "grading": w / "grading",
        "root": w,
    }


EXCLUSIONS_SCHEMA = "trustladder.ap7.exclusions.v1"


def is_m1_escape(rec, min_sev="SEV2"):
    floor = SEV_ORDER[min_sev]
    for e in rec["verdict"]["escapes"]:
        if e["escaped"] and SEV_ORDER[e["sev"]] >= floor:
            return True
    return False


def is_m0_complete(rec):
    return bool(rec["verdict"]["m0_complete"])

