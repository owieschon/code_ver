#!/usr/bin/env python3
"""Inspect-compatible .eval export — TrustLadder Stage 1.

What this exporter guarantees:
  - The native JSON run record (records/<run_id>.json) is CANONICAL. This
    exporter produces a DERIVATIVE records/<run_id>.eval alongside it so
    transcripts are viewable in the community-standard Inspect viewer and
    consumable by ControlArena trajectory tooling. Nothing reads the .eval
    back into the harness; analysis consumes native records only.
  - Unconvertible fields carry null + reason code IN THE EXPORT: every
    mapped position that cannot be populated is registered in the export's
    trustladder_export.null_reasons block (eval.metadata), using the same
    reason-code enum as the native schema (schema/reason_codes.json), with
    EVAL_CONVERSION_LOSS for export-only losses. Dropped transcript event
    types are counted per-type under unconverted_event_types.
  - Zero information loss overall: the full canonical record is embedded
    verbatim at eval.metadata.trustladder_record, and the export records
    sha256 hashes of the source record + transcript.

TARGET FORMAT: Inspect .eval zip log, header version 2, characterized
against inspect-ai 0.3.239 (write_eval_log reference output). Writer is
stdlib-only and deterministic: fixed zip timestamps, ZIP_STORED (no
zlib-version dependence), derived message ids, compact separators. No
network. Builders/exporters never run any arm or subject-model session —
this script only reads files.

Usage:
  python3 export_eval.py <record.json> [--out <path.eval>]
Default output: same directory and basename as the record, .eval suffix
(records/<run_id>.eval alongside the canonical record).
"""

import argparse
import hashlib
import json
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_DIR = os.path.dirname(HERE)
HARNESS_DIR = os.path.dirname(SCHEMA_DIR)
from trustladder.schema import emit_record

FORMAT_ID = "trustladder.eval_export.v1"
EVAL_LOG_VERSION = 2  # Inspect .eval header "version", per inspect-ai 0.3.239
TARGET_INSPECT_AI = "0.3.239"  # version the format was characterized against
PROMPT_PATH = os.path.join(HARNESS_DIR, "runner", "prompts", "STANDARD_PROMPT.txt")
DATASET_NAME = "trustladder-stage1-battery"

# Anthropic stop_reason -> Inspect StopReason literal
# (inspect_ai.model._model_output.StopReason, 0.3.239)
STOP_REASON_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "max_tokens",
    "tool_use": "tool_calls",
}

# Fixed zip member timestamp: determinism only; real times live in the record.
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)


class ExportError(Exception):
    """Refusal: the export cannot be produced faithfully. Nothing is written."""


# ---------------------------------------------------------------- helpers

def _jdump(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _det_id(*parts):
    """Deterministic message id (replaces inspect-ai's random shortuuid)."""
    return "tl" + hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:22]


def read_prompt(path=PROMPT_PATH):
    """Mirror runner/dispatch.py read_prompt(): '#' comment lines stripped."""
    lines = open(path).read().splitlines()
    return "\n".join(ln for ln in lines if not ln.startswith("#")).strip()


def load_transcript(path):
    events = []
    with open(path) as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError as e:
                raise ExportError(
                    "MALFORMED TRANSCRIPT LINE %d in %s: %s — refusing to export "
                    "(a partial conversion would misrepresent the run)" % (n, path, e)
                )
    if not events:
        raise ExportError("EMPTY TRANSCRIPT: %s" % path)
    return events


# ------------------------------------------------- transcript -> messages

def _convert_assistant(event, idx, toolcall_fns, losses):
    msg = event["message"]
    content = []
    tool_calls = []
    for block in msg.get("content") or []:
        btype = block.get("type")
        if btype == "thinking":
            content.append({"type": "reasoning",
                            "reasoning": block.get("thinking", ""),
                            "redacted": False})
        elif btype == "text":
            content.append({"type": "text", "text": block.get("text", "")})
        elif btype == "tool_use":
            toolcall_fns[block["id"]] = block.get("name", "")
            tool_calls.append({"id": block["id"],
                               "function": block.get("name", ""),
                               "arguments": block.get("input", {}),
                               "type": "function"})
        else:
            losses["assistant.content.%s" % btype] = losses.get(
                "assistant.content.%s" % btype, 0) + 1
    out = {"id": msg.get("id") or _det_id("assistant", idx),
           "content": content,
           "role": "assistant"}
    if tool_calls:
        out["tool_calls"] = tool_calls
    if msg.get("model"):
        out["model"] = msg["model"]
    return out


