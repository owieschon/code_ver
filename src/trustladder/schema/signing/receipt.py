#!/usr/bin/env python3
"""receipt.py — Ed25519 signed-receipt core (VALUABLE tier).

Implements the four signed-receipt fields. Field names are frozen.
  record_hash       SHA256 over the agent's AS-DISPATCHED record: the
                    canonical record minus the four receipt fields AND
                    the grading-mutable fields (verdict,
                    grader_provenance, disagreement) — Option B
                    (canonicalization defined below)
  prev_record_hash  append-only hash chain (genesis: null + CHAIN_GENESIS)
  signature         Ed25519 over record_hash (the UTF-8 bytes of the
                    64-char lowercase hex digest), hex-encoded
  signer_key_id     "ed25519:" + SHA256 hex of the raw 32-byte public key

CANONICALIZATION (of record): deep-copy the record; drop the
HASH_EXCLUDED_FIELDS (the four receipt fields + the three
grading-mutable fields); drop any null_reasons entry whose root field is
excluded (so the hash of an as-dispatched record is identical computed
before signing, after signing, at verification, AND before vs. after a
grading merge of verdict/grader_provenance/disagreement); serialize with
json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True);
hash the UTF-8 bytes. Deterministic by construction (Ed25519 signatures
are deterministic; no clock reads here — chain state carries no
timestamps).

KEY LOCATION: ~/.trustladder/keys/ — OUTSIDE the repository, therefore
outside every arm worktree (the agent-visible surface IS the worktree):
the key is outside the agent-writable surface by construction. Keygen at
install: keygen.py (refuses overwrite).

CRYPTO BACKEND: python `cryptography` when importable (primary lane),
else the `openssl` CLI (pkeyutl -rawin; local subprocess, no network).
Both lanes produce identical deterministic signatures; the fallback lane
is exercised live via TRUSTLADDER_FORCE_OPENSSL=1.

GUARD-SURFACE (BINDING): this emitter, the chain state
(receipts/chain_head.json), and the key are guard-surface —
agent-write-blocked by construction (none live in a worktree) and
tampering attempts classify GUARD-SURFACE in the action ledger
(ledger/extract.py GUARD_SURFACE_PATTERNS; coverage check:
check_guard_watchlist.py).

Stdlib + optional `cryptography`. No network. No model.
"""

import hashlib
import json
import os
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS_DIR = os.path.dirname(os.path.dirname(HERE))

RECEIPT_FIELDS = ("record_hash", "prev_record_hash",
                  "signature", "signer_key_id")

# Grading-mutable fields, filled AFTER dispatch by the grading protocol
# (grading/grade_run.py merge_into_record). They are NOT part of the
# agent's as-dispatched claim and must NOT enter record_hash, or a later
# grading merge would change the hashed bytes and verify_chain would read
# a legitimately-graded record as tampered. Option B: the signature seals
# the agent's as-dispatched record; the verdict's integrity rests on the
# grading protocol (blind grading G1, calibration gate G2, triangulation/
# adjudication G3/G5) — a SEPARATE trust domain.
GRADING_MUTABLE_FIELDS = ("verdict", "grader_provenance", "disagreement")

# Single auditable exclusion set for record_hash: receipt fields (a hash
# cannot cover itself or the signature over it) AND the grading-mutable
# fields (Option B). The as-dispatched fields that REMAIN hashed are
# everything else the agent emits: claim, evidence_refs, gate_decisions,
# policy_fingerprint, policy_proof_ref, costs, identity, and the
# surviving null_reasons entries.
HASH_EXCLUDED_FIELDS = RECEIPT_FIELDS + GRADING_MUTABLE_FIELDS

DEFAULT_KEY_DIR = os.path.join(os.path.expanduser("~"),
                               ".trustladder", "keys")
PRIVATE_KEY_NAME = "trustladder_signing_ed25519.pem"
PUBLIC_KEY_NAME = "trustladder_signing_ed25519.pub"
KEY_ID_NAME = "key_id.txt"

DEFAULT_CHAIN_HEAD = os.path.join(HARNESS_DIR, "receipts",
                                  "chain_head.json")
CHAIN_HEAD_SCHEMA = "trustladder.receipt.chain_head.v1"

