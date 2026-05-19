"""
firebase_helper.py
Pure REST client for Firebase Realtime Database.
Uses /<path>.json  —  NO service account, NO SDK, NO API key needed.
Set FIREBASE_SECRET (DB legacy secret) only if your DB rules are NOT public.
"""
import requests
from config import FIREBASE_URL, FIREBASE_SECRET

TIMEOUT = 12

def _url(path: str) -> str:
    base = f"{FIREBASE_URL.rstrip('/')}/{path}.json"
    if FIREBASE_SECRET:
        return f"{base}?auth={FIREBASE_SECRET}"
    return base

# ── CRUD ────────────────────────────────────────────────────────────────────

def get(path: str):
    try:
        r = requests.get(_url(path), timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        print(f"[FB GET  ] {path} → {e}")
        return None

def put(path: str, data):
    """Overwrite a node completely."""
    try:
        r = requests.put(_url(path), json=data, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        print(f"[FB PUT  ] {path} → {e}")
        return None

def patch(path: str, data: dict):
    """Merge / update specific fields."""
    try:
        r = requests.patch(_url(path), json=data, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception as e:
        print(f"[FB PATCH] {path} → {e}")
        return None

def post(path: str, data):
    """Push a new child with an auto-generated key."""
    try:
        r = requests.post(_url(path), json=data, timeout=TIMEOUT)
        if r.status_code == 200:
            return r.json()          # returns {"name": "<generated-key>"}
        return None
    except Exception as e:
        print(f"[FB POST ] {path} → {e}")
        return None

def delete(path: str) -> bool:
    try:
        r = requests.delete(_url(path), timeout=TIMEOUT)
        return r.status_code == 200
    except Exception as e:
        print(f"[FB DEL  ] {path} → {e}")
        return False

# ── Convenience helpers ──────────────────────────────────────────────────────

def get_list(path: str) -> list:
    """Return list of {_id, **fields} dicts from a Firebase dict node."""
    data = get(path)
    if not data or not isinstance(data, dict):
        return []
    out = []
    for k, v in data.items():
        if isinstance(v, dict):
            out.append({"_id": k, **v})
        else:
            out.append({"_id": k, "value": v})
    return out

def get_setting(key: str, default=None):
    val = get(f"settings/{key}")
    return val if val is not None else default

def set_setting(key: str, value):
    return put(f"settings/{key}", value)
