# grading/ — two-stage grading harness (deliverable 9)

Authority: PREREGISTRATION_v3_trustladder.md Sec. 6 (G1–G5, training-recall
detection), Sec. 7 (M1 threshold), Sec. 9 (AP7) · ARCHITECTURE.md Secs. 5–6.
Python 3, stdlib only, deterministic, no network. Demonstrated-red receipts:
`harness/receipts/grading_red_0*.txt`.

## Two stages, structurally separated (G1)

STAGE A — claims extraction (`extract_claims.py`). The ONLY pass that holds
the transcript. Emits a structured claims table (claimed-vs-did) to
`claims/<run_id>.json` and runs the transcript half of the canary/recall
scan (flags → adjudication queue). Deterministic regex layer; the post-hoc
grading session refines rows at experiment time — the file contract is
fixed here.

STAGE B — defect grading, blind. Input is a closed-contract JSON
(`stage_b_loader.py`): `run_id, task_id, terminal_tree_path` (+ optional
`tree_hash`, `claims_table_ref` [a pointer, never content],
`claimed_done`, `battery_root`). The loader REFUSES transcript text fields,
arm metadata (keys or bare L0/L1/SHAM/L3 values, any depth),
transcript-shaped strings, and unknown top-level keys. `grade_defects.py`
applies `battery/keys/<id>/key_tests.py` to the terminal tree only;
`severity_rubric.py` encodes the G4 table (SEV1–SEV4) and the M1 threshold
(counts severity >= SEV2). `terminal_tree_path` may be the terminal repo or
a worktree root (`repo/` + `TASK.md`): the repo is graded, the whole tree
recall-scanned.

## G2 calibration gate

`build_calibration.py` derives the set from battery materials — 3
known-defective (seeded containers as shipped, scratch copies: t01/t09/t11;
family-c containers are key-green as shipped, so defective artifacts come
from families a/b) + 3 known-clean (reference applied AS A PATCH with
canary lines stripped: t01/t09/t18). `grade_calibration.py` must show red
on all 3 defective and green on all 3 clean; writes
`calibration/receipt.json` (incl. instrument + key hashes and the
calibration-cost line item, prereg 7A). `grade_run.py` consumes that
receipt as a state-file gate and refuses experimental grading when it is
missing, FAILed, or hash-stale (instrument/keys changed since calibration
=> regrade from zero).

## Experimental grading

```
python3 grade_run.py --input <grading_input.json> [--record records/<run_id>.json]
```
Order: G2 gate → blindness fence → answer key on terminal tree → recall
scan → verdict to `verdicts/<run_id>.json` (+ optional merge of `verdict`
and `grader_provenance` into the run record, clearing PENDING_GRADING).

## Training-recall signal (Amendment K)

`recall_scan.py`: registered canaries (proven leakage) + workspace-
underivable tells (CVE/CWE/GHSA, upstream fix/patch/commit, backport,
cherry-pick, commit URLs, advisories, RFC refs, bare 40-hex hashes) +
`known_fix_patterns.json` heuristics (extension by logged revision).
Verified zero false positives across all 30 shipped containers. Flags
route to `adjudication/queue.jsonl` (G5) — never auto-exclude. Confirmed
rulings: `record_recall_ruling.py` upserts the flag into
`ledger/adjudication_queue.jsonl` (deliverable 7's row schema) and
invokes `ledger/ap7_routing.py route` — the SINGLE canonical writer of
`ledger/exclusions.json` (AP7 contract: excluded from M0/M1 denominators
AND counted in M3/PROVENANCE; the frozen analysis script enforces the
routing).

## Dry-run artifacts

`dryrun/` holds the end-to-end demonstration (synthetic transcript →
claims table → graded verdict → record merge → AP7 wire log). `claims/`
and `verdicts/` stay empty until experimental grading.
