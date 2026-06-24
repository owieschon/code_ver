# Running it

**Requirements:** Python 3.10+. No third-party packages for the offline demo. (Optional:
`cryptography` for real Ed25519 signing — there is an `openssl` fallback, so you don't need
it.)

Everything below runs with no secrets and no network, writing only to a workspace directory
you name.

## The whole chain end-to-end (one command)

The live study needs a real agent, a private governance kit, and the full task battery, none
of which ship here. `trustladder-mini-run` substitutes a stub agent (for each run it leaves
either the defective or the reference terminal tree from the demo fixture) and then runs the
real machinery over those runs:

```bash
pip install -e .
trustladder-mini-run --workspace /tmp/mini
```

For 32 runs across the four arms it signs a run-record, extracts a stage-A claims table,
grades the terminal tree behind the G2 calibration gate, merges the verdict back into the
signed record, verifies the whole hash chain, then aggregates the per-arm escape rate (in
SQL) from grading that actually happened. The tail looks like:

```
   arm   n   completion%   escape%
   L0    8   100.0         87.5
   L1    8   100.0         75.0
   SHAM  8   100.0         75.0
   L3    8   100.0         12.5
```

At equal completion the enforced gate (L3) escapes far less often than instruction-only
(L1), and the placebo (SHAM) tracks L1. That is the study's hypothesis, recovered from the
real grading path on synthetic runs. It is an illustration, not a result. `test_mini_pipeline.py`
asserts the shape.

Everything except the agent is the production code path. The sections below run the pieces on
their own.

## The statistical pipeline on synthetic data

To run the analysis pipeline by itself, the demo fabricates a synthetic dataset (the live
agent and task battery are not in this repo). This shows the pipeline works end-to-end. It is
not a scientific result (see [RESULTS.md](RESULTS.md)).

```bash
pip install -e .          # one-time; installs the `trustladder-analyze` command
WS=/tmp/trustladder-demo

# 1. Fabricate a synthetic set of 120 run-records + ledger + telemetry.
trustladder-analyze dummy --workspace "$WS" --scenario confirmed

# 2. Validity gates — are the positive/negative controls behaving, is the
#    grader agreement high enough, is the L1 base rate in range? (Must pass
#    before the confirmatory contrast is allowed to run.)
trustladder-analyze validity --workspace "$WS"

# 3. The confirmatory contrast: L3 escape rate vs L1, at equal completion.
trustladder-analyze confirmatory --workspace "$WS"

# 4. Exploratory secondaries + a cross-side consistency check.
trustladder-analyze secondaries --workspace "$WS"
trustladder-analyze ap7-check   --workspace "$WS"
```

Step 3 prints the headline line, e.g.:

```
[H1] [SYNTHETIC DEMO DATA — NOT A RESULT] effect=64.7pp  CI95%(BCa)=[35.3, 82.4]pp  floor=20pp (T-D1)
[H1] [SYNTHETIC DEMO DATA — NOT A RESULT] outcome=CONFIRMED  m0_noninferior=True  h1_confirmed=True
```

Those numbers come from the **synthetic** "confirmed" scenario — they demonstrate that the
estimator, the confidence intervals, and the decision rule are wired correctly. Rebuild the
workspace with `--scenario refuted` (instead of `confirmed`) to see the pipeline correctly
*not* confirm. Available scenarios: `confirmed`, `spans_floor`, `spans_extended`, `refuted`.

## The signing chain (optional)

The record signing + hash-chain verification also runs offline. It generates a throwaway
key in a temp directory, signs a few synthetic records, and verifies the chain:

```bash
pytest tests/                      # includes the signing round-trip
```

## A descriptive report, in SQL

The run-records and the violation ledger are relational, so the descriptive summaries are
written as SQL (stdlib `sqlite3`, no new dependency):

```bash
trustladder-analyze dummy --workspace "$WS" --scenario confirmed   # if not already done
trustladder-report --workspace "$WS"
```

It loads the nested records into `runs` / `escapes` / `violations` tables and prints per-arm
completion and escape rates, an arm-by-stratum breakdown, and a runs-to-violations join by
policy class. The queries live in their own files under
`src/trustladder/analysis/sql/` (`schema.sql`, `per_arm.sql`, `by_stratum.sql`,
`violations_by_class.sql`) — CTEs, named columns, comments on the business logic — and
`sql_report.py` loads them.

SQL handles the descriptive aggregation; the inferential statistics (the BCa bootstrap, the
Newcombe interval, the confirmatory decision) cannot be expressed in SQL and stay in Python
(`stats.py`). `test_sql_report.py` checks the SQL aggregates against an independent Python
computation, so the SQL stays in step with the metric definitions rather than drifting into
a second source of truth.

## The grading seam, on one fixture

`test_grading_seam.py` isolates the record-to-grade seam that defect D3 proved had never run
end-to-end before the study's freeze (see [RESULTS.md](RESULTS.md)). It runs on the same
self-contained fixture (`src/trustladder/demo/fixture/`) and walks the real path:

1. G2 calibration: the instrument shows RED on the seeded defect and GREEN on the reference
   before any grading is allowed (the gate that shipped a placeholder receipt at freeze).
2. Stage A: extract a claims table from the run transcript (claims before grading).
3. Stage B: `grade_run` grades the terminal tree behind the G2 gate and merges the verdict
   back into the signed record.
4. The record's hash excludes the grading-mutable fields, so the chain still verifies after
   the verdict is added.

```bash
pytest tests/test_grading_seam.py -q
```

## What you still can't run here, and why

| Step | Needs | Why it's not in this repo |
|------|-------|---------------------------|
| Live agent dispatch (`runner/`) | the `claude` CLI + an account | costs money; not the point of the demo |
| L3 / SHAM arm assembly (`arms/`) | a private governance kit | it's a separate, unpublished project |
| Grading on the *real* battery | the withheld task answer keys | publishing them would spoil the benchmark |

`ledger/` and `packets/` are included so you can read the full measurement design, but
(unlike grading) are not exercised end-to-end here.

## Running the tests

```bash
pytest                # or: python3 -m pytest
```

The suite covers the offline pipeline (schema validation, the signing round-trip, a
confirmatory/refuted analysis run, the statistics library), the grading seam, and the
end-to-end mini-run. It does not test the live dispatch layer, which is not present.

## Command reference

| Command | What it does | Needs the private prereg? |
|---------|--------------|---------------------------|
| `trustladder-mini-run` | the whole chain on a stub agent | no |
| `trustladder-analyze dummy` | fabricate a synthetic workspace | no |
| `trustladder-analyze validity` | run the validity gates | no |
| `trustladder-analyze confirmatory` | the L3-vs-L1 contrast | no |
| `trustladder-analyze secondaries` | exploratory tables | no |
| `trustladder-analyze ap7-check` | both-sides exclusion consistency | no |
| `trustladder-analyze all` | the full pipeline (skips readouts) | no (readouts step skipped) |
| `trustladder-analyze readouts` / `verify-verbatim` | check/emit frozen readout templates | **yes** — refuses cleanly without it |
| `trustladder-report` | descriptive per-arm tables in SQL | no |

`readouts` and `verify-verbatim` need the registered preregistration, which is kept private
and not shipped; they refuse with an actionable message rather than crashing. See
[ARCHITECTURE.md](ARCHITECTURE.md#observability) for how a run is diagnosed after the fact.
