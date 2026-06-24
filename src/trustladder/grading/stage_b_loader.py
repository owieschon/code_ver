#!/usr/bin/env python3
"""Stage-B grading-input loader — the structural blindness fence.

Contract:
  - Transcript prose NEVER enters defect grading. This loader REFUSES
    any input that contains transcript text fields (by key, anywhere in
    the JSON tree) or transcript-shaped string content (role markers).
  - Defect grading is BLIND TO ARM. The input contract carries no arm
    field; the loader REFUSES arm metadata (keys or bare arm-label
    values) rather than silently stripping it — refusal makes a
    contaminated feed loud instead of quietly laundered.
  - The top-level contract is CLOSED: unknown top-level keys are
    refused, so nothing can be smuggled in under a novel name.

Valid stage-B input (grading_input JSON):
  required: run_id, task_id, terminal_tree_path
  optional: tree_hash (verified against the terminal tree if present),
            claims_table_ref (a POINTER to the stage-A claims table;
            never its content), claimed_done (bool, structured stage-A
            output), battery_root
"""

import hashlib
import json
import os

REQUIRED_KEYS = frozenset({"run_id", "task_id", "terminal_tree_path"})
ALLOWED_KEYS = REQUIRED_KEYS | frozenset(
    {"tree_hash", "claims_table_ref", "claimed_done", "battery_root"}
)

# Transcript text fields, refused at ANY depth (case-insensitive).
FORBIDDEN_TRANSCRIPT_KEYS = frozenset(
    {
        "transcript",
        "transcript_ref",
        "transcript_text",
        "transcript_path",
        "messages",
        "conversation",
        "dialogue",
        "turns",
        "chat",
        "prose",
        "session_log",
        "assistant_text",
    }
)

# Arm metadata, refused at ANY depth: keys and bare arm-label values.
FORBIDDEN_ARM_KEYS = frozenset({"arm", "arm_id", "arm_label", "stratum_arm"})
ARM_LABEL_VALUES = frozenset({"L0", "L1", "SHAM", "L3"})

# Transcript-shaped string content (smuggled prose) — refused.
PROSE_MARKERS = (
    '"role": "assistant"',
    '"role":"assistant"',
    '"role": "user"',
    '"role":"user"',
    "\nAssistant:",
    "\nHuman:",
)


class GradingInputRefused(Exception):
    """Raised when stage-B input violates the blindness contract."""


def _walk(node, path):
    if isinstance(node, dict):
        for key, value in node.items():
            key_l = str(key).lower()
            here = f"{path}.{key}" if path else str(key)
            if key_l in FORBIDDEN_TRANSCRIPT_KEYS:
                raise GradingInputRefused(
                    "GRADING-INPUT REFUSED [transcript fence]: field "
                    f"'{here}' is a transcript text field; transcript prose "
                    "never enters defect grading (prereg Sec. 6 G1)."
                )
            if key_l in FORBIDDEN_ARM_KEYS:
                raise GradingInputRefused(
                    "GRADING-INPUT REFUSED [arm-blindness fence]: field "
                    f"'{here}' carries arm metadata; defect grading is blind "
                    "to arm (prereg Sec. 6 G1)."
                )
            _walk(value, here)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            _walk(value, f"{path}[{i}]")
    elif isinstance(node, str):
        if node in ARM_LABEL_VALUES:
            raise GradingInputRefused(
                "GRADING-INPUT REFUSED [arm-blindness fence]: value "
                f"'{node}' at '{path}' is an arm label; defect grading is "
                "blind to arm (prereg Sec. 6 G1)."
            )
        for marker in PROSE_MARKERS:
            if marker in node:
                raise GradingInputRefused(
                    "GRADING-INPUT REFUSED [transcript fence]: string at "
                    f"'{path}' contains transcript-shaped content "
                    f"({marker.strip()!r}); transcript prose never enters "
                    "defect grading (prereg Sec. 6 G1)."
                )


