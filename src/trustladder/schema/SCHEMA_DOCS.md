# SCHEMA DOCS — Run Record + Verdict Telemetry (Deliverable 8 CORE)

Authority: PREREGISTRATION_v3_trustladder.md Sec. 7A (Amendments D, F,
v3.1) > DISPATCH deliverable 8 > harness/ARCHITECTURE.md Sec. 2
(field names frozen there). Schema slimming is a loosening (prereg 7A).

Files in this directory:

| File | Role |
|---|---|
| `run_record.schema.json` | Canonical run-record shape; one JSON record per run at `records/<run_id>.json`; the public benchmark publishes in this format. |
| `telemetry_event.schema.json` | Verdict-level telemetry event shape (single source for both `records/telemetry.jsonl` lines and each record's `costs.verdict_events` items). |
| `reason_codes.json` | The reason-code enum for the null convention (extend only by logged revision). |
| `emit_record.py` | Record constructor (`skeleton`), validator (`validate_record`, two lanes + the conventions JSON Schema cannot express), emitter (`emit`, refuses on violation and on overwrite). |
| `telemetry.py` | `append_event` (validate-then-append to `records/telemetry.jsonl`; malformed events refused, nothing written) and `read_events` (re-validates on read). |
| `derive.py` | Per-arm derived columns: cost-per-verified-completion, cost-per-claimed-completion, per-mechanism latency/tokens/dollars aggregates. |
| `demo_derive_synthetic.py` | Deterministic live-path demonstration on synthetic data (writes only `schema/demo_data/`). |
| `signing/receipt.py` | Signed-receipt core (VALUABLE tier, BUILT): canonical `record_hash`, `prev_record_hash` chain, Ed25519 `signature`, `signer_key_id`, chain-head state I/O. |
| `signing/keygen.py` | Install-time Ed25519 keygen to `~/.trustladder/keys/` (refuses overwrite). |
| `signing/verify_chain.py` | Chain-verification check (recompute hashes, verify signatures, walk prev links head→genesis, length + orphan checks). |
| `signing/check_guard_watchlist.py` | Refuses unless key/emitter/chain-state tampering classifies GUARD-SURFACE through the LIVE ledger extraction layer. |
| `signing/demo_chain.py` | Deterministic scratch-chain builder for the demonstrated-red ceremony (never touches the real chain head). |

---

## What the signature does and does not prove

Verbatim, PREREGISTRATION_v3_trustladder.md Sec. 7A, RATIONALE OF
RECORD (vendor-neutral, encodes the design distinction):

> a signature attests that a record was made and not
> altered AFTER the fact; the policy_proof_ref attests that the
> decider was proven able to say no BEFORE the fact. A signed decision
> proves what the gate said; a demonstrated catch proves the gate
> works. A complete receipt carries both; signed-decision practice in
> adjacent tooling carries only the first.

Stated plainly, of record:

- The `signature` (Ed25519 over `record_hash`) and the
  `record_hash`/`prev_record_hash` chain prove ONLY record integrity
  after the fact: this record was made, has not been altered since,
  and sits unmoved in the append-only chain.
- The signature does NOT prove the gate's decision was correct, that
  the gate works, that the policy was effective, or that the agent
  behaved. A forged-judgment world signs its records just as cleanly.
- The BEFORE-the-fact proof lives in `policy_proof_ref`: the pointer
  to the in-force `policy_fingerprint`'s demonstrated-red
  (adversarial eval pass) record — the decider proven able to say no
  before it was trusted to.
- A complete receipt carries both. A record with a valid signature
  and a null `policy_proof_ref` is integrity-attested hearsay about
  an unproven gate; a record with `policy_proof_ref` and no signature
  is a proven gate's claim with no after-the-fact tamper evidence.

### Scope of the signature: the agent's AS-DISPATCHED claim (Option B, A5 / T-D24)

The signature covers, and attests, **the agent's as-dispatched record**:
the identity fields, `claim`, `evidence_refs`, `gate_decisions`,
`policy_fingerprint`, `policy_proof_ref`, `costs`, and the surviving
`null_reasons` entries — the bytes the agent emitted at dispatch, the
thing that needs tamper-evidence.

- It ATTESTS that the agent's as-dispatched claim was made and was not
  altered after the fact. Tampering with any as-dispatched field after
  signing fails verification (demonstrated red:
  `receipts/signing_B_tamper_red.txt`, claim altered post-signing →
  `RECORD HASH MISMATCH`).
- It deliberately does **NOT** cover `verdict`, `grader_provenance`, or
  `disagreement`. Those are filled AFTER dispatch by the grading
  protocol, not by the agent; the `record_hash` exclusion set
  (`signing/receipt.py` `HASH_EXCLUDED_FIELDS`) excludes them by
  construction, so a legitimate grading merge changes no hashed bytes
  and the signed record still verifies (demonstrated:
  `receipts/signing_B_grading_merge_green.txt`).
- The integrity of `verdict`/`grader_provenance`/`disagreement` rests on
  the **grading protocol** — a separate trust domain — not on the
  agent's signature: blind grading (G1, blindness fence), the
  calibration state-file gate (G2), and triangulation / adjudication
  (G3/G5). Sealing the verdict inside the agent's signature chain would
  conflate the two domains.

Owner rationale (A5 decision, Option B, recorded verbatim-in-spirit): *"The
signature's job is to seal the agent's as-dispatched claim — the thing
that needs tamper-evidence. The verdict's integrity properly rests on the
grading protocol (blind grading, calibration, adjudication), so it does
not belong inside the agent's signature chain at all. Option C conflates
two trust domains and costs two signatures to explain."* This is the
**Option B** resolution of freeze blocker A5 (raised T-D21, resolved
T-D24); it extends — and does not contradict — the Amendment F / prereg
7A rationale-of-record above (the signature is after-the-fact integrity;
`policy_proof_ref` is before-the-fact proof). A5/Option B simply scopes
*which* fields the after-the-fact integrity covers: the agent's
as-dispatched claim, not the grader's verdict.

Signing status in this build: **BUILT** (VALUABLE tier, 2026-06-12;
`schema/signing/`) and default-on in the production dispatch path
(`runner/dispatch.py` non-dummy lane calls `emit(..., sign=True)`).
The four signed-receipt fields (`record_hash`, `prev_record_hash`,
`signature`, `signer_key_id`) are present in every record EITHER WAY —
field names stable; unsigned records emit null + `DEFERRED_UNSIGNED`
(chain genesis: null + `CHAIN_GENESIS` on `prev_record_hash`); the
unsigned lane remains legal and demonstrated
(`receipts/signing_green_unsigned_lane.txt`). Ledger integrity
(BINDING, prereg 7A): the receipt emitter, chain state, and signing
key are guard-surface — agent-write-blocked; tampering attempts
classify GUARD-SURFACE in the action ledger; the key lives outside
the agent-writable surface at `~/.trustladder/keys/`.

---

## Signed-receipt implementation (schema/signing/, of record)

**Key location + rationale.** The Ed25519 signing key is generated at
install by `signing/keygen.py` into `~/.trustladder/keys/` (dir 0700;
private key `trustladder_signing_ed25519.pem`, 0600; public key
`trustladder_signing_ed25519.pub`, 0644; `key_id.txt` records the
fingerprint and backend). Rationale: the agent-visible surface IS the
arm worktree (ARCHITECTURE Sec. 3) and every worktree is created from
this repository's content — a path outside the repo is outside every
worktree, hence outside the agent-writable surface by construction.
Keygen refuses to overwrite an existing key (rotation mid-study would
orphan every chained signature; demonstrated red:
`receipts/signing_red_keygen_overwrite.txt`).

**Crypto backend, recorded.** Primary lane on this machine: python
`cryptography` 46.0.7. Fallback lane (no `cryptography` import, or
`TRUSTLADDER_FORCE_OPENSSL=1`): the `openssl` CLI (3.6.2) via
`pkeyutl -rawin` — local subprocess, no network. Both lanes proved
live and byte-identical (deterministic Ed25519):
`receipts/signing_backend_lanes.txt`.

**Canonicalization (what `record_hash` covers).** Deep-copy the
record; drop `HASH_EXCLUDED_FIELDS` (the four receipt fields + the three
grading-mutable fields `verdict`/`grader_provenance`/`disagreement` —
the single auditable exclusion set in `signing/receipt.py`) AND any
`null_reasons` entry whose root field is excluded (keyed by root field,
so both `"verdict"` and dotted sub-paths like `"verdict.m0_complete"`
go); serialize
`json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)`;
SHA256 the UTF-8 bytes. The hash is therefore identical computed before
signing, after signing, at verification, AND before vs. after a grading
merge — the Option B / A5 scope (T-D24): `record_hash` covers the
agent's as-dispatched record only, never the grader's verdict. The `signature` is Ed25519 over the UTF-8
bytes of the 64-char lowercase-hex `record_hash`, hex-encoded.
`signer_key_id` = `"ed25519:" + SHA256hex(raw 32-byte public key)`.

**Chain state.** `receipts/chain_head.json`
(`trustladder.receipt.chain_head.v1`: `head_record_hash`,
`head_run_id`, `length`) — guard-surface, advanced atomically and only
AFTER the record file is durably written. Genesis records carry
`prev_record_hash: null` + `CHAIN_GENESIS`.

**Threat-model note (of record).** Per the frozen contract the
signature covers `record_hash` only; `prev_record_hash` and the chain
order are NOT under any signature. Splice/truncation/reorder detection
therefore rests on (a) the prev-link walk recomputing every content
hash, and (b) the guard-surface chain head anchoring the head hash and
the chain LENGTH. Demonstrated catches: content tamper
(`receipts/chain_red.txt`), forged signature and spliced-out record
(`receipts/signing_red_signature_and_splice.txt`).

**Verification.** `signing/verify_chain.py` checks, walking head →
genesis: recomputed canonical hash == stored `record_hash`; signature
verifies with the install public key; `signer_key_id` matches that
key; prev links resolve with no cycles; genesis carries
`CHAIN_GENESIS`; walked length == chain-head length; no signed record
sits outside the chain. Unsigned (`DEFERRED_UNSIGNED`) records are
reported and skipped — legal non-members. Clean-chain green:
`receipts/chain_green.txt`. A PASS proves record integrity AFTER the
fact only; the BEFORE-the-fact proof is `policy_proof_ref` (rationale
verbatim above).

---

## Null + reason-code convention (frozen)

An unpopulatable field is emitted as **null** AND registered in the
record's top-level `null_reasons` map: `{<field_path>: <REASON_CODE>}`
(dot-paths, e.g. `costs.cost_per_verified_completion`). Below-floor
measures are the one non-null case: they emit the string `"<FLOOR"`
with the floor stated inline (e.g. `"<0.0001"`) and are registered
with `BELOW_MEASUREMENT_FLOOR`. Omission without a reason code is a
loosening; `emit_record.py` rejects it (demonstrated red:
`receipts/emit_record_red_null_without_reason.txt`). Stale entries
(a reason for a populated field) are also rejected.

| Reason code | When emitted | Typical fields |
|---|---|---|
| `NOT_APPLICABLE_ARM` | Field has no referent in this arm; contamination fence: no in-band logging in bare arms | `gate_decisions`, `policy_fingerprint`, `policy_proof_ref` in L0/L1 |
| `DEFERRED_UNSIGNED` | Signing deferred per the VALUABLE-tier pre-named order | `record_hash`, `prev_record_hash`, `signature`, `signer_key_id` |
| `BELOW_MEASUREMENT_FLOOR` | Measure unobtainable below the floor; field emits `"<FLOOR"` (floor stated), not null | `costs.dollars`, event `latency_ms`/`tokens`/`dollars` |
| `EVAL_CONVERSION_LOSS` | Field unconvertible in the Inspect-compatible `.eval` export ONLY; never valid in the canonical native record | `.eval` export fields |
| `PENDING_GRADING` | Grading has not run yet (runner-skeleton state) | `verdict`, `grader_provenance`, `disagreement`, `costs.cost_per_*_completion` |
| `PENDING_ADJUDICATION` | A disagreement/contamination/recall flag is open, ruling not yet written back | `disagreement.adjudication`-related state |
| `CHAIN_GENESIS` | First record in the append-only hash chain | `prev_record_hash` |
| `NO_DISAGREEMENT` | Grading complete, no disagreement/flag — null is the substantive value (prereg types `disagreement` as `null \| {...}`). Logged revision, BUILD_NOTES Deliverable 8 (tightening: keeps every-null-carries-a-reason total) | `disagreement` |

---

## Derived columns (prereg 7A, Amendment D)

Computed PER ARM at analysis by `derive.py`; per-run raw stays in the
record (`costs.verdict_events` + run totals). Definitions of record:
claimed completion = `claim.claimed_done == true`; verified
completion = `verdict.m0_complete == true`; each cost column =
sum(`costs.dollars`) over the arm's runs divided by the respective
count; zero denominator emits null with an explicit note. Below-floor
values contribute 0 (totals are lower bounds) and are counted in
`below_floor_counts`. Per-mechanism latency/tokens/dollars/verdict
counts aggregate from `records/telemetry.jsonl` joined to records by
`run_id`; arm is read from the record only, so the join cannot
contradict it. Grader calibration-set cost is logged as its own line
item (grading harness, deliverable 9 — not folded into these columns).

## Demonstrated-red receipts (house discipline)

- `receipts/emit_record_red_missing_claim.txt` — record missing `claim` refused (both validation lanes).
- `receipts/emit_record_red_null_without_reason.txt` — null without reason code refused.
- `receipts/emit_record_red_duplicate_run_id.txt` — append-only store: re-emit of an existing `run_id` refused.
- `receipts/telemetry_red_malformed_event.txt` — malformed verdict event refused at append; file untouched.
- `receipts/derive_red_malformed_event.txt` — derivation refuses a telemetry file containing a malformed line.
- `receipts/derive_red_orphan_event.txt` — derivation refuses an event whose `run_id` has no record.
- `receipts/derive_green_synthetic.txt` — green: full synthetic end-to-end derivation (deterministic; byte-identical across runs).
- `receipts/signing_red_guard_watchlist.txt` — guard-watchlist check RED before wiring: key/emitter tampering fell through to SCOPE, not GUARD-SURFACE (green after wiring: `receipts/signing_guard_watchlist_green.txt`).
- `receipts/signing_red_no_key.txt` — emit `--sign` with no installed key refused; nothing written.
- `receipts/signing_red_keygen_overwrite.txt` — second keygen run refused (install-once key).
- `receipts/chain_red.txt` — REQUIRED ceremony: 3 signed records emitted, record 2 modified, chain verification FAILS (RECORD HASH MISMATCH).
- `receipts/chain_green.txt` — clean-chain green after restoring record 2.
- `receipts/signing_B_grading_merge_green.txt` — Option B / A5 (T-D24): signed as-dispatched record SURVIVES a grading merge of verdict/grader_provenance/disagreement (record_hash unchanged; chain verifies).
- `receipts/signing_B_tamper_red.txt` — Option B / A5 (T-D24): tampering an AS-DISPATCHED field (claim) after signing still FAILS verification (RECORD HASH MISMATCH).
- `receipts/signing_red_signature_and_splice.txt` — forged signature → SIGNATURE INVALID; record deleted from store → chain-link resolution failure.
- `receipts/signing_green_unsigned_lane.txt` — unsigned DEFERRED_UNSIGNED lane intact alongside the signed lane.
- `receipts/signing_backend_lanes.txt` — cryptography and openssl lanes both live, byte-identical signatures, cross-lane verify.
- `receipts/signing_livepath_grep.txt` — grep-proves-live-path for the signing layer (dispatch → emit(sign=True) → receipt.py; watchlist in ledger/extract.py).
