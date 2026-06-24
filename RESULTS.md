# Results

Stage 1 of the study produced no confirmatory result. Why it didn't is the substance of
this page.

## What was collected

The 72 primary subject runs executed correctly. Every record is signed, the hash chain
verifies, the planted-canary checks are clean, and all 72 terminal code trees recompute to
exactly their recorded `tree_hash`. The run data is intact.

## What broke

The freeze validated every component in isolation (each schedule builder, each validator,
each grading unit) but never ran a single real record through the full
run → grade → analysis chain before sealing. When the pipeline was finally run end-to-end,
the untested seam surfaced three defects in a row, all in guards and wiring, none in how the
runs were produced or measured:

- **D1**: the variance probe was never dispatched (the run command hardcoded the wrong validator).
- **D2**: the mini-replication was never built (a global-vs-registered interleave check).
- **D3**: no record could be graded. The runner and the grader hashed the final code tree
  with incompatible algorithms, so the integrity guard refused 100% of real records.

(The calibration receipt shipped at freeze was also a placeholder, not a real one.)

## The decision

Grading was halted before any confirmatory verdict was produced. The author did not
hand-assemble the confirmatory measurement path around the broken apparatus: in a study
whose premise is testing the author's own governance kit, that would invert the purpose of
preregistration. Instead the three defects, the meta-finding, the 72 raw records, and a
proposed remedy went to an independent methodologist for an admissibility ruling before any
verdict existed.

The proposed remedy was one disclosed, supervised end-to-end validation pass of the full
grading chain on the real records, replacing reactive defect-by-defect workarounds with a
single auditable pass that surfaces any remaining seam defects before confirmatory verdicts
are produced.

## Reading this as a result

The failure mode (every unit test passed, but the system was never run end-to-end) is a
common and expensive one. The response is worth noting: under maximum conflict of interest,
with a confirmatory number within reach, grading was stopped and the integrity call handed to
someone independent rather than shipping a result the apparatus hadn't earned.

## About the numbers in the demo

The [`RUNNING.md`](RUNNING.md) walkthrough prints an `H1 ... outcome=CONFIRMED` line. That is
**synthetic** — the analysis tool fabricates a dataset to demonstrate the estimator and the
decision rule are wired correctly. It is **not** a finding about real agents and must not be
read as one. (Run `--scenario refuted` to watch the same pipeline correctly decline to
confirm.)

---

*Provenance:* this narrative condenses the primary integrity finding recorded in the study's
deviations log, preserved privately (see [`dev-history/`](dev-history/)).