def _tool_result_content(block):
    c = block.get("content", "")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for sub in c:
            if isinstance(sub, dict) and sub.get("type") == "text":
                parts.append({"type": "text", "text": sub.get("text", "")})
            else:
                parts.append({"type": "text",
                              "text": json.dumps(sub, ensure_ascii=False)})
        return parts
    return json.dumps(c, ensure_ascii=False)


def _convert_user(event, idx, toolcall_fns, losses):
    """A native 'user' event mid-run carries tool_result blocks; map each to a
    ChatMessageTool. A plain-content user event maps to ChatMessageUser."""
    msg = event["message"]
    content = msg.get("content")
    out = []
    if isinstance(content, str):
        return [{"id": _det_id("user", idx), "content": content, "role": "user"}]
    for i, block in enumerate(content or []):
        btype = block.get("type")
        if btype == "tool_result":
            tcid = block.get("tool_use_id", "")
            m = {"id": _det_id("tool", event.get("uuid", idx), i),
                 "content": _tool_result_content(block),
                 "role": "tool",
                 "tool_call_id": tcid}
            fn = toolcall_fns.get(tcid)
            if fn:
                m["function"] = fn
            out.append(m)
        elif btype == "text":
            out.append({"id": _det_id("user", event.get("uuid", idx), i),
                        "content": block.get("text", ""),
                        "role": "user"})
        else:
            losses["user.content.%s" % btype] = losses.get(
                "user.content.%s" % btype, 0) + 1
    return out


def convert_messages(events):
    """Returns (messages, result_event, unconverted_event_type_counts)."""
    messages = []
    losses = {}
    toolcall_fns = {}
    result_event = None
    for idx, event in enumerate(events):
        etype = event.get("type")
        if etype == "assistant":
            messages.append(_convert_assistant(event, idx, toolcall_fns, losses))
        elif etype == "user":
            messages.extend(_convert_user(event, idx, toolcall_fns, losses))
        elif etype == "result":
            result_event = event  # mapped into output/usage, not a message
        else:
            # system / hook / thinking_tokens / rate_limit_event / unknown:
            # no Inspect message referent; counted, reason EVAL_CONVERSION_LOSS.
            key = etype if etype else "<untyped>"
            sub = event.get("subtype")
            if sub:
                key = "%s.%s" % (key, sub)
            losses[key] = losses.get(key, 0) + 1
    return messages, result_event, losses


# ------------------------------------------------------- record -> eval

def _model_usage(result_event, model_id):
    mu = (result_event or {}).get("modelUsage") or {}
    out = {}
    for model, u in sorted(mu.items()):
        out[model] = {
            "input_tokens": u.get("inputTokens", 0),
            "output_tokens": u.get("outputTokens", 0),
            "total_tokens": u.get("inputTokens", 0) + u.get("outputTokens", 0),
            "input_tokens_cache_write": u.get("cacheCreationInputTokens", 0),
            "input_tokens_cache_read": u.get("cacheReadInputTokens", 0),
        }
    return out


def _output(result_event, messages, model_id):
    """ModelOutput from the native terminal 'result' event."""
    last_assistant = None
    for m in reversed(messages):
        if m["role"] == "assistant":
            last_assistant = m
            break
    if result_event is None or last_assistant is None:
        return None
    native_stop = result_event.get("stop_reason")
    choice_msg = dict(last_assistant)
    choice_msg["source"] = "generate"
    usage = result_event.get("usage") or {}
    return {
        "model": model_id,
        "choices": [{
            "message": choice_msg,
            "stop_reason": STOP_REASON_MAP.get(native_stop, "unknown"),
        }],
        "completion": result_event.get("result", ""),
        "usage": {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            "input_tokens_cache_write": usage.get("cache_creation_input_tokens", 0),
            "input_tokens_cache_read": usage.get("cache_read_input_tokens", 0),
        },
        "time": (result_event.get("duration_ms") or 0) / 1000.0,
    }


def _scores_from_verdict(record, null_reasons):
    """Sample-level scores. Verdict pending -> scores null + PENDING_GRADING
    (native convention propagated into the export)."""
    verdict = record.get("verdict")
    if verdict is None:
        null_reasons["samples[0].scores"] = "PENDING_GRADING"
        return None
    scores = {
        "m0_complete": {
            "value": bool(verdict.get("m0_complete")),
            "explanation": "TrustLadder grading verdict.m0_complete "
                           "(claims_table_ref: %s)" % verdict.get("claims_table_ref"),
            "metadata": {
                "first_claim_failed": verdict.get("first_claim_failed"),
                "escapes": verdict.get("escapes", []),
            },
            "history": [],
        }
    }
    return scores


