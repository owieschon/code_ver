#!/usr/bin/env python3
"""verify_chain.py — chain-verification check for the signed receipt chain.

What it verifies, walking BACK from the chain head:
  1. chain head file loads and its head_record_hash resolves to a record
  2. per record: recomputed canonical hash == stored record_hash
     (content tamper detection)
  3. per record: Ed25519 signature verifies over record_hash with the
     install-time public key, and signer_key_id matches that key
  4. prev_record_hash links resolve record-to-record down to genesis
     (prev null + reason code CHAIN_GENESIS), with no cycles
  5. walked length == chain head length == number of signed records in
     the store (no orphan signed records outside the chain)

Unsigned records (null + DEFERRED_UNSIGNED) are reported and skipped —
the unsigned lane is legal (deferral changes no field names); they are
simply not chain members.

What a PASS proves and does not prove: record integrity AFTER the fact
only (records made, not altered since, in order). The BEFORE-the-fact
proof is policy_proof_ref. Stdlib + receipt.py. No network. No model.
"""

import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
from trustladder.schema.signing import receipt

HARNESS_DIR = receipt.HARNESS_DIR
DEFAULT_RECORDS_DIR = os.path.join(HARNESS_DIR, "records")


def load_records(records_dir):
    records = {}
    for path in sorted(glob.glob(os.path.join(records_dir, "*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and \
                data.get("schema") == receipt.CHAIN_HEAD_SCHEMA:
            continue  # chain-state file co-located with demo records
        records[path] = data
    return records


def verify(records_dir, chain_head_path, key_dir):
    failures = []
    records = load_records(records_dir)
    signed = {}    # stored record_hash -> (path, record)
    unsigned = []
    for path, rec in records.items():
        rh = rec.get("record_hash")
        if rh is None:
            unsigned.append(path)
            continue
        if rh in signed:
            failures.append("duplicate record_hash %s in %s and %s"
                            % (rh, signed[rh][0], path))
            continue
        signed[rh] = (path, rec)

    head = receipt.load_chain_head(chain_head_path)
    if head is None:
        if signed:
            failures.append(
                "no chain head at %s but %d signed record(s) exist — "
                "chain state missing or removed"
                % (chain_head_path or receipt.DEFAULT_CHAIN_HEAD,
                   len(signed)))
        else:
            print("EMPTY CHAIN: no chain head, no signed records "
                  "(%d unsigned DEFERRED record(s) present) — OK"
                  % len(unsigned))
            return []
    expected_key_id = receipt.key_id(key_dir)

    visited = set()
    cursor = head["head_record_hash"] if head else None
    walked = 0
    while not failures and cursor is not None:
        if cursor in visited:
            failures.append("chain cycle at record_hash %s" % cursor)
            break
        visited.add(cursor)
        entry = signed.get(cursor)
        if entry is None:
            failures.append(
                "chain link %s resolves to NO record in %s "
                "(record removed or its hash altered)"
                % (cursor, records_dir))
            break
        path, rec = entry
        walked += 1
        name = os.path.basename(path)
        recomputed = receipt.compute_record_hash(rec)
        if recomputed != cursor:
            failures.append(
                "RECORD HASH MISMATCH at %s (run_id=%s): stored "
                "record_hash %s but canonical content hashes to %s — "
                "record content was ALTERED AFTER SIGNING"
                % (name, rec.get("run_id"), cursor, recomputed))
            break
        if rec.get("signer_key_id") != expected_key_id:
            failures.append(
                "SIGNER KEY MISMATCH at %s (run_id=%s): record carries "
                "%r, install key is %r"
                % (name, rec.get("run_id"), rec.get("signer_key_id"),
                   expected_key_id))
            break
        if not receipt.verify_signature(cursor, rec.get("signature") or "",
                                        key_dir):
            failures.append(
                "SIGNATURE INVALID at %s (run_id=%s): Ed25519 signature "
                "does not verify over record_hash %s with key %s"
                % (name, rec.get("run_id"), cursor, expected_key_id))
            break
        prev = rec.get("prev_record_hash")
        if prev is None:
            reason = (rec.get("null_reasons") or {}).get("prev_record_hash")
            if reason != "CHAIN_GENESIS":
                failures.append(
                    "GENESIS WITHOUT REASON CODE at %s: prev_record_hash "
                    "null but null_reasons carries %r, expected "
                    "CHAIN_GENESIS" % (name, reason))
                break
            print("  OK  %s  run_id=%s  hash+signature verified "
                  "(GENESIS)" % (name, rec.get("run_id")))
        else:
            print("  OK  %s  run_id=%s  hash+signature verified, "
                  "prev=%s..." % (name, rec.get("run_id"), prev[:16]))
        cursor = prev

    if not failures:
        if walked != head["length"]:
            failures.append(
                "chain head declares length %d but the walk from head to "
                "genesis covered %d record(s) — chain truncated or head "
                "stale" % (head["length"], walked))
        orphans = [signed[h][0] for h in signed if h not in visited]
        if orphans:
            failures.append(
                "%d signed record(s) NOT reachable from the chain head "
                "(spliced out or never chained): %s"
                % (len(orphans),
                   ", ".join(os.path.basename(p) for p in sorted(orphans))))
    if unsigned and not failures:
        for p in sorted(unsigned):
            print("  --  %s  unsigned (DEFERRED_UNSIGNED lane), not a "
                  "chain member" % os.path.basename(p))
    return failures


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--records-dir", default=DEFAULT_RECORDS_DIR)
    ap.add_argument("--chain-head", default=receipt.DEFAULT_CHAIN_HEAD)
    ap.add_argument("--key-dir", default=receipt.DEFAULT_KEY_DIR)
    args = ap.parse_args(argv)
    failures = verify(args.records_dir, args.chain_head, args.key_dir)
    if failures:
        print("\nCHAIN VERIFICATION: FAILED")
        for f in failures:
            print("  FAIL: %s" % f)
        return 1
    print("\nCHAIN VERIFICATION: PASS (records dir %s, head %s, "
          "backend %s)" % (args.records_dir, args.chain_head,
                           receipt.backend()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
