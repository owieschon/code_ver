"""A small self-contained demonstration of the apparatus end-to-end.

`fixture/` holds one seeded task (an off-by-one defect, its answer key, and clean
and defective terminal trees). `mini_pipeline` drives the full
produce -> grade -> aggregate chain over it with a stub agent, so the central
claim — that the machinery runs end-to-end — is executable, not just described.
"""

from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixture"
BATTERY = FIXTURE / "battery"
DEFECTIVE_TREE = FIXTURE / "artifacts" / "defective" / "t01"
CLEAN_TREE = FIXTURE / "artifacts" / "clean" / "t01"