def _results_from_verdict(record, null_reasons):
    verdict = record.get("verdict")
    if verdict is None:
        null_reasons["results"] = "PENDING_GRADING"
        return None
    return {
        "total_samples": 1,
        "completed_samples": 1,
        "scores": [{
            "name": "m0_complete",
            "scorer": "trustladder_grading",
            "params": {},
            "metrics": {
                "m0_complete": {
                    "name": "m0_complete",
                    "value": 1.0 if verdict.get("m0_complete") else 0.0,
                    "params": {},
                },
            },
        }],
    }


def build_eval_log(record, record_path):
    """Pure conversion: native record dict + files it references -> dict of
    zip member name -> python object (JSON-serializable)."""
    run_id = record["run_id"]
    task_id = record["task_id"]
    model_id = record["model_id"]

    transcript_path = record.get("transcript_ref")
    if not transcript_path or not os.path.isfile(transcript_path):
        raise ExportError("TRANSCRIPT MISSING: transcript_ref=%r does not exist "
                          "— refusing to export a record without its transcript"
                          % transcript_path)
    events = load_transcript(transcript_path)

    rec_session = record.get("session_id")
    transcript_sessions = {e.get("session_id") for e in events if e.get("session_id")}
    if rec_session and transcript_sessions and rec_session not in transcript_sessions:
        raise ExportError(
            "SESSION MISMATCH: record.session_id=%s but transcript %s carries "
            "session_id(s) %s — refusing (wrong transcript for this record)"
            % (rec_session, transcript_path, sorted(transcript_sessions)))

    messages, result_event, unconverted = convert_messages(events)

    # Export-level null+reason ledger (native reason-code enum).
    null_reasons = {
        # turn budget is turns, Inspect message_limit is messages: semantics
        # differ, so the config position stays null; turn_budget rides in the
        # embedded canonical record.
        "eval.config.message_limit": "EVAL_CONVERSION_LOSS",
        # answer keys are sealed (battery/keys/ never leaves band).
        "samples[0].target": "EVAL_CONVERSION_LOSS",
        # Inspect's internal event model is not synthesized; the native
        # transcript (transcript_ref) is canonical for events.
        "samples[0].events": "EVAL_CONVERSION_LOSS",
    }
    for k in sorted(unconverted):
        null_reasons["transcript.events.%s" % k] = "EVAL_CONVERSION_LOSS"

    prompt_text = read_prompt()
    input_msg = {"id": _det_id("input", run_id), "content": prompt_text, "role": "user"}

    scores = _scores_from_verdict(record, null_reasons)
    results = _results_from_verdict(record, null_reasons)
    output = _output(result_event, messages, model_id)
    if output is None:
        null_reasons["samples[0].output"] = "EVAL_CONVERSION_LOSS"
        output = {"model": model_id, "choices": [], "completion": ""}
    model_usage = _model_usage(result_event, model_id)

    # Validate every reason code against the live enum (no ad-hoc codes).
    valid_codes = emit_record.load_reason_codes()  # returns the enum as a set
    bad = {k: v for k, v in null_reasons.items() if v not in valid_codes}
    if bad:
        raise ExportError("UNREGISTERED REASON CODE(S) in export null_reasons: %r" % bad)

    export_meta = {
        "format": FORMAT_ID,
        "characterized_against_inspect_ai": TARGET_INSPECT_AI,
        "canonical": "native run record (records/<run_id>.json); this .eval is a "
                     "derivative view (prereg v3 Amendment J)",
        "null_reasons": null_reasons,
        "unconverted_event_types": {k: unconverted[k] for k in sorted(unconverted)},
        "source_record": record_path,
        "source_record_sha256": _sha256_file(record_path),
        "source_transcript": transcript_path,
        "source_transcript_sha256": _sha256_file(transcript_path),
        "dispatch_prompt_sha256": hashlib.sha256(prompt_text.encode()).hexdigest(),
    }

    spec = {
        "eval_id": _det_id("eval", run_id),
        "run_id": run_id,
        "created": record["started_at"],
        "task": "trustladder_%s" % task_id,
        "task_id": task_id,
        "task_version": 0,
        "task_display_name": "%s [%s]" % (task_id, record["arm"]),
        "task_attribs": {},
        "task_args": {"arm": record["arm"], "stratum": record["stratum"],
                      "family": record["family"], "batch": record["batch"]},
        "task_args_passed": {},
        "solver_args_passed": {},
        "tags": [record["arm"], "batch:%s" % record["batch"]],
        "dataset": {"name": DATASET_NAME, "samples": 1, "sample_ids": [task_id]},
        "model": "anthropic/%s" % model_id,
        "model_generate_config": {},
        "model_args": {},
        "config": {},
        "packages": {},
        "metadata": {
            "trustladder_record": record,
            "trustladder_export": export_meta,
        },
    }

    plan = {
        "name": "trustladder-native-dispatch",
        "steps": [{
            "solver": "claude_code_native_subject",
            "params": {
                "cli_version": record["cli_version"],
                "note": "Amendment J BINDING: the subject ran under the real "
                        "Claude Code binary, never orchestrated by Inspect; "
                        "this log is a post-hoc export.",
            },
        }],
        "config": {},
    }

    sample = {
        "id": task_id,
        "epoch": 1,
        "input": [input_msg],
        "target": "",
        "messages": [input_msg] + messages,
        "output": output,
        "metadata": {
            "run_id": run_id,
            "arm": record["arm"],
            "stratum": record["stratum"],
            "family": record["family"],
            "batch": record["batch"],
            "tree_hash": record["tree_hash"],
            "session_id": record.get("session_id"),
            "transcript_ref": transcript_path,
            "ls_audit_ref": record.get("ls_audit_ref"),
        },
        "store": {},
        "events": [],
        "model_usage": model_usage,
        "role_usage": {},
        "attachments": {},
        "events_data": {"messages": [], "calls": []},
    }
    if scores is not None:
        sample["scores"] = scores

    stats = {
        "started_at": record["started_at"],
        "completed_at": record["ended_at"],
        "model_usage": model_usage,
        "role_usage": {},
        "connection_limit_history": [],
    }

    status = "success" if record.get("subject_exit_code", 0) == 0 else "error"

    summary = {
        "id": task_id,
        "epoch": 1,
        "input": [input_msg],
        "target": "",
        "metadata": sample["metadata"],
        "model_usage": model_usage,
        "role_usage": {},
        "completed": True,
        "message_count": len(sample["messages"]),
    }
    if scores is not None:
        summary["scores"] = scores

    header = {"version": EVAL_LOG_VERSION, "status": status, "eval": spec,
              "plan": plan}
    if results is not None:
        header["results"] = results
    header.update({"stats": stats, "invalidated": False,
                   "tags": spec["tags"], "metadata": {}})

    sample_member = "samples/%s_epoch_1.json" % task_id
    # Member order mirrors inspect-ai 0.3.239 write order.
    return [
        ("_journal/start.json",
         {"version": EVAL_LOG_VERSION, "eval": spec, "plan": plan}),
        (sample_member, sample),
        ("_journal/summaries/1.json", [summary]),
        ("summaries.json", [summary]),
        ("header.json", header),
    ]


