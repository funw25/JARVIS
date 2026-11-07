"""Experimental Windows user-scoped DPAPI wrapping for synthetic tests only.

This module is not wired to the application. Python cannot guarantee erasure of
all copies of key material in memory; it does not provide cross-device recovery.
"""
import ctypes
import sys
from ctypes import wintypes

from app.security.storage_policy import require_synthetic_storage

_ENTROPY = b"jarvis-synthetic-vault-dpapi-v1"


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(value: bytes):
    buf = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    return _Blob(len(value), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte))), buf


def _transform(value: bytes, entropy: bytes, *, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("This experimental key wrapper requires Windows")
    if not isinstance(value, bytes) or not 1 <= len(value) <= 16384:
        raise ValueError("Invalid synthetic key material")
    if not isinstance(entropy, bytes) or not 1 <= len(entropy) <= 256:
        raise ValueError("Invalid optional entropy")
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt.CryptProtectData.argtypes = [ctypes.POINTER(_Blob), wintypes.LPCWSTR, ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    crypt.CryptProtectData.restype = wintypes.BOOL
    crypt.CryptUnprotectData.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    crypt.CryptUnprotectData.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    source, source_buffer = _blob(value)
    extra, extra_buffer = _blob(entropy)
    result = _Blob()
    try:
        if protect:
            ok = crypt.CryptProtectData(ctypes.byref(source), None, ctypes.byref(extra), None, None, 0x1, ctypes.byref(result))
        else:
            ok = crypt.CryptUnprotectData(ctypes.byref(source), None, ctypes.byref(extra), None, None, 0x1, ctypes.byref(result))
        if not ok:
            raise OSError(ctypes.get_last_error(), "DPAPI operation failed")
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        ctypes.memset(source_buffer, 0, len(source_buffer))
        ctypes.memset(extra_buffer, 0, len(extra_buffer))
        if result.pbData:
            ctypes.memset(result.pbData, 0, result.cbData)
            kernel.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def protect_synthetic_key(key: bytes) -> bytes:
    require_synthetic_storage()
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("Synthetic vault key must be 32 bytes")
    return _transform(key, _ENTROPY, protect=True)


def unprotect_synthetic_key(wrapped: bytes) -> bytes:
    require_synthetic_storage()
    value = _transform(wrapped, _ENTROPY, protect=False)
    if len(value) != 32:
        raise ValueError("Invalid synthetic vault key")
    return value
