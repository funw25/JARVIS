"""Minimal Windows Credential Manager access for the backup passphrase.

Generic credentials only, stored for the current user. The secret never touches
the repository, a config file or the process environment.
"""
import ctypes
import sys
from ctypes import wintypes

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2
TARGET_PREFIX = "Jarvis:"
DEFAULT_TARGET = TARGET_PREFIX + "backup-passphrase"


class CredentialError(RuntimeError):
    code = "CREDENTIAL_ERROR"


class CredentialMissing(CredentialError):
    code = "CREDENTIAL_MISSING"


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


def _api():
    if sys.platform != "win32":
        raise CredentialError("Windows Credential Manager requires Windows")
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
    advapi.CredWriteW.restype = wintypes.BOOL
    advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(_Credential))]
    advapi.CredReadW.restype = wintypes.BOOL
    advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi.CredDeleteW.restype = wintypes.BOOL
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    advapi.CredFree.restype = None
    return advapi


def store_secret(secret, target=DEFAULT_TARGET):
    """Save a secret as a generic credential owned by the current user."""
    if not isinstance(secret, str) or not secret:
        raise CredentialError("secret must be a non-empty string")
    blob = secret.encode("utf-8")
    buffer = (ctypes.c_byte * len(blob)).from_buffer_copy(blob)
    credential = _Credential()
    credential.Type = CRED_TYPE_GENERIC
    credential.TargetName = target
    credential.Comment = "Jarvis backup passphrase"
    credential.CredentialBlobSize = len(blob)
    credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = ""
    advapi = _api()
    if not advapi.CredWriteW(ctypes.byref(credential), 0):
        raise CredentialError("CredWriteW failed with %d" % ctypes.get_last_error())
    ctypes.memset(buffer, 0, len(buffer))
    return target


def read_secret(target=DEFAULT_TARGET):
    advapi = _api()
    pointer = ctypes.POINTER(_Credential)()
    if not advapi.CredReadW(target, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        error = ctypes.get_last_error()
        if error in (1168, 2):  # ERROR_NOT_FOUND / ERROR_FILE_NOT_FOUND
            raise CredentialMissing("no credential stored for %s" % target)
        raise CredentialError("CredReadW failed with %d" % error)
    try:
        credential = pointer.contents
        size = credential.CredentialBlobSize
        raw = ctypes.string_at(credential.CredentialBlob, size) if size else b""
        return raw.decode("utf-8")
    finally:
        advapi.CredFree(ctypes.cast(pointer, ctypes.c_void_p))


def delete_secret(target=DEFAULT_TARGET):
    advapi = _api()
    if not advapi.CredDeleteW(target, CRED_TYPE_GENERIC, 0):
        error = ctypes.get_last_error()
        if error in (1168, 2):
            return False
        raise CredentialError("CredDeleteW failed with %d" % error)
    return True


def has_secret(target=DEFAULT_TARGET):
    try:
        read_secret(target)
        return True
    except CredentialMissing:
        return False