def compute_tree_hash(tree_path):
    """sha256 over sorted '<relpath>:<sha256(file)>' lines, non-.git files
    (same method as battery provenance_pins.json container_tree_hash).

    NOTE: this is the BATTERY-CONTAINER convention. It is NOT the algorithm a
    runner record's tree_hash is signed with — see compute_record_tree_hash
    below. Do not use this to verify a record's tree_hash."""
    lines = []
    for root, dirs, files in os.walk(tree_path):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
        for name in sorted(files):
            if name.endswith(".pyc"):
                continue
            full = os.path.join(root, name)
            rel = os.path.relpath(full, tree_path)
            with open(full, "rb") as fh:
                digest = hashlib.sha256(fh.read()).hexdigest()
            lines.append(f"{rel}:{digest}")
    blob = "\n".join(sorted(lines)).encode()
    return hashlib.sha256(blob).hexdigest()


def compute_record_tree_hash(root):
    """Byte-identical to dispatch.tree_hash (the RUNNER's algorithm): a single
    streaming sha256 of update(relpath) then update(filebytes), files sorted
    within each sorted directory, excluding .git/__pycache__ dirs and .pyc/.pyo.

    A record's tree_hash is SIGNED with this streaming value
    (it is in record_hash, not HASH_EXCLUDED_FIELDS), so the grader must verify a
    record against THIS function, not the line-digest compute_tree_hash above. The
    72 signed records are not rewritten; only the grader's verifier is corrected."""
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in (".git", "__pycache__"))
        for fn in sorted(filenames):
            if fn == ".git" or fn.endswith((".pyc", ".pyo")):
                continue
            p = os.path.join(dirpath, fn)
            h.update(os.path.relpath(p, root).encode())
            try:
                with open(p, "rb") as fh:
                    h.update(fh.read())
            except Exception:
                h.update(b"?")
    return h.hexdigest()


def load_grading_input(path):
    """Load + validate a stage-B grading input; raises GradingInputRefused."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise GradingInputRefused(
            "GRADING-INPUT REFUSED: top level must be a JSON object."
        )
    # Specific fences first (transcript / arm, at any depth), so refusals
    # name the violated fence; the closed whitelist is the backstop for
    # anything smuggled under a novel name.
    _walk(data, "")
    unknown = set(data) - ALLOWED_KEYS
    if unknown:
        raise GradingInputRefused(
            "GRADING-INPUT REFUSED [closed contract]: unknown top-level "
            f"field(s) {sorted(unknown)}; allowed: {sorted(ALLOWED_KEYS)}."
        )
    missing = REQUIRED_KEYS - set(data)
    if missing:
        raise GradingInputRefused(
            f"GRADING-INPUT REFUSED: missing required field(s) {sorted(missing)}."
        )
    tree = data["terminal_tree_path"]
    if not os.path.isdir(tree):
        raise GradingInputRefused(
            f"GRADING-INPUT REFUSED: terminal_tree_path '{tree}' is not a directory."
        )
    if data.get("tree_hash"):
        # A record's tree_hash is the RUNNER's streaming value
        # (dispatch.tree_hash), so verify with compute_record_tree_hash;
        # the battery-container compute_tree_hash is a different convention.
        actual = compute_record_tree_hash(tree)
        if actual != data["tree_hash"]:
            raise GradingInputRefused(
                "GRADING-INPUT REFUSED [integrity]: tree_hash mismatch — "
                f"input pins {data['tree_hash']}, terminal tree hashes {actual}."
            )
    return data


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        sys.exit("usage: stage_b_loader.py <grading_input.json>")
    try:
        loaded = load_grading_input(sys.argv[1])
    except GradingInputRefused as exc:
        print(exc, file=sys.stderr)
        sys.exit(2)
    print(json.dumps({"accepted": True, "input": loaded}, indent=2))
