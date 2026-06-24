#!/usr/bin/env python3
"""keygen.py — install-time Ed25519 keygen for the signed-receipt layer.

Signing key is generated at install and held OUTSIDE the agent-writable
surface. Key dir: ~/.trustladder/keys/ — outside the repository,
therefore outside every arm worktree (the agent-visible surface IS the
worktree).

Writes (dir 0700):
  trustladder_signing_ed25519.pem  PKCS8 PEM private key, 0600
  trustladder_signing_ed25519.pub  SPKI PEM public key,  0644
  key_id.txt                       signer_key_id + backend used, 0644

REFUSES to overwrite an existing key: rotating the signer mid-study
would orphan every signature in the chain (demonstrated red:
receipts/signing_red_keygen_overwrite.txt).

Backend: python `cryptography` if importable, else `openssl` CLI
(recorded into key_id.txt). Local only; no network.
"""

import os
import stat
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
from trustladder.schema.signing import receipt


def generate(key_dir=None):
    key_dir = key_dir or receipt.DEFAULT_KEY_DIR
    priv_path = os.path.join(key_dir, receipt.PRIVATE_KEY_NAME)
    pub_path = os.path.join(key_dir, receipt.PUBLIC_KEY_NAME)
    id_path = os.path.join(key_dir, receipt.KEY_ID_NAME)
    for p in (priv_path, pub_path):
        if os.path.exists(p):
            raise SystemExit(
                "REFUSED: %s already exists. Keygen runs ONCE at install; "
                "overwriting the signing key would orphan every signature "
                "already in the append-only chain (chain verification "
                "would fail on signer_key_id/signature for all prior "
                "records). Nothing written." % p)
    os.makedirs(key_dir, exist_ok=True)
    os.chmod(key_dir, stat.S_IRWXU)  # 0700

    used = receipt.backend()
    if used == "cryptography":
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey)
        priv = Ed25519PrivateKey.generate()
        priv_pem = priv.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption())
        pub_pem = priv.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo)
        _write(priv_path, priv_pem, 0o600)
        _write(pub_path, pub_pem, 0o644)
    else:
        fd = os.open(priv_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519",
                        "-out", priv_path], check=True)
        os.chmod(priv_path, 0o600)
        subprocess.run(["openssl", "pkey", "-in", priv_path, "-pubout",
                        "-out", pub_path], check=True)
        os.chmod(pub_path, 0o644)

    kid = receipt.key_id(key_dir)
    _write(id_path,
           ("signer_key_id: %s\nbackend: %s\nkey_dir: %s\n"
            % (kid, used, key_dir)).encode("utf-8"), 0o644)
    print("KEY GENERATED at install")
    print("  key_dir:       %s  (outside the repo => outside every arm "
          "worktree => outside the agent-writable surface)" % key_dir)
    print("  private key:   %s (0600)" % priv_path)
    print("  public key:    %s (0644)" % pub_path)
    print("  signer_key_id: %s" % kid)
    print("  backend:       %s" % used)
    return kid


def _write(path, data, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


def main(argv):
    key_dir = None
    if len(argv) == 2 and argv[0] == "--key-dir":
        key_dir = argv[1]
    elif argv:
        sys.stderr.write("usage: keygen.py [--key-dir DIR]\n")
        return 2
    generate(key_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
