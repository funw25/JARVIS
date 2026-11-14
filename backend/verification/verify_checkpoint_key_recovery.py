"""Offline recovery of an ephemeral fictional checkpoint key; no files or accounts."""
import copy
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from usecases import checkpoint_key_recovery as recovery


def expect(call):
    try:
        call()
    except recovery.RecoveryError:
        return
    raise AssertionError("recovery unexpectedly succeeded")


key = Ed25519PrivateKey.generate()
public = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
password = "fictional offline passphrase number 7281"
package = recovery.create_package(key, password)
assert password not in json.dumps(package)
restored = recovery.recover_package(package, password, expected_public_key=public)
public_object = key.public_key()
public_object.verify(restored.sign(b"fictional-checkpoint"), b"fictional-checkpoint")
expect(lambda: recovery.recover_package(package, "incorrect fictional passphrase", expected_public_key=public))
expect(lambda: recovery.recover_package(package, password, expected_public_key=b"\x00" * 32))
expect(lambda: recovery.recover_package(package, password, expected_public_key=None))
substitute = recovery.create_package(Ed25519PrivateKey.generate(), password)
expect(lambda: recovery.recover_package(substitute, password, expected_public_key=public))
for field in ("salt", "nonce", "ciphertext"):
    altered = copy.deepcopy(package)
    altered[field] = ("00" if altered[field][:2] != "00" else "01") + altered[field][2:]
    expect(lambda: recovery.recover_package(altered, password, expected_public_key=public))
for name, value in (("memory_kib", 2**30), ("iterations", 1), ("parallelism", True)):
    altered = copy.deepcopy(package); altered["kdf"][name] = value
    expect(lambda: recovery.recover_package(altered, password, expected_public_key=public))
expect(lambda: recovery.create_package(key, "short"))

# A fresh Python process reconstructs the key only from the fictional package,
# passphrase and separately pinned public key; this is NOT a second machine.
child = """
import json,sys
sys.path.insert(0, %r)
from usecases import checkpoint_key_recovery as r
data=json.load(sys.stdin)
k=r.recover_package(data['package'],data['passphrase'],expected_public_key=bytes.fromhex(data['public']))
k.public_key().verify(k.sign(b'fictional'),b'fictional')
print('FRESH_PROCESS_RECOVERY=PASS')
""" % str(ROOT / "backend")
run = subprocess.run([sys.executable, "-c", child], input=json.dumps({"package": package, "passphrase": password,
                     "public": public.hex()}), text=True, capture_output=True, encoding="utf-8", timeout=30)
assert run.returncode == 0 and "FRESH_PROCESS_RECOVERY=PASS" in run.stdout, run.stderr
print("CHECKPOINT_KEY_PORTABLE_RECOVERY_AND_TAMPER_GUARDS=PASS")
print("CROSS_MACHINE_KEY_CUSTODY_AND_EXTERNAL_TRUST=NOT_VERIFIED")
