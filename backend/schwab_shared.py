"""Read-only Schwab access through another app's stored login.

Schwab allows one app per developer and keeps one valid sign-in per app: when a second
program signs in with the same app key, the first one's login is revoked. So the journal
never signs in. It reads the login another app keeps (a schwab-py token file, Fernet
encrypted), set with:

    SCHWAB_SHARED_TOKEN_FILE   path to that app's token file (only ever read)
    SCHWAB_SHARED_TOKEN_KEY    its Fernet key
    SCHWAB_SHARED_APP_NAME     that app's name, for "sign in again in ..." messages
    SCHWAB_APP_KEY / SCHWAB_APP_SECRET   the same Schwab app, to refresh an access token
                               when the shared one has run out (optional)

The shared file's access token is used while it is fresh. When it has run out, the
journal asks Schwab for its own access token with the shared refresh token and keeps it
in memory. Nothing is ever written to the shared file.

Only GET requests to the paths in _ALLOWED_PATHS leave this module: account numbers and
transactions. No orders, no streamer. Same approach as Edge Trader's shared login
(ashwinurao/edge-trader-for-schwab PR #2, companion/schwab.py).
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import httpx

API_BASE = "https://api.schwabapi.com/trader/v1"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
REFRESH_TOKEN_DAYS = 7          # Schwab refresh tokens last 7 days from sign-in
ACCESS_MARGIN_S = 60            # treat an access token this close to expiry as expired
TIMEOUT_S = 20

_ALLOWED_PATHS = (
    re.compile(r"^/accounts/accountNumbers$"),
    re.compile(r"^/accounts/[A-Za-z0-9]+/transactions$"),
)

_ours: dict | None = None       # {"refresh": ..., "access": ..., "expires_at": ...}


class AuthExpired(RuntimeError):
    """The shared login is missing, unreadable or ran out. The message says what to do."""


def token_file() -> Path | None:
    p = os.environ.get("SCHWAB_SHARED_TOKEN_FILE")
    return Path(os.path.expanduser(p)) if p else None


def configured() -> bool:
    return token_file() is not None


def app_name() -> str:
    return os.environ.get("SCHWAB_SHARED_APP_NAME") or "the app whose Schwab login the journal shares"


def signin_hint() -> str:
    return f"sign in to Schwab again in {app_name()}"


def read_login(path: Path | None = None, key: str | None = None) -> dict:
    """The shared login: refresh and access tokens, when each was issued or runs out."""
    path = path or token_file()
    key = key if key is not None else os.environ.get("SCHWAB_SHARED_TOKEN_KEY", "")
    if path is None:
        raise AuthExpired("Schwab is not set up: set SCHWAB_SHARED_TOKEN_FILE and "
                          "SCHWAB_SHARED_TOKEN_KEY in backend/.env (see README).")
    try:
        from cryptography.fernet import Fernet
        payload = json.loads(Fernet(key.encode()).decrypt(path.read_bytes()))
        tok = payload["token"]
        return {
            "refresh": tok["refresh_token"],
            "access": tok.get("access_token") or "",
            "access_expires_at": float(tok.get("expires_at") or 0),
            "issued_at": float(payload["creation_timestamp"]),
        }
    except Exception as e:
        # Never echo the file's contents or the key: the type of failure is enough.
        raise AuthExpired(f"Could not read the Schwab login from {app_name()} "
                          f"({type(e).__name__}). Check SCHWAB_SHARED_TOKEN_FILE and "
                          f"SCHWAB_SHARED_TOKEN_KEY.") from None


def hours_left(login: dict, now: float | None = None) -> float:
    now = time.time() if now is None else now
    return (login["issued_at"] + REFRESH_TOKEN_DAYS * 86400 - now) / 3600


def access_token(now: float | None = None, post=None) -> str:
    """A usable access token, or AuthExpired. `post` replaces httpx.post in tests."""
    global _ours
    now = time.time() if now is None else now
    login = read_login()
    if hours_left(login, now) <= 0:
        raise AuthExpired(f"The Schwab login ran out: {signin_hint()}.")
    if login["access"] and login["access_expires_at"] - now > ACCESS_MARGIN_S:
        return login["access"]
    if _ours and _ours["refresh"] == login["refresh"] and _ours["expires_at"] - now > ACCESS_MARGIN_S:
        return _ours["access"]
    key, secret = os.environ.get("SCHWAB_APP_KEY"), os.environ.get("SCHWAB_APP_SECRET")
    if not key or not secret:
        raise AuthExpired(f"The Schwab access token from {app_name()} has run out. Open "
                          f"{app_name()} so it refreshes, or set SCHWAB_APP_KEY and "
                          f"SCHWAB_APP_SECRET so the journal can refresh it.")
    r = (post or httpx.post)(TOKEN_URL, auth=(key, secret), timeout=TIMEOUT_S,
                             data={"grant_type": "refresh_token", "refresh_token": login["refresh"]})
    if r.status_code != 200:
        raise AuthExpired(f"Schwab refused the shared login (HTTP {r.status_code}): {signin_hint()}.")
    body = r.json()
    _ours = {"refresh": login["refresh"], "access": body["access_token"],
             "expires_at": now + int(body.get("expires_in") or 1800)}
    return _ours["access"]


def get(path: str, params: dict | None = None, get_fn=None):
    """GET one allowed Trader API path. Anything else is refused before it leaves."""
    if not any(p.match(path) for p in _ALLOWED_PATHS):
        raise PermissionError(f"Trading Journal: {path} is not allowed on the Schwab client")
    r = (get_fn or httpx.get)(API_BASE + path, params=params, timeout=TIMEOUT_S,
                              headers={"Authorization": f"Bearer {access_token()}",
                                       "Accept": "application/json"})
    if r.status_code == 401:
        raise AuthExpired(f"Schwab did not accept the login: {signin_hint()}.")
    if r.status_code != 200:
        raise ValueError(f"Schwab answered HTTP {r.status_code} for {path.split('/')[-1]}. "
                         f"Nothing was imported; try again in a minute.")
    return r.json()
