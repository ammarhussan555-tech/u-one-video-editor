"""U One - secure API key storage.

Keys are kept OUT of logs, error messages, exported projects and the UI
after saving. Preferred backend is the OS credential store (Windows
Credential Manager via ``keyring`` when installed); otherwise keys are kept
in a file with owner-only permissions inside the per-user app directory.
"""
from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path
from typing import Dict, Optional

from .app_paths import key_file_path

SERVICE = "U One"
ACCOUNT_PEXELS = "pexels_api_key"
ACCOUNT_PIXABAY = "pixabay_api_key"
ACCOUNT_GOOGLE_KEY = "google_api_key"
ACCOUNT_GOOGLE_CX = "google_cx"
ACCOUNT_SERPER = "serper_api_key"

_ACCOUNTS = {
    "pexels": ACCOUNT_PEXELS,
    "pixabay": ACCOUNT_PIXABAY,
    "google_key": ACCOUNT_GOOGLE_KEY,
    "google_cx": ACCOUNT_GOOGLE_CX,
    "serper": ACCOUNT_SERPER,
}

try:
    import keyring  # type: ignore
    _HAS_KEYRING = True
except Exception:
    _HAS_KEYRING = False


def _restricted_write(path: Path, data: bytes):
    path.write_bytes(data)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass


def _read_store() -> Dict[str, str]:
    p = key_file_path()
    if not p.is_file():
        return {}
    try:
        raw = p.read_bytes()
        # simple reversible obfuscation; the OS file ACL is the real protection
        blob = base64.b64decode(raw).decode("utf-8", "ignore")
        return json.loads(blob[4:]) if blob.startswith("U1K:") else {}
    except Exception:
        return {}


def _write_store(d: Dict[str, str]):
    blob = "U1K:" + json.dumps(d)
    _restricted_write(key_file_path(), base64.b64encode(blob.encode("utf-8")))


def set_key(which: str, value: str):
    value = (value or "").strip()
    account = _ACCOUNTS.get(which, ACCOUNT_PIXABAY)
    if _HAS_KEYRING:
        try:
            if value:
                keyring.set_password(SERVICE, account, value)
            else:
                keyring.delete_password(SERVICE, account)
            return
        except Exception:
            pass
    store = _read_store()
    if value:
        store[account] = value
    else:
        store.pop(account, None)
    _write_store(store)


def get_key(which: str) -> str:
    account = _ACCOUNTS.get(which, ACCOUNT_PIXABAY)
    if _HAS_KEYRING:
        try:
            v = keyring.get_password(SERVICE, account)
            if v:
                return v
        except Exception:
            pass
    return _read_store().get(account, "")


def has_keys() -> Dict[str, bool]:
    return {"pexels": bool(get_key("pexels")),
            "pixabay": bool(get_key("pixabay")),
            "google": bool(get_key("google_key") and get_key("google_cx")),
            "serper": bool(get_key("serper")),
            "reddit": bool(get_key("reddit_id") and get_key("reddit_secret"))}
