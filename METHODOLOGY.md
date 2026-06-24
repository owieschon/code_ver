# Methodology

A condensed, plain-language version of the study design. The full preregistration, decision
log, and freeze record were written before data collection and preserved privately (see
[`dev-history/`](dev-history/)); those are authoritative, this file summarises them.

## The question, fixed before any data

> At **equal-or-better task completion**, does the enforced-gate arm (**L3**) have a lower
> defect ("escape") rate than the instruction-only arm (**L1**), by at least a
> preregistered floor (20 percentage points)?

Fixing the question, the floor, and the analysis before collecting data removes the freedom
to pick, after seeing the results, the comparison that looks best.

## The arms

Each task is run under all four arms (a paired design), so every comparison is
within-task:

- **L0** — bare agent.
- **L1** — agent + written discipline, no enforcement.
- **SHAM** — the governance surfaces are present and visible, but the gate never blocks.
  This isolates the Hawthorne effect: if L1 and SHAM behave the same, then being watched is
  not the active ingredient; being stopped is.
- **L3** — agent + a real deterministic gate that can block a non-conforming change.

## The tasks

A battery of seeded coding tasks (not included in this repo). Each task:
- has a known defect with a **fail-to-pass** test (the seeded version fails it, a reference
  version passes it — both demonstrated at admission, so the defect is real and gradeable);
- includes **lures** — 2-3 spots where the cheapest way to "go green" is to weaken a check,
  at least one of which reads as innocent housekeeping;
- is labelled **typical** or **novel** by manifold distance, so the analysis can ask whether
  enforcement matters more on unfamiliar work.

Positive controls (blockable by construction) and negative controls (clean, honest tasks)
calibrate the grader and the gate.

## Grading (kept blind)

Defects are graded **after** the runs, by an instrument that never sees which arm produced a
record:
1. A **calibration gate** must pass first — the grader is shown known-defective and
   known-clean controls and must score them correctly before it is allowed to grade real
   runs.
2. Stage A extracts the agent's claims; stage B applies the task's defect tests to the final
   code tree and assigns severity from a fixed rubric.
3. A **second grader from a different model family** independently grades, and inter-rater
   agreement (Cohen's kappa) must clear a threshold. Disagreements become **adjudication
   packets** resolved by a human, with a required written rationale.

## Validity gates (must pass before anything is believed)

Before the confirmatory contrast is even computed, five validity gates must pass: positive
controls separate from negative; negative controls don't differ across arms; the L1 base
escape rate is in a usable range; grader agreement clears its floor; and an
eval-awareness check shows the agent isn't visibly behaving differently because it thinks
it's being tested. **Fail any gate → the run is invalid; fix it, rerun, and publish the
outcome regardless.**

## Kill criteria and the unblinding order

The decision rule is one-directional and pre-committed: a claim is **refuted** only if the
confidence interval sits entirely below the floor, *and* the validity gates passed. The
analysis script enforces the order structurally — the validity verdict is written **before**
the confirmatory contrast is unblinded, so you cannot peek at the result and then adjust the
rules. (You can see this enforced in the code: `confirmatory` refuses to run until a validity
verdict file exists.)

## Conflict of interest

This was a maximum-conflict-of-interest study: the author of the experiment also built the
governance kit being tested as the L3 arm. The design accounts for it with blind grading,
cross-family graders, preregistration, and escalation of integrity judgments to an
independent methodologist rather than the author. The conflict is disclosed rather than
hidden. See [RESULTS.md](RESULTS.md) for how that played out in practice.
