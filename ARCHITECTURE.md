# Architecture

TrustLadder is split into a **live layer** that produces evidence and a **measurement
layer** that consumes it. They are connected by exactly one thing: a frozen JSON contract
for the run-record. There is almost no shared code across the boundary — each stage reads
files the previous stage wrote. This repository contains the measurement layer.

```
            LIVE LAYER (not in this repo)              MEASUREMENT LAYER (this repo)
   ┌───────────────────────────────────────┐   ┌──────────────────────────────────────────┐
   │  battery ─▶ runner ─▶ agent ─▶ record  │   │  ledger ─▶ grading ─▶ packets ─▶ analysis │
   │  (tasks)   (assign,  (isolated  (signed │   │ (violations  (blind    (adjudi-  (validity│
   │            dispatch)  worktree)  JSON)  │   │  + contam.)   grader)   cation)   → H1 →  │
   └───────────────────────────────────────┘   │                                  readouts)│
                       │                         └──────────────────────────────────────────┘
                       └──────── signed run-record (the only contract) ───────────▲
```

## The contract: one signed run-record per run

Everything downstream keys off a single canonical JSON document per run
(`src/trustladder/schema/run_record.schema.json`). It carries:

- **Identity** (immutable): `run_id`, `task_id`, `arm`, `stratum`, `model_id`,
  `cli_version`, `tree_hash`, transcript pointer, timestamps, turn budget.
- **Claim & evidence**: what the agent said it did, gate decisions, the policy
  fingerprint and proof reference, costs.
- **Verdict** (filled in later by grading): the defect verdict, grader provenance,
  disagreement record.
- **Receipt**: `record_hash`, `prev_record_hash`, `signature`, `signer_key_id`.

**Why late grading does not break the signature:** the `record_hash` is computed over
the *as-dispatched* record with the four receipt fields **and** the three grading-mutable
fields excluded (`src/trustladder/schema/signing/receipt.py`). So grading can merge a verdict into
the record afterwards without invalidating the signature or breaking the hash chain. The
record proves *what the agent did*; the verdict is appended without rewriting history.

Null convention: any field that is null or below a floor must carry a reason code from a
fixed enum (`src/trustladder/schema/reason_codes.json`), or validation refuses the record.
A missing value is never silent.

## Components (this repo)

### `src/trustladder/schema/` — the foundation
No intra-project dependencies; everything else builds on it.
- `emit_record.py` — build, validate (a hand-rolled JSON-Schema check, plus the
  `jsonschema` library if present), and write a run-record.
- `derive.py`, `telemetry.py` — derived cost columns and the flat telemetry event stream
  (same validator as the embedded cost events, so they can't drift apart).
- `signing/` — Ed25519 keygen, the receipt hash-chain, and chain verification.
  `cryptography` is optional; there is an `openssl` CLI fallback.
- `eval_export/` — export each record to the `.eval` format with the canonical record
  embedded and re-verifiable.

### `src/trustladder/analysis/` — the frozen statistical pipeline
The entry point (`analysis.py`) runs a fixed order: validity gates, confirmatory contrast,
exploratory secondaries, consistency check, readouts. The order is fixed so the validity
verdict is computed and written before the confirmatory contrast is unblinded, so you can't
peek and then decide the rules. The package is split by responsibility:
- `stats.py` — the statistics (Cohen's kappa, BCa / Wilson / Newcombe intervals), hand-written
  on the standard library and unit-tested directly (`tests/test_stats.py`).
- `dummy.py` — the synthetic-data generator (the `dummy` subcommand) that fabricates a
  workspace for the offline demo and tests.
- `_model.py` — the few constants and helpers both of the above share.
- `sql_report.py` + `sql/*.sql` — the descriptive reporting layer; the queries live in their
  own `.sql` files.

### `src/trustladder/grading/` — the blind defect grader
A two-stage instrument behind a calibration gate. Stage A extracts the agent's claims from
the transcript; stage B applies the per-task defect tests to the *terminal* code tree and
assigns severity from a fixed rubric. A blindness fence (`stage_b_loader.py`) keeps the arm
label away from the grader. A second, cross-family grader and an inter-rater agreement
(kappa) check guard against a single grader's bias.

### `src/trustladder/ledger/` — out-of-band violation accounting
Post-hoc, separate from grading: classifies policy violations and detects test
contamination (an agent weakening or deleting the very checks meant to catch it). Kept
out-of-band so it can't influence the primary defect verdict.

### `src/trustladder/packets/` — adjudication
When two graders disagree, `gen_packet.py` assembles a self-contained packet for human
adjudication — the rationale is required, the verdict is human, AI is allowed only for
comprehension.

## Dependency shape

`schema/` ← (everything). `analysis/` reads the records and ledger outputs and imports only
`schema/` helpers; it never imports the runner, grader, or ledger code and never reads raw
transcripts. `grading/` and `ledger/` read the live-layer outputs and write files
`analysis/` later consumes. The glue is the field-name contract above, not shared mutable
state, so the layers can be developed and audited independently.

The package's cross-module `sys.path` hacks are gone; imports are ordinary package imports.
(The only remaining `sys.path.insert` calls are inside `demo/fixture/`, where the synthetic
task code and standalone answer-key script load the target tree by path on purpose — that is
how a real external task repo behaves, not package wiring.)

## Observability

This is a deterministic, offline tool with no LLM calls and no deployed service in the
shipped code, so the usual application-monitoring stack does not apply here, and bolting it on
would be scaffolding with nothing to watch:
- **No Sentry.** There is no long-running service to monitor, and Sentry would add a network
  call and a DSN to a tool whose whole posture is "runs offline, no secrets, no network."
- **No Phoenix / LangSmith / LangGraph tracing.** Those trace LLM and agent calls. The shipped
  code makes none — the live agent dispatch and the LLM classification lane are the excluded
  layer. That is where call-tracing belongs, not here.

What this engine actually gives you for diagnosing a run after the fact:
- **The signed, hash-chained run-record is the audit trail.** Each run is reconstructable from
  its record plus the `transcript_ref` it points at; the chain proves nothing was altered.
- **The `.eval` export** (`schema/eval_export/`) renders records and transcripts in the
  community Inspect viewer (ControlArena-compatible), so a run is inspectable in standard tooling.
- **Fail-closed, named refusals.** Errors are surfaced, not swallowed: a gate that can't pass
  prints which rule it violated (`[AP6 REFUSAL]`, `[CONFIG REFUSAL]`, the missing-prereg
  refusal) on stderr with a distinct exit code, rather than a traceback or a silent wrong answer.
- **`trustladder-mini-run`** narrates each stage and verifies the chain, so the whole
  produce → grade → aggregate path is observable in one command.
