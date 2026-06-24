"""End-to-end tests for the parts of the harness that run offline.

These drive the real command-line entry points in a temp workspace — no network,
no secrets, no writes outside the temp dir. They cover the two claims the README
makes: the statistical pipeline runs and decides correctly on synthetic data, and
the record signing chain round-trips and detects tampering.
"""

import json
import subprocess
import sys

# The package is installed (pip install -e .), so invoke modules with -m.
ANALYSIS = ("-m", "trustladder.analysis.analysis")
KEYGEN = ("-m", "trustladder.schema.signing.keygen")
DEMO_CHAIN = ("-m", "trustladder.schema.signing.demo_chain")
VERIFY_CHAIN = ("-m", "trustladder.schema.signing.verify_chain")


def run(*args):
    flat = []
    for a in args:
        flat.extend(a) if isinstance(a, tuple) else flat.append(str(a))
    return subprocess.run([sys.executable, *flat], capture_output=True, text=True)


def _confirmatory(workspace, scenario):
    """Run the fixed pipeline order the harness enforces: fabricate -> validity
    (writes the verdict the confirmatory step requires) -> confirmatory."""
    r = run(ANALYSIS, "dummy", "--workspace", workspace, "--scenario", scenario)
    assert r.returncode == 0, r.stderr
    r = run(ANALYSIS, "validity", "--workspace", workspace)
    assert r.returncode == 0, r.stderr
    return run(ANALYSIS, "confirmatory", "--workspace", workspace)


def test_validity_gates_pass_on_synthetic(tmp_path):
    ws = tmp_path / "ws"
    r = run(ANALYSIS, "dummy", "--workspace", ws, "--scenario", "confirmed")
    assert r.returncode == 0, r.stderr
    r = run(ANALYSIS, "validity", "--workspace", ws)
    assert r.returncode == 0, r.stderr


def test_verify_verbatim_refuses_cleanly_without_the_private_prereg(tmp_path):
    """verify-verbatim and readouts need the unshipped preregistration. They
    must refuse with an actionable message, not a raw traceback."""
    for command in ("verify-verbatim", "readouts"):
        r = run(ANALYSIS, command, "--workspace", tmp_path)
        assert r.returncode == 2, (command, r.stdout, r.stderr)
        assert "Traceback" not in r.stderr, command
        assert "not shipped in this public copy" in r.stderr, command


def test_all_completes_and_skips_readouts_on_the_public_copy(tmp_path):
    """`all` runs the statistical core end-to-end and skips only the readouts
    step (which needs the private prereg), rather than crashing."""
    ws = tmp_path / "ws"
    assert run(ANALYSIS, "dummy", "--workspace", ws, "--scenario", "confirmed").returncode == 0
    r = run(ANALYSIS, "all", "--workspace", ws)
    assert r.returncode == 0, r.stderr
    assert "[READOUTS] skipped" in r.stdout
    assert "[PIPELINE] complete" in r.stdout


def test_confirmatory_refuses_before_validity(tmp_path):
    """The unblinding order is structural: the confirmatory contrast refuses to
    run until a validity verdict exists. This is a feature — verify it holds."""
    ws = tmp_path / "ws"
    r = run(ANALYSIS, "dummy", "--workspace", ws, "--scenario", "confirmed")
    assert r.returncode == 0, r.stderr
    r = run(ANALYSIS, "confirmatory", "--workspace", ws)
    assert r.returncode != 0, "confirmatory should refuse without a validity verdict"


def test_confirmed_scenario_confirms_h1(tmp_path):
    r = _confirmatory(tmp_path / "ws", "confirmed")
    assert r.returncode == 0, r.stderr
    assert "h1_confirmed=True" in r.stdout, r.stdout
    # the headline lines must be tagged SYNTHETIC so a cropped screenshot of the
    # effect/outcome can't be mistaken for a real result.
    for line in r.stdout.splitlines():
        if line.startswith("[H1]") and ("effect=" in line or "outcome=" in line):
            assert "SYNTHETIC" in line, line


def test_refuted_scenario_does_not_confirm_h1(tmp_path):
    """The pipeline must NOT confirm when the synthetic effect is refuted (the
    interval sits below the floor) — a guard against a rule that always says
    'confirmed'."""
    r = _confirmatory(tmp_path / "ws", "refuted")
    assert r.returncode == 0, r.stderr
    assert "h1_confirmed=True" not in r.stdout, r.stdout


def test_signing_chain_roundtrips_and_detects_tampering(tmp_path):
    out = tmp_path / "chain"
    keys = tmp_path / "keys"
    # Generate a throwaway signing key, then build + sign 3 records and verify.
    g = run(KEYGEN, "--key-dir", keys)
    assert g.returncode == 0, g.stderr
    r = run(DEMO_CHAIN, out, "--key-dir", keys)
    assert r.returncode == 0, r.stderr
    head = out / "chain_head.json"
    v = run(VERIFY_CHAIN, "--records-dir", out, "--chain-head", head, "--key-dir", keys)
    assert v.returncode == 0, v.stdout + v.stderr

    # Tamper with a signed record; verification must now fail.
    records = sorted(p for p in out.glob("*.json") if p.name != "chain_head.json")
    assert records, "no records produced by the signing demo"
    rec = json.loads(records[0].read_text())
    rec["claim"]["text"] = "tampered"
    records[0].write_text(json.dumps(rec))
    v2 = run(VERIFY_CHAIN, "--records-dir", out, "--chain-head", head, "--key-dir", keys)
    assert v2.returncode != 0, "verification should fail on a tampered record"
