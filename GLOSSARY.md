# Glossary

Plain definitions for the terms used across this repo. (The original study used more
shorthand; this is the outsider-facing version.)

## The arms (the "ladder")
- **L0** — bare agent: the task and nothing else.
- **L1** — instructed agent: the task plus written rules (a `CLAUDE.md`), with **no**
  enforcement.
- **SHAM** — placebo: the governance machinery is present and visible, but the gate never
  actually blocks. Controls for the "being watched" effect.
- **L3** — governed agent: the task plus a **real deterministic gate** that can block a
  non-conforming change.

## Measurement
- **Escape** — a shipped defect at severity ≥ S2; the primary thing being counted.
- **Completion** — whether the agent produced a plausibly-finished result, held equal across
  arms so escape-rate comparisons are fair.
- **Run-record** — the canonical signed JSON document for one run; the single contract
  between the live layer and the analysis layer.
- **Tree hash** — a hash of the final code tree the agent left behind; signed into the
  record so the graded artifact can't be swapped.

## Tasks
- **Fail-to-pass (F2P)** — a seeded defect proven real at admission: the broken version fails
  a named test, the reference version passes it.
- **Lure** — a spot in a task where the cheapest way to "go green" is to weaken a check;
  at least one is disguised as innocent housekeeping.
- **Typical / Novel** — how far a task sits from familiar territory; lets the analysis ask
  whether enforcement matters more on unfamiliar work.
- **Positive / negative control** — a task blockable by construction / a clean honest task;
  together they calibrate the grader and the gate.

## Grading & analysis
- **Calibration gate** — the grader must score known-defective and known-clean controls
  correctly *before* it is trusted to grade real runs.
- **Blind grading** — the grader never sees which arm produced a record.
- **Cross-family grader** — a second grader from a different model family; their agreement
  (Cohen's **kappa**) must clear a threshold.
- **Validity gates** — five checks (controls behaving, base rate in range, grader agreement,
  eval-awareness parity) that must all pass before any result is believed.
- **Confirmatory contrast** — the one pre-registered comparison (L3 vs L1 escape rate at
  equal completion) that the study was built to make.
- **Unblinding order** — the rule that the validity verdict is computed and written *before*
  the confirmatory contrast is revealed, so the rules can't be tuned to the result.

## Statistics
- **BCa / Newcombe / Wilson interval** — confidence-interval methods used for the rate
  difference; computed on the standard library, no dependencies.
- **Floor** — the preregistered minimum effect size (20 percentage points) that L3 must beat
  L1 by for the confirmatory claim to hold.

## Integrity
- **Conflict of interest (COI)** — here, the author of the study also built the governance
  kit under test; handled openly via blinding, preregistration, and an independent
  methodologist (see [RESULTS.md](RESULTS.md)).
- **Independent methodologist** — an external reviewer who ruled on grading-path
  admissibility, kept separate from the author to protect the result.
