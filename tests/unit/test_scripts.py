"""Regression tests for the shell helpers (run with plain bash, no Docker)."""
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run(snippet: str):
    return subprocess.run(["bash", "-c", f"source scripts/lib.sh; {snippet}"], cwd=ROOT, capture_output=True, text=True)


def test_envval_missing_key_does_not_kill_a_set_e_script():
    # An older .env without e.g. PUBLIC_HOST made `scripts/ibg.sh up` exit silently after the database step.
    r = run('h=$(envval THIS_KEY_DOES_NOT_EXIST_ANYWHERE); echo "survived:[$h]"')
    assert r.returncode == 0 and "survived:[]" in r.stdout, r.stderr


def test_envval_reads_a_known_key():
    r = run("envval MCC")
    assert r.returncode == 0 and r.stdout.strip() == "001"


def test_all_scripts_are_valid_bash():
    for name in sorted(os.listdir(os.path.join(ROOT, "scripts"))):
        if name.endswith(".sh"):
            assert subprocess.run(["bash", "-n", os.path.join("scripts", name)], cwd=ROOT).returncode == 0, name
