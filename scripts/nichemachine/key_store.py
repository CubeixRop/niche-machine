"""User-scoped API key storage using Windows Credential Manager.

Only non-secret labels and opaque ids are kept in the app manifest. Secret values
are written to the current Windows user's Generic Credentials vault.
"""
from __future__ import annotations

import ctypes
import hmac
import json
import os
from ctypes import wintypes
from pathlib import Path

from .util import APP

MANIFEST = APP / "gemini-key-profiles.json"
_TARGET_PREFIX = "NicheMachine.Gemini."


class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD), ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR),
        ("LastWritten", _FILETIME), ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR),
    ]


def persistent_supported() -> bool:
    return os.name == "nt"


def _api():
    if not persistent_supported():
        raise RuntimeError("Persistent Gemini key storage requires Windows Credential Manager.")
    dll = ctypes.WinDLL("advapi32", use_last_error=True)
    dll.CredWriteW.argtypes = [ctypes.POINTER(_CREDENTIALW), wintypes.DWORD]
    dll.CredWriteW.restype = wintypes.BOOL
    dll.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                              ctypes.POINTER(ctypes.POINTER(_CREDENTIALW))]
    dll.CredReadW.restype = wintypes.BOOL
    dll.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    dll.CredDeleteW.restype = wintypes.BOOL
    dll.CredFree.argtypes = [ctypes.c_void_p]
    dll.CredFree.restype = None
    return dll


def save_secret(profile_id: str, secret: str) -> None:
    dll = _api()
    blob = secret.encode("utf-8")
    if not blob or len(blob) > 2560:
        raise ValueError("Gemini key is empty or too long for Windows Credential Manager.")
    buffer = ctypes.create_string_buffer(blob)
    item = _CREDENTIALW()
    item.Type = 1  # CRED_TYPE_GENERIC
    item.TargetName = _TARGET_PREFIX + profile_id
    item.CredentialBlobSize = len(blob)
    item.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    item.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE (this Windows user on this PC)
    item.UserName = "Niche Machine Gemini"
    if not dll.CredWriteW(ctypes.byref(item), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def read_secret(profile_id: str) -> str | None:
    dll = _api()
    credential = ctypes.POINTER(_CREDENTIALW)()
    if not dll.CredReadW(_TARGET_PREFIX + profile_id, 1, 0, ctypes.byref(credential)):
        error = ctypes.get_last_error()
        if error == 1168:  # ERROR_NOT_FOUND
            return None
        raise ctypes.WinError(error)
    try:
        item = credential.contents
        return ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize).decode("utf-8")
    finally:
        dll.CredFree(credential)


def delete_secret(profile_id: str) -> None:
    dll = _api()
    if not dll.CredDeleteW(_TARGET_PREFIX + profile_id, 1, 0):
        error = ctypes.get_last_error()
        if error != 1168:
            raise ctypes.WinError(error)


def contains_secret(secret: str) -> bool:
    """Check this app's Credential Manager entries without returning key values."""
    dll = _api()
    credential_pointer = ctypes.POINTER(_CREDENTIALW)
    credentials_pointer = ctypes.POINTER(credential_pointer)
    credentials = credentials_pointer()
    count = wintypes.DWORD()
    dll.CredEnumerateW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                   ctypes.POINTER(credentials_pointer)]
    dll.CredEnumerateW.restype = wintypes.BOOL
    if not dll.CredEnumerateW(_TARGET_PREFIX + "*", 0, ctypes.byref(count), ctypes.byref(credentials)):
        error = ctypes.get_last_error()
        if error == 1168:  # no app-managed credentials yet
            return False
        raise ctypes.WinError(error)
    try:
        target = secret.encode("utf-8")
        for index in range(count.value):
            item = credentials[index].contents
            raw = ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize)
            if hmac.compare_digest(raw, target):
                return True
        return False
    finally:
        dll.CredFree(credentials)


def load_profiles() -> tuple[list[dict], dict[str, str]]:
    """Return safe profile metadata and any available secrets from Credential Manager."""
    if not MANIFEST.exists():
        return [], {}
    try:
        profiles = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("The saved Gemini key profile file is unreadable; it was left unchanged.") from exc
    if not isinstance(profiles, list):
        raise RuntimeError("The saved Gemini key profile file has an invalid format; it was left unchanged.")
    secrets: dict[str, str] = {}
    for profile in profiles:
        if not isinstance(profile, dict) or not profile.get("id"):
            continue
        value = read_secret(str(profile["id"]))
        if value:
            secrets[str(profile["id"])] = value
    return profiles, secrets


def save_profiles(profiles: list[dict]) -> None:
    APP.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(profiles, indent=2), encoding="utf-8")
    tmp.replace(MANIFEST)
