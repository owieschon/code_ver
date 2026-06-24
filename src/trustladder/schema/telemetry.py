#!/usr/bin/env python3
"""telemetry.py — TrustLadder Stage 1 verdict-level telemetry appender.

Per verdict event: mechanism (hook | verifier | judge), latency,
tokens, dollars, verdict. Event shape authoritative in
schema/telemetry_event.schema.json; validation shared with
emit_record.validate_event_dict so the record's costs.verdict_events
and this flat stream can never drift apart.

- append_event(): validates, then appends one JSON line to
  records/telemetry.jsonl (default; path overridable). A malformed
  event is REFUSED and NOTHING is appended.
- Below-floor measures: pass the string "<FLOOR" (e.g. "<0.0001"),
  floor stated inline — omission is a loosening, so there is no way to
  leave a measure field out.
- Deterministic, stdlib-only, no network, no clock reads (ts is
  caller-supplied).

Demonstrated-red receipt (house discipline):
  receipts/telemetry_red_malformed_event.txt
"""

import json
import os
import sys

from trustladder.schema.emit_record import validate_event_dict, HARNESS_DIR

DEFAULT_TELEMETRY_PATH = os.path.join(HARNESS_DIR, "records", "telemetry.jsonl")


def append_event(event, telemetry_path=DEFAULT_TELEMETRY_PATH):
    """Validate one verdict event and append it as a JSON line.
    REFUSES (raises ValueError) on any schema violation; on refusal the
    telemetry file is untouched. Returns the path appended to."""
    errors = validate_event_dict(event)
    if errors:
        raise ValueError(
            "REFUSED: malformed verdict-telemetry event; nothing appended "
            "to %s.\n  - %s" % (telemetry_path, "\n  - ".join(errors)))
    line = json.dumps(event, sort_keys=True)
    os.makedirs(os.path.dirname(telemetry_path), exist_ok=True)
    with open(telemetry_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return telemetry_path


def read_events(telemetry_path=DEFAULT_TELEMETRY_PATH):
    """Read and re-validate every event line. REFUSES (raises ValueError)
    on the first malformed line — consumers (derive.py) never compute
    over unvalidated events. An ABSENT stream is also a refusal, not an
    empty result: runner/dispatch.py creates the stream (and appends the
    record's verdict events) at every record emit, so a missing file
    means the live wiring never ran — treating it as zero events would
    let derived columns compute vacuously over nothing (red receipt:
    receipts/derive_red_missing_stream.txt)."""
    if not os.path.exists(telemetry_path):
        raise ValueError(
            "REFUSED: flat verdict-telemetry stream %s does not exist — "
            "no dispatch ever appended to it (runner/dispatch.py creates "
            "and appends the stream at record emit). Refusing to read a "
            "missing stream as zero events." % telemetry_path)
    events = []
    with open(telemetry_path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(
                    "REFUSED: %s line %d is not valid JSON (%s)"
                    % (telemetry_path, lineno, e))
            errors = validate_event_dict(event)
            if errors:
                raise ValueError(
                    "REFUSED: %s line %d violates telemetry_event.schema.json:"
                    "\n  - %s" % (telemetry_path, lineno, "\n  - ".join(errors)))
            events.append(event)
    return events


def main(argv):
    if len(argv) >= 2 and argv[0] == "append":
        telemetry_path = DEFAULT_TELEMETRY_PATH
        if len(argv) >= 4 and argv[2] == "--telemetry-path":
            telemetry_path = argv[3]
        with open(argv[1], "r", encoding="utf-8") as f:
            event = json.load(f)
        try:
            path = append_event(event, telemetry_path)
        except ValueError as e:
            sys.stderr.write(str(e) + "\n")
            return 1
        print("APPENDED: 1 event -> %s" % path)
        return 0
    if len(argv) >= 1 and argv[0] == "check":
        telemetry_path = argv[1] if len(argv) >= 2 else DEFAULT_TELEMETRY_PATH
        try:
            events = read_events(telemetry_path)
        except ValueError as e:
            sys.stderr.write(str(e) + "\n")
            return 1
        print("OK: %d events validate against telemetry_event.schema.json"
              % len(events))
        return 0
    sys.stderr.write(
        "usage: telemetry.py append <event.json> [--telemetry-path PATH]\n"
        "       telemetry.py check [telemetry.jsonl]\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