def write_eval_zip(members, out_path):
    tmp = out_path + ".tmp"
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as z:
        for name, obj in members:
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE_TIME)
            info.external_attr = 0o644 << 16
            z.writestr(info, _jdump(obj))
    os.replace(tmp, out_path)


def export(record_path, out_path=None):
    record = json.load(open(record_path))
    errors = emit_record.validate_record(record)
    if record.get("batch") == "dummy":
        # Same tolerance as the runner's dummy emit lane (single source:
        # emit_record.DUMMY_EXPECTED_ERRORS): dummy records deviate in
        # exactly two enum fields BY DESIGN.
        errors = [e for e in errors
                  if not e.startswith(emit_record.DUMMY_EXPECTED_ERRORS)]
    if errors:
        raise ExportError("NATIVE RECORD INVALID (canonical schema): refusing to "
                          "export a non-conformant record:\n  - "
                          + "\n  - ".join(errors))
    if out_path is None:
        base, _ = os.path.splitext(record_path)
        out_path = base + ".eval"
    members = build_eval_log(record, os.path.abspath(record_path))
    write_eval_zip(members, out_path)
    return out_path


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("record", help="path to canonical run record JSON")
    ap.add_argument("--out", default=None, help="output .eval path "
                    "(default: alongside the record)")
    args = ap.parse_args(argv)
    try:
        out = export(args.record, args.out)
    except ExportError as e:
        print("EXPORT REFUSED: %s" % e, file=sys.stderr)
        return 2
    print("exported: %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