# SPKI DER for an Ed25519 public key = this fixed 12-byte prefix + the
# raw 32-byte key (RFC 8410) — used by the openssl lane to recover the
# raw key for the fingerprint.
_ED25519_SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


# ---------------------------------------------------------------------------
# Canonical hash
# ---------------------------------------------------------------------------

def canonical_payload(record):
    """The agent's AS-DISPATCHED record, serialized canonically. Returns
    bytes.

    Excludes HASH_EXCLUDED_FIELDS = the four receipt fields (a hash
    cannot cover itself or the signature over it) AND the grading-mutable
    fields (verdict, grader_provenance, disagreement). Their null_reasons
    entries are dropped too — keyed by root field, so both top-level
    paths ("verdict") and dotted sub-paths ("verdict.m0_complete") go —
    so the hash of an as-dispatched record is identical computed before
    signing, after signing, and at verification, AND identical before vs.
    after a grading merge. This is the Option B resolution: the signature
    seals only the as-dispatched claim; the verdict's integrity rests on
    the grading protocol, a separate trust domain."""
    rec = json.loads(json.dumps(record))  # deep copy via round-trip
    for field in HASH_EXCLUDED_FIELDS:
        rec.pop(field, None)
    null_reasons = rec.get("null_reasons")
    if isinstance(null_reasons, dict):
        for path in list(null_reasons):
            if path.split(".", 1)[0] in HASH_EXCLUDED_FIELDS:
                del null_reasons[path]
    return json.dumps(rec, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def compute_record_hash(record):
    """SHA256 hex over the canonical payload (the record_hash field)."""
    return hashlib.sha256(canonical_payload(record)).hexdigest()


# ---------------------------------------------------------------------------
# Backend selection (recorded: cryptography is the primary lane on this
# machine; openssl CLI is the live-demonstrated fallback)
# ---------------------------------------------------------------------------

def backend():
    if os.environ.get("TRUSTLADDER_FORCE_OPENSSL"):
        return "openssl"
    try:
        import cryptography  # noqa: F401
        return "cryptography"
    except ImportError:
        return "openssl"


def _key_paths(key_dir):
    key_dir = key_dir or DEFAULT_KEY_DIR
    return (os.path.join(key_dir, PRIVATE_KEY_NAME),
            os.path.join(key_dir, PUBLIC_KEY_NAME))


def _require_key(path, kind):
    if not os.path.exists(path):
        raise ValueError(
            "REFUSED: %s key not found at %s — signing key is generated "
            "at install by schema/signing/keygen.py and held OUTSIDE the "
            "agent-writable surface (~/.trustladder/keys/). Nothing "
            "signed, nothing written." % (kind, path))


def raw_public_key(key_dir=None):
    """Raw 32-byte Ed25519 public key from the PEM SPKI file."""
    _, pub_path = _key_paths(key_dir)
    _require_key(pub_path, "public")
    if backend() == "cryptography":
        from cryptography.hazmat.primitives import serialization
        with open(pub_path, "rb") as f:
            pub = serialization.load_pem_public_key(f.read())
        return pub.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw)
    der = subprocess.run(
        ["openssl", "pkey", "-pubin", "-in", pub_path, "-outform", "DER"],
        check=True, capture_output=True).stdout
    if not der.startswith(_ED25519_SPKI_PREFIX) or len(der) != 44:
        raise ValueError("public key at %s is not Ed25519 SPKI" % pub_path)
    return der[len(_ED25519_SPKI_PREFIX):]


def key_id(key_dir=None):
    """signer_key_id: 'ed25519:' + SHA256 hex of the raw public key."""
    return "ed25519:" + hashlib.sha256(raw_public_key(key_dir)).hexdigest()


def sign_hash(record_hash_hex, key_dir=None):
    """Ed25519 signature (hex) over the UTF-8 bytes of record_hash."""
    priv_path, _ = _key_paths(key_dir)
    _require_key(priv_path, "private")
    message = record_hash_hex.encode("utf-8")
    if backend() == "cryptography":
        from cryptography.hazmat.primitives import serialization
        with open(priv_path, "rb") as f:
            priv = serialization.load_pem_private_key(f.read(),
                                                      password=None)
        return priv.sign(message).hex()
    with tempfile.NamedTemporaryFile(suffix=".msg") as msg:
        msg.write(message)
        msg.flush()
        out = subprocess.run(
            ["openssl", "pkeyutl", "-sign", "-inkey", priv_path,
             "-rawin", "-in", msg.name],
            check=True, capture_output=True).stdout
    return out.hex()


