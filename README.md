# TrustLadder

**Does forcing an AI coding agent through a real enforcement gate actually make it safer, or
is telling it the rules (or just watching it) good enough?**

TrustLadder is a preregistered experiment. It runs the *same* coding tasks under four
levels of governance and measures how often each one ships a defect, holding
task-completion equal.

> **Status: research prototype.** This is an experiment apparatus, not a product. The
> analysis and record-signing pipeline run end-to-end on synthetic data with no secrets
> and no network (see [RUNNING.md](RUNNING.md)). The live experiment (dispatching a real
> agent against the task battery) is described here but is **not** runnable from this
> repository; it depends on a private governance kit and a task battery that are not
> included (see [What's here vs what isn't](#whats-here-vs-what-isnt)).

*Not here for the statistics?* The short version of why this project exists, and the
judgment call at the center of it, is in [RESULTS.md](RESULTS.md) — about a two-minute read.

---

## The four arms of the ladder

| Arm | What the agent gets | What it controls for |
|-----|---------------------|----------------------|
| **L0** | The task, nothing else | Baseline behaviour |
| **L1** | The task + written rules (a `CLAUDE.md`), **no enforcement** | Does *instruction* alone work? |
| **SHAM** | The governance machinery is visibly present but the gate **never blocks** | The "being watched" (Hawthorne) effect |
| **L3** | The task + a **real deterministic gate** that can actually block a bad change | Does *enforcement* add anything over the above? |

The confirmatory question is narrow and was fixed before any data was collected:
**at equal-or-better task completion, does L3's defect ("escape") rate beat L1's by at
least a preregistered margin?** Comparing SHAM against L1 and L3 separates *being
watched* from *being stopped*.

## How a run flows

```
 task battery ──▶ runner ──▶ AI agent in an ──▶ signed JSON ──▶ blind grading ──▶ statistical
 (seeded         (assigns    isolated git        run-record      instrument        analysis
  defects +       arm, frozen  worktree)         (hash-chained)  (calibrated,      (validity gates →
  lures)          prompt)                                         cross-checked)     confirmatory → readouts)
                  └─────────────── live layer (not in this repo) ──────────────┘   └──── this repo ────┘
```

The graded artifact is a signed, frozen record of what the agent did, and the grader
never sees which arm produced it. Grading-mutable fields are excluded from the record's
signature hash, so a verdict can be merged in later without invalidating the signature.

## What's here vs what isn't

In this repository (the measurement and analysis engine, runs offline):
- `src/trustladder/schema/` — the canonical run-record: build, validate, and Ed25519-sign it; hash-chained receipts; `.eval` export.
- `src/trustladder/analysis/` — the frozen statistical pipeline: validity gates, confirmatory contrast, exploratory readouts.
- `src/trustladder/grading/` — the blind, calibration-gated defect grader (two-stage, severity-rubric driven).
- `src/trustladder/ledger/` — post-hoc, out-of-band classification of policy violations and test contamination.
- `src/trustladder/packets/` — adjudication packets generated when two graders disagree.

Excluded (see [`dev-history/`](dev-history/) for why):
- The live dispatch layer (`runner/`, `arms/`) shells out to a private governance kit, so it can't run for a stranger and would republish another project.
- The task battery: the seeded task repos are git submodule links with no public source, and the answer keys (reference solutions, planted "canaries") are withheld so the benchmark isn't spoiled.
- Raw agent transcripts and recorded run data, which carry machine-specific paths and session identifiers.

## What this demonstrates

Stage 1 did not produce a confirmatory result, and the reason is the interesting part
(full version in [RESULTS.md](RESULTS.md)). The author also built the governance kit under
test, so this was a maximum-conflict-of-interest design. Before freezing, the apparatus
turned out to have validated each component in isolation without ever running one real
record through the full pipeline. Rather than hand-assemble the confirmatory path around
the broken apparatus, the author halted grading and escalated to an independent
methodologist.

The apparatus itself is runnable. `trustladder-mini-run` drives the whole chain on a stub
agent (sign a run-record, extract claims, grade behind the calibration gate, merge the
verdict, verify the hash chain, aggregate per-arm escape rates), and the aggregation recovers
the study's hypothesis from grading that actually happened. The record-to-grade seam that
defect D3 broke, where the runner and grader hashed the terminal tree with incompatible
algorithms, has its own focused test. So the central claim can be executed rather than just
described.

## Repository map

```
README.md          you are here
ARCHITECTURE.md    the components and how a run flows through them
METHODOLOGY.md     hypotheses, arms, grading, kill criteria, COI handling
RESULTS.md         what was (and wasn't) found, including the integrity catch
RUNNING.md         run the offline demo in ~2 minutes
GLOSSARY.md        the vocabulary, defined plainly
src/trustladder/   the measurement + analysis engine (Python, stdlib-only)
tests/             a small pytest suite over the offline pipeline
dev-history/       a provenance note (the full preregistration / decision log are kept private)
```

## Quick start

```bash
# Python 3.10+. No third-party dependencies for the demo.
pip install -e .

# The whole chain on a stub agent (sign -> grade -> aggregate):
trustladder-mini-run --workspace /tmp/mini

# Or run the statistical pipeline by itself on a synthetic dataset:
trustladder-analyze dummy        --workspace /tmp/tl --scenario confirmed
trustladder-analyze validity     --workspace /tmp/tl
trustladder-analyze confirmatory --workspace /tmp/tl
```

`trustladder-report --workspace /tmp/tl` renders per-arm completion and escape rates as SQL
(stdlib `sqlite3`) over the same records. See [RUNNING.md](RUNNING.md) for the full
walkthrough and what each step proves.

## License

[Apache License 2.0](LICENSE).
