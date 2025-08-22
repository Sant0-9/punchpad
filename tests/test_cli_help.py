import os
import subprocess
import sys

def test_cli_help_and_version():
    env = os.environ.copy()
    # Ensure test uses an isolated data dir so DB/logs don't interfere
    import tempfile
    env["PUNCHPAD_DATA_DIR"] = tempfile.mkdtemp(prefix="punchpad_cli_")

    # Help should print usage and exit 0
    res_help = subprocess.run([sys.executable, "-m", "punchpad_app", "--help"], capture_output=True, text=True, env=env, timeout=15)
    assert res_help.returncode == 0
    assert "usage" in res_help.stdout.lower()

    # Version should print 0.1.0 and exit 0
    res_ver = subprocess.run([sys.executable, "-m", "punchpad_app", "--version"], capture_output=True, text=True, env=env, timeout=15)
    assert res_ver.returncode == 0
    assert "0.1.0" in (res_ver.stdout + res_ver.stderr)