def verify_signature(record_hash_hex, signature_hex, key_dir=None):
    """True iff signature verifies over record_hash with the public key."""
    _, pub_path = _key_paths(key_dir)
    _require_key(pub_path, "public")
    message = record_hash_hex.encode("utf-8")
    try:
        sig = bytes.fromhex(signature_hex)
    except (ValueError, TypeError):
        return False
    if backend() == "cryptography":
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        with open(pub_path, "rb") as f:
            pub = serialization.load_pem_public_key(f.read())
        try:
            pub.verify(sig, message)
            return True
        except InvalidSignature:
            return False
    with tempfile.NamedTemporaryFile(suffix=".msg") as msg, \
            tempfile.NamedTemporaryFile(suffix=".sig") as sigf:
        msg.write(message)
        msg.flush()
        sigf.write(sig)
        sigf.flush()
        res = subprocess.run(
            ["openssl", "pkeyutl", "-verify", "-pubin", "-inkey",
             pub_path, "-rawin", "-in", msg.name,
             "-sigfile", sigf.name],
            capture_output=True)
    return res.returncode == 0


# ---------------------------------------------------------------------------
# Chain state (receipts/chain_head.json — guard-surface)
# ---------------------------------------------------------------------------

def load_chain_head(chain_head_path=None):
    """Chain head dict, or None when the chain has no records yet."""
    path = chain_head_path or DEFAULT_CHAIN_HEAD
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        head = json.load(f)
    if head.get("schema") != CHAIN_HEAD_SCHEMA:
        raise ValueError("REFUSED: %s carries schema %r, expected %r"
                         % (path, head.get("schema"), CHAIN_HEAD_SCHEMA))
    return head


def advance_chain_head(record, chain_head_path=None):
    """Advance the chain head to a just-emitted SIGNED record. Called
    only AFTER the record file is durably written (emit_record.py).
    Refuses if the record does not extend the current head."""
    path = chain_head_path or DEFAULT_CHAIN_HEAD
    head = load_chain_head(path)
    prev_expected = head["head_record_hash"] if head else None
    if record.get("prev_record_hash") != prev_expected:
        raise ValueError(
            "REFUSED: record %r carries prev_record_hash %r but the chain "
            "head is %r — the chain is append-only; nothing advanced."
            % (record.get("run_id"), record.get("prev_record_hash"),
               prev_expected))
    new_head = {
        "schema": CHAIN_HEAD_SCHEMA,
        "head_record_hash": record["record_hash"],
        "head_run_id": record["run_id"],
        "length": (head["length"] if head else 0) + 1,
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(new_head, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)
    return new_head


# ---------------------------------------------------------------------------
# Record signing
# ---------------------------------------------------------------------------

def sign_record(record, key_dir=None, chain_head_path=None):
    """Return a signed copy of `record`: the four receipt fields
    populated, null_reasons adjusted (DEFERRED_UNSIGNED entries removed;
    genesis keeps prev_record_hash null + CHAIN_GENESIS).
    Does NOT write anything; emit_record.py owns the write + the
    chain-head advance ordering."""
    for field in ("record_hash", "signature", "signer_key_id"):
        if record.get(field) is not None:
            raise ValueError(
                "REFUSED: record %r already carries %s — re-signing an "
                "emitted record would rewrite history (append-only chain)."
                % (record.get("run_id"), field))
    signed = json.loads(json.dumps(record))  # deep copy
    head = load_chain_head(chain_head_path)
    null_reasons = signed.setdefault("null_reasons", {})
    for field in RECEIPT_FIELDS:
        null_reasons.pop(field, None)
    if head is None:
        signed["prev_record_hash"] = None
        null_reasons["prev_record_hash"] = "CHAIN_GENESIS"
    else:
        signed["prev_record_hash"] = head["head_record_hash"]
    record_hash = compute_record_hash(signed)
    signed["record_hash"] = record_hash
    signed["signature"] = sign_hash(record_hash, key_dir)
    signed["signer_key_id"] = key_id(key_dir)
    return signed
