"""Verify the isolated demo in a separate process using an isolated temp root."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix="jarvis-demo-verification-") as directory:
    env = dict(os.environ, TEMP=directory, TMP=directory, TMPDIR=directory,
               JARVIS_DATA_MODE="personal", JARVIS_DATA_DIR=str(Path(directory) / "must-not-be-used"))
    direct = subprocess.run([sys.executable, str(ROOT / "backend" / "demo_jarvis.py"), "--json"],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    if direct.returncode != 0:
        raise RuntimeError("Direct demo startup failed: " + direct.stderr)
    assert json.loads(direct.stdout)["synthetic_only"]
    assert list(Path(directory).iterdir()) == [], "direct startup left temporary files"
    wrapper = """import runpy, socket, sys
from pathlib import Path

def blocked(*args, **kwargs):
    raise AssertionError('Demo attempted network access')

socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
socket.getaddrinfo = blocked
sys.argv = sys.argv[1:]
sys.path.insert(0, str(Path(sys.argv[0]).parent))
runpy.run_path(sys.argv[0], run_name='__main__')
"""
    completed = subprocess.run([sys.executable, "-c", wrapper, str(ROOT / "backend" / "demo_jarvis.py"), "--json"],
                               cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    if completed.returncode != 0:
        raise RuntimeError("Demo subprocess failed: " + completed.stderr)
    result = json.loads(completed.stdout)
    assert result["synthetic_only"] and result["temporary_database"]
    assert not result["external_connections"] and not result["actual_send_available"]
    assert result["approvals_are_simulated"] and result["local_fake_model"]
    assert result["mail_needs_reply"] == 1 and result["open_todos"] == 2
    assert result["knowledge_notes"] == 1 and result["mail_source_available"]
    assert result["pending_send_proposals"] == 1 and result["proposal_status"] == "pending"
    assert set(result["task_titles"]) == {"Review project plan", "Organize idea"}
    assert list(Path(directory).iterdir()) == [], "temporary database was not removed"
print("DIRECT_SYNTHETIC_DEMO_AND_CLEANUP=PASS")
print("NETWORK_BLOCKED_SYNTHETIC_DEMO=PASS")
