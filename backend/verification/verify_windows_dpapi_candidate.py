"""Windows DPAPI probe with random disposable bytes; this script saves no key.

It does not integrate key management into Jarvis or test recovery across devices.
"""
import ctypes
import os
import secrets
import sys

if sys.platform != "win32":
    print("WINDOWS_DPAPI_PROBE=NOT_RUN (Windows is required)")
    raise SystemExit(2)

from ctypes import wintypes

class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

crypt = ctypes.WinDLL("crypt32", use_last_error=True)
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
crypt.CryptProtectData.argtypes = [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
crypt.CryptProtectData.restype = wintypes.BOOL
crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
crypt.CryptUnprotectData.restype = wintypes.BOOL
kernel.LocalFree.argtypes = [ctypes.c_void_p]
kernel.LocalFree.restype = ctypes.c_void_p


def blob(data):
    buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    return DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def transform(data, entropy, *, protect):
    source, source_buffer = blob(data)
    extra, extra_buffer = blob(entropy)
    result = DATA_BLOB()
    try:
        if protect:
            success = crypt.CryptProtectData(ctypes.byref(source), None, ctypes.byref(extra), None, None, 0x1, ctypes.byref(result))
        else:
            success = crypt.CryptUnprotectData(ctypes.byref(source), None, ctypes.byref(extra), None, None, 0x1, ctypes.byref(result))
        if not success:
            raise OSError(ctypes.get_last_error(), "DPAPI operation failed")
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.memset(source_buffer, 0, len(source_buffer))
        ctypes.memset(extra_buffer, 0, len(extra_buffer))
        if result.pbData:
            ctypes.memset(result.pbData, 0, result.cbData)
            kernel.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def must_fail(function):
    try:
        function()
    except OSError:
        return
    raise AssertionError("DPAPI accepted invalid input")


plaintext, entropy = secrets.token_bytes(32), secrets.token_bytes(32)
protected = transform(plaintext, entropy, protect=True)
assert protected and plaintext not in protected
assert transform(protected, entropy, protect=False) == plaintext
must_fail(lambda: transform(protected, secrets.token_bytes(32), protect=False))
corrupt = bytearray(protected)
corrupt[-1] ^= 1
must_fail(lambda: transform(bytes(corrupt), entropy, protect=False))
print("WINDOWS_DPAPI_RANDOM_ROUNDTRIP_AND_TAMPER_REJECTION=PASS")
print("CROSS_USER_RESTART_RECOVERY_AND_APP_INTEGRATION=NOT_TESTED")
