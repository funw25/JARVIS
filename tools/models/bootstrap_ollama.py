"""Fetch and verify a pinned official Ollama Windows CLI archive locally.

Run this only when the user has chosen the local Ollama route. The runtime stays
under the ignored .jarvis-ollama directory; this script does not install a
Windows service, sign in, or start a model.
"""
import hashlib
import os
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / ".jarvis-ollama"
VERSION = "0.40.15"
URL = ("https://github.com/ollama/ollama/releases/download/"
       f"v{VERSION}/ollama-windows-amd64.zip")
EXPECTED_SHA256 = "9ca74a7ab66ec86870b1eecac7df160df08dc4e3a291ab5d2d59bcf9310221c6"
EXPECTED_BYTES = 1614941966
ARCHIVE = BASE / f"ollama-windows-amd64-v{VERSION}.zip"
PART = BASE / (ARCHIVE.name + ".part")
RUNTIME = BASE / f"runtime-v{VERSION}"


def verify(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(4 * 1024 * 1024):
            digest.update(block)
    return path.stat().st_size == EXPECTED_BYTES and digest.hexdigest() == EXPECTED_SHA256


def download():
    if ARCHIVE.is_file() and verify(ARCHIVE):
        print("OLLAMA_ARCHIVE=PASS cached verified archive")
        return
    offset = PART.stat().st_size if PART.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    request = urllib.request.Request(URL, headers=headers)
    proxy = os.environ.get("JARVIS_GITHUB_PROXY", "http://127.0.0.1:7890")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy}))
    with opener.open(request, timeout=60) as response:
        if offset and response.status != 206:
            raise RuntimeError("server did not resume the partial archive; leave .part for inspection")
        with PART.open("ab" if offset else "wb") as target:
            while block := response.read(4 * 1024 * 1024):
                target.write(block)
                if target.tell() // (256 * 1024 * 1024) != (target.tell() - len(block)) // (256 * 1024 * 1024):
                    print("OLLAMA_DOWNLOAD_MB=%d" % (target.tell() // (1024 * 1024)), flush=True)
    if not verify(PART):
        raise RuntimeError("Ollama archive size or SHA-256 mismatch; archive not used")
    PART.replace(ARCHIVE)
    print("OLLAMA_ARCHIVE=PASS sha256=%s" % EXPECTED_SHA256)


def extract():
    if (RUNTIME / "ollama.exe").is_file():
        print("OLLAMA_RUNTIME=PASS existing executable")
        return
    if RUNTIME.exists():
        raise RuntimeError("incomplete runtime directory exists; inspect it before retrying")
    RUNTIME.mkdir(parents=True)
    with zipfile.ZipFile(ARCHIVE) as bundle:
        bundle.extractall(RUNTIME)
    if not (RUNTIME / "ollama.exe").is_file():
        raise RuntimeError("verified archive did not place ollama.exe at runtime root")
    print("OLLAMA_RUNTIME=PASS path=%s" % (RUNTIME / "ollama.exe"))


def main():
    BASE.mkdir(parents=True, exist_ok=True)
    download()
    extract()


if __name__ == "__main__":
    main()
