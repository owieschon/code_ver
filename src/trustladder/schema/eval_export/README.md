# Inspect-compatible .eval export — TrustLadder Stage 1 (Amendment J)

Authority: PREREGISTRATION_v3_trustladder.md Sec. 5 "TOOLING INTEROP"
(Amendment J, adopted) + DISPATCH deliverable 8 + ARCHITECTURE.md Secs. 1–2.

## Standing rules (BINDING)
- The native JSON run record (`records/<run_id>.json`) is CANONICAL. The
  `.eval` is a derivative view for the community-standard Inspect viewer and
  ControlArena trajectory tooling. Nothing in the harness reads `.eval` back;
  analysis consumes native records only.
- The subject is NEVER orchestrated by Inspect (Amendment J first BINDING
  clause); this is a post-hoc export of a native Claude Code run.
- Unconvertible fields carry null + reason code IN THE EXPORT:
  `eval.metadata.trustladder_export.null_reasons` uses the same enum as the
  native schema (`schema/reason_codes.json`); `EVAL_CONVERSION_LOSS` marks
  export-only losses. Dropped transcript event types are counted per-type in
  `unconverted_event_types`.
- Zero net information loss: the full canonical record is embedded verbatim
  at `eval.metadata.trustladder_record`; sha256 of source record, source
  transcript, and the dispatch prompt are recorded in `trustladder_export`.
- OAP export is PRE-NAMED ONLY (post-launch) — not built here.

## Files
- `export_eval.py`  converter: canonical record (+ its transcript_ref) →
  `records/<run_id>.eval`. Stdlib-only, deterministic (fixed zip timestamps,
  ZIP_STORED, derived ids), no network. Refuses (writes nothing) on: invalid
  native record, missing transcript, transcript/record session mismatch,
  malformed transcript line. Called by `runner/dispatch.py` right after every
  record emission (production lane); also runnable standalone to regenerate.
- `verify_export.py` fidelity check V1–V8 (members, identity, embedded-record
  hash, null+reason convention, usage totals, claim text, summaries,
  byte-determinism via re-export).

## Target format
Inspect `.eval` zip log, header `version: 2`, characterized against
inspect-ai **0.3.239** (reference produced with the library's own
`write_eval_log`, then mirrored byte-shape-for-shape; the shipped export is
read back by `read_eval_log` and served by `inspect view` in the receipts).
Members: `_journal/start.json`, `samples/<task_id>_epoch_1.json`,
`_journal/summaries/1.json`, `summaries.json`, `header.json`.

## Field mapping (native → .eval)
| native record | .eval position |
|---|---|
| run_id | eval.run_id (eval_id derived: sha256(run_id)) |
| task_id | eval.task_id; eval.task = `trustladder_<task_id>`; sample.id |
| arm / stratum / family / batch | eval.task_args + eval.tags + sample.metadata |
| model_id | eval.model (`anthropic/` prefixed); output.model; model_usage key |
| started_at / ended_at | eval.created; stats.started_at / completed_at |
| transcript_ref (assistant events) | ChatMessageAssistant (thinking→reasoning block, text→text block, tool_use→tool_calls) |
| transcript_ref (user tool_result events) | ChatMessageTool (tool_call_id, function joined from pending tool_calls) |
| transcript result event | sample.output (completion, stop_reason map, usage) + model_usage |
| runner/prompts/STANDARD_PROMPT.txt ('#' lines stripped, sha256 recorded) | sample.input + first message |
| subject_exit_code | status (`success` / `error`) |
| verdict (when graded) | sample.scores.m0_complete + header results |
| cli_version | plan step `claude_code_native_subject` params |
| ENTIRE RECORD | eval.metadata.trustladder_record (verbatim) |

Unconvertible (standing): `eval.config.message_limit` (turn budget is turns,
not messages), `samples[0].target` (answer keys sealed in battery/keys/),
`samples[0].events` (Inspect's internal event model not synthesized — the
native transcript is canonical for events); plus per-run: system/hook/
thinking_tokens/rate_limit transcript events, `samples[0].scores` + `results`
while verdict is PENDING_GRADING, `samples[0].output` when no result event
exists (dry-run lane).

Dummy-lane tolerance: `batch="dummy"` records validate with exactly the
runner's tolerance, single-sourced at `emit_record.DUMMY_EXPECTED_ERRORS`.

## Receipts (harness/receipts/)
- `eval_export_red_refusal_lanes.txt` — 4 refusal lanes demonstrated red
- `eval_export_red_verify_tamper.txt` — 4 tampered exports caught (V1/V3/V4/V6+V8)
- `eval_export_green_pilot_record.txt` — pilot dummy record exported, V1–V8
  green, read back by inspect-ai 0.3.239
- `eval_export_green_graded_lane.txt` — synthetic graded verdict → scores/results
  lane live
- `eval_export_green_dispatch_wiring.txt` — runner auto-export demonstrated via
  dry-run lane (incl. found-red on the degenerate-record verifier paths)
- `eval_export_green_inspect_viewer.txt` — headless `inspect view` serves the
  export (log listing + full log load through the viewer's loader)
- `eval_export_livepath_grep.txt` — live-path greps
