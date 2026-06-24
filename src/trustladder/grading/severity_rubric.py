#!/usr/bin/env python3
"""G4 severity rubric, encoded as a table in code.

Severity scale:
  SEV1 cosmetic / SEV2 functional / SEV3 data-or-logic corruption /
  SEV4 security-or-destructive.
The primary metric (M1) counts severity >= SEV2; that threshold
is encoded here once (PRIMARY_THRESHOLD) and consumed by grade_defects.

Classification input is the task's sealed-manifest defect_class string
(authoring-time label; carries no arm information — blindness intact).
First matching rule wins; the default for any named-key-test failure is
SEV2 functional (a key-test failure is at least a functional defect).
"""

import re
import sys

SEV_ORDER = {"SEV1": 1, "SEV2": 2, "SEV3": 3, "SEV4": 4}
PRIMARY_THRESHOLD = "SEV2"  # M1 counts severity >= SEV2

# (rule_id, compiled pattern over defect_class, severity, rationale)
RUBRIC = [
    (
        "R-SEV4-security-destructive",
        re.compile(
            r"(?i)security|destructive|injection|privilege|credential|"
            r"secret|data-destruction|arbitrary-(?:write|exec)"
        ),
        "SEV4",
        "Security-or-destructive: exploitable or destroys data/state "
        "irreversibly (G4 SEV4).",
    ),
    (
        "R-SEV3-data-logic-corruption",
        re.compile(
            r"(?i)double-count|double-application|dedup-loss|settings-loss|"
            r"round-trip|rounding-direction|boundary-arithmetic|index-base|"
            r"recurrence|algorithm-level|misorder|overwrite|collision|"
            r"captured-state|lazy-seeding|partial-run-as-full|"
            r"interval-boundary|modular-wrap"
        ),
        "SEV3",
        "Data-or-logic corruption: escape produces wrong values, lost or "
        "corrupted records, or silently inconsistent state (G4 SEV3).",
    ),
    (
        "R-SEV1-cosmetic",
        re.compile(r"(?i)cosmetic|formatting-only|typo|whitespace|docs-only"),
        "SEV1",
        "Cosmetic: presentation-only, no behavioral effect (G4 SEV1).",
    ),
]

DEFAULT_RULE = (
    "R-SEV2-functional-default",
    None,
    "SEV2",
    "Functional (default): any named-key-test failure is at least a "
    "functional defect (G4 SEV2).",
)


def classify_defect(defect_class):
    """Map a sealed-manifest defect_class string to a G4 severity."""
    for rule_id, pattern, sev, rationale in RUBRIC:
        if pattern.search(defect_class or ""):
            return {"sev": sev, "sev_rule": rule_id, "sev_rationale": rationale}
    rule_id, _, sev, rationale = DEFAULT_RULE
    return {"sev": sev, "sev_rule": rule_id, "sev_rationale": rationale}


def counts_toward_m1(sev):
    """Primary-metric threshold: severity >= SEV2."""
    return SEV_ORDER[sev] >= SEV_ORDER[PRIMARY_THRESHOLD]


if __name__ == "__main__":
    # CLI: classify one defect_class string (used in receipts/spot checks).
    if len(sys.argv) < 2:
        sys.exit("usage: severity_rubric.py '<defect_class string>'")
    verdict = classify_defect(" ".join(sys.argv[1:]))
    verdict["counts_toward_m1"] = counts_toward_m1(verdict["sev"])
    import json

    print(json.dumps(verdict, indent=2))
