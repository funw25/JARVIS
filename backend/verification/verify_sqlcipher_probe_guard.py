"""Ensure the candidate probe refuses a fake driver backed by plain SQLite."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix="jarvis-plaintext-stub-") as directory:
    root = Path(directory)
    package = root / "sqlcipher3"
    package.mkdir()
    (package / "__init__.py").write_text("from . import dbapi2\n", encoding="utf-8")
    (package / "dbapi2.py").write_text("from sqlite3 import *\n", encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(root), TEMP=str(root), TMP=str(root), TMPDIR=str(root))
    wrapper = "import runpy,sys; sys.path.insert(0,sys.argv[1]); sys.argv=sys.argv[2:]; runpy.run_path(sys.argv[0],run_name='__main__')"
    result = subprocess.run([sys.executable, "-c", wrapper, str(root), str(ROOT / "backend" / "verification" / "verify_sqlcipher_candidate.py")],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "SQLCipher version unavailable" in result.stderr
    assert "=PASS" not in result.stdout
    assert sorted(item.name for item in root.iterdir()) == ["sqlcipher3"]
print("PLAINTEXT_SQLITE_IMPERSONATION_REJECTED=PASS")
