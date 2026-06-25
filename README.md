# TrustLadder

You can tell an AI coding agent the rules in a `CLAUDE.md`. You can let it know it's being watched. Or you can put a real gate in front of it that can actually stop a non-conforming change. The open question is which of those, if any, lowers the rate of defects the agent ships — and whether "being watched" alone does anything, or whether it has to be "being stopped." **TrustLadder is the preregistered experiment apparatus built to measure that**, by running the same seeded tasks under four governance arms and counting defects at equal task completion.

The stakes are why the engineering looks the way it does. A "we measured it and enforcement works" result that is actually fabricated is worse than no result: it tells every team downstream to lean on a guardrail that may not hold. This study is the maximum-conflict-of-interest version of the question — the author of the experiment also built the governance kit under test as one of the arms — so the measurement engine is built to **refuse to produce a confirmatory number until a validity check has actually passed**. It fails closed against its own author, and it did:

> Stage 1 produced **no confirmatory result**. The freeze had validated every component in isolation but never run one real record through the full produce → grade → analyze chain. The first end-to-end pass surfaced three wiring defects in a row — culminating in **D3**, where the runner and the grader hashed the final code tree with incompatible algorithms and the integrity guard refused 100% of real records. With a confirmatory number within reach, grading was **halted** and the integrity call handed to an independent methodologist, rather than hand-assembling a result around a broken pipeline. The whole account is [RESULTS.md](RESULTS.md).

This repo is the measurement-and-analysis half of that apparatus. The live layer that runs a real agent against the task battery is deliberately excluded.

## The design

Each seeded task is run under all four arms (a paired, within-task design), and the analysis asks one preregistered question: at **equal-or-better completion, does the enforced-gate arm escape less than the instruction-only arm, by at least a 20-percentage-point floor?**

- **L0** — bare agent: the task and nothing else.
- **L1** — instructed agent: the task plus a written `CLAUDE.md`, no enforcement.
- **SHAM** — placebo: the governance surfaces are present and visible, but the gate never blocks. Isolates the Hawthorne effect — if L1 and SHAM behave the same, being *watched* isn't the active ingredient; being *stopped* is.
- **L3** — governed agent: a real deterministic gate that can block a non-conforming change.

An **escape** is a shipped defect at severity ≥ S2 — the thing being counted. ([GLOSSARY.md](GLOSSARY.md) defines the vocabulary; [METHODOLOGY.md](METHODOLOGY.md) is the condensed study design.)

```
        LIVE LAYER (not in this repo)            MEASUREMENT LAYER (this repo)
   battery ─▶ runner ─▶ agent ─▶ record  │  ledger ─▶ grading ─▶ packets ─▶ analysis
   (tasks)   (dispatch) (worktree) (signed│  (violations (blind   (adjudi-  (validity
                                    JSON)  │   + contam.)  grader)  cation)   → H1 → readouts)
                            │              │
                            └── signed run-record: the only contract ──────────▲
```

The two halves share no mutable state — only one frozen JSON document per run, signed and hash-chained, that the live layer writes and the measurement layer reads. That contract is what lets the measurement engine be published and audited on its own.

## What's in this repo

The offline engine — **standard-library-only Python, no network, no secrets**. The one optional dependency, `cryptography`, falls back to the `openssl` CLI, so nothing here is required to run the analysis. It runs end-to-end on a committed synthetic fixture:

```bash
pip install -e .                              # Python 3.10+, zero runtime deps
trustladder-mini-run --workspace /tmp/mini    # sign → grade behind the calibration gate
                                              # → merge verdict → verify chain → aggregate
```

That drives the whole chain on a *stub* agent (it leaves either the defective or the reference terminal tree from the demo fixture, then runs the real machinery over those runs): it signs a hash-chained run-record, grades the terminal tree blind behind a calibration gate, merges the verdict back into the signed record — the signature still verifies, because the grading-mutable fields are excluded from the record hash — and aggregates per-arm escape rates in stdlib `sqlite3`. The tail recovers the study's hypothesis shape from real grading on synthetic runs:

```
   arm   n   completion%   escape%
   L0    8   100.0         87.5
   L1    8   100.0         75.0
   SHAM  8   100.0         75.0
   L3    8   100.0         12.5
```

An illustration that the pipeline is wired correctly — **not a result about real agents.**

The load-bearing pieces, all hand-rolled:

- **Statistics with no numpy/scipy** (`src/trustladder/analysis/stats.py`): BCa bootstrap (Efron 1987), Newcombe (1998) MOVER-Wilson paired interval, Wilson score, Acklam inverse-normal, Cohen's kappa, and a three-outcome decision rule against the fixed floor. Unit-tested directly.
- **Signed, append-only run-records** (`src/trustladder/schema/`): a hand-rolled JSON-Schema validator, Ed25519 receipts with a prev-hash chain, and a null convention where any missing value must carry a reason code or validation refuses the record.
- **A blind, calibration-gated grader** (`src/trustladder/grading/`): the instrument must score known-defective controls RED and known-clean GREEN *before* it's trusted to grade real runs; a blindness fence keeps the arm label away from it, and verdicts record `blind_to_arm: true`.
- **The structural unblinding order** (`src/trustladder/analysis/analysis.py`): the confirmatory contrast *cannot* execute until a `validity_verdict.json` reading `status=VALID` exists on disk — its first act is `_require_valid_verdict(workspace)`. You can't peek at the result and then tune the rules; the AP6 refusal is the gate that failed closed in Stage 1.
- **A regression test for the D3 defect** (`tests/test_grading_seam.py`): reproduces the exact record-to-grade hashing seam the original freeze never exercised.

Suite: **29 test functions across 5 files**, ruff-linted, CI on Python 3.10 and 3.12.

## What's deliberately not here

The live agent-dispatch layer (the `runner/` and the L3 / SHAM arm assembly) and the seeded task battery are excluded — they shell out to a private, unpublished governance kit, and publishing the answer keys would spoil the benchmark. `ledger/` and `packets/` *are* included so the full measurement design is readable, though (unlike grading) they aren't exercised end-to-end. [ARCHITECTURE.md](ARCHITECTURE.md) is the component map.

The 72 real subject runs and the independent-methodologist ruling are **private and not reproducible from this checkout** (see [RESULTS.md](RESULTS.md)).

One honest note: this is a research prototype, and the demo's `outcome=CONFIRMED` line is **synthetic** — a planted dataset (`src/trustladder/analysis/dummy.py`) that proves the estimator and decision rule are wired correctly, not a finding. Run `trustladder-analyze dummy --scenario refuted` to watch the same pipeline correctly decline to confirm. [RUNNING.md](RUNNING.md) walks every command. Licensed [Apache-2.0](LICENSE).
