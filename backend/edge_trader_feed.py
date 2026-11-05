"""Automatic import of Schwab fills from Edge Trader (ashwinurao/edge-trader-for-schwab).

Ash, 2026-11-05: the journal should fetch trades by itself as they are made, and take
them from Edge Trader, which already polls Schwab for fills every 10 seconds (the two
apps are meant to become one). So the journal makes no Schwab calls for this and is not
another user of the shared Schwab login. Edge Trader serves its stored fills read-only
at GET /api/fills (companion/fills_feed.py there), as Schwab's own TRADE transactions.

Settings (backend/.env):

    EDGE_TRADER_URL          Edge Trader's address, e.g. http://192.168.68.69:8770. Unset: off.
    EDGE_TRADER_ACCOUNT_ID   the journal account that receives the fills (its id).
    EDGE_TRADER_POLL_S       seconds between pulls. Default 60, at least 15.
    EDGE_TRADER_FIRST_DAYS   days asked for on the first pull after a start. Default 30, at
                             most 60 (what Edge Trader keeps).

Later pulls ask for the last CORRECTION_DAYS days, the window Edge Trader re-reads from
Schwab for late corrections. Fills go through schwab_import.executions_from_transactions
and csv_parser.build_trades_from_executions, the same path as "Import from Schwab", so
grouping, fees, P&L and duplicate detection (by Schwab fill id) are the same.

When Edge Trader says its fill history is incomplete or its fills poll is stale, nothing
is imported: a journal with fills missing would look complete. The status says why.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone

import httpx

import schwab_import
from csv_parser import build_trades_from_executions

log = logging.getLogger(__name__)

CORRECTION_DAYS = 3
MAX_FIRST_DAYS = 60       # Edge Trader's LOOKBACK_DAYS (companion/poller.py)
MIN_POLL_S = 15
TIMEOUT_S = 10

# A manual import and an automatic one must not read and write the same trades at once.
IMPORT_LOCK = threading.Lock()


def _int_env(name: str, default: int, lo: int, hi: int | None = None) -> int:
    try:
        v = int(os.environ.get(name) or default)
    except ValueError:
        v = default
    return max(lo, min(v, hi)) if hi else max(lo, v)


def config() -> dict:
    url = (os.environ.get("EDGE_TRADER_URL") or "").strip().rstrip("/")
    raw_account = (os.environ.get("EDGE_TRADER_ACCOUNT_ID") or "").strip()
    return {
        "url": url,
        "account_id": int(raw_account) if raw_account.isdigit() else None,
        "poll_s": _int_env("EDGE_TRADER_POLL_S", 60, MIN_POLL_S),
        "first_days": _int_env("EDGE_TRADER_FIRST_DAYS", 30, 1, MAX_FIRST_DAYS),
    }


class Feed:
    """One background loop. `get` replaces httpx.get and `now` the clock in tests."""

    def __init__(self, store_trades, connect, get=None, now=None):
        self._store_trades = store_trades     # main._store_trades(conn, account_id, trades)
        self._connect = connect               # database.get_db
        self._get = get or httpx.get
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._first = True
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._status = {"state": "off", "message": None, "last_pull_at": None,
                        "last_ok_at": None, "last_new_fills": None, "last_imported": None}

    # ---- status ----------------------------------------------------------

    def status(self) -> dict:
        cfg = config()
        with self._lock:
            st = dict(self._status)
        if not cfg["url"]:
            st.update(state="off", message="Automatic import is off. Set EDGE_TRADER_URL and "
                      "EDGE_TRADER_ACCOUNT_ID in backend/.env to turn it on (see README).")
        elif st["state"] == "off":
            st.update(state="starting", message="The first pull from Edge Trader runs shortly.")
        return {**st, "enabled": bool(cfg["url"]), "url": cfg["url"] or None,
                "account_id": cfg["account_id"], "poll_s": cfg["poll_s"]}

    def _set(self, **kw) -> dict:
        with self._lock:
            self._status.update(kw, last_pull_at=self._now().isoformat(timespec="seconds"))
            return dict(self._status)

    # ---- one pull --------------------------------------------------------

    def pull_once(self) -> dict:
        """Ask Edge Trader for recent fills and import the new ones. Never raises."""
        cfg = config()
        if not cfg["url"]:
            return self.status()
        try:
            return self._pull(cfg)
        except Exception as e:      # the loop must never die; the status says what failed
            log.exception("Edge Trader pull failed")
            return self._set(state="error", message=f"Automatic import failed ({type(e).__name__}: "
                             f"{str(e)[:200]}). Nothing was imported; it tries again next pull.")

    def _pull(self, cfg: dict) -> dict:
        if cfg["account_id"] is None:
            return self._set(state="error", message="Set EDGE_TRADER_ACCOUNT_ID in backend/.env to "
                             "the journal account that should receive the fills.")
        days = cfg["first_days"] if self._first else CORRECTION_DAYS
        since = (self._now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            r = self._get(cfg["url"] + "/api/fills", params={"since": since}, timeout=TIMEOUT_S)
        except httpx.HTTPError as e:
            return self._set(state="error", message=f"Could not reach Edge Trader at {cfg['url']} "
                             f"({type(e).__name__}). Is it running? Nothing was imported.")
        if r.status_code != 200:
            try:
                why = r.json().get("error") or ""
            except ValueError:
                why = ""
            hint = (" Add the journal's address for Edge Trader to EDGE_TRADER_HOSTS there."
                    if r.status_code == 403 else "")
            return self._set(state="error", message=f"Edge Trader answered HTTP {r.status_code}"
                             f"{': ' + why if why else ''}.{hint} Nothing was imported.")
        body = r.json()
        if not body.get("complete"):
            if not body.get("history_complete"):
                why = "Edge Trader is still loading its fill history from Schwab"
            else:
                why = (f"Edge Trader's fills are not current (last update "
                       f"{body.get('transactions_ok_at') or 'never'}"
                       f"{', ' + body['transactions_error'] if body.get('transactions_error') else ''})")
            return self._set(state="waiting", message=f"{why}. Nothing was imported; it tries again "
                             f"next pull. If this lasts, check Edge Trader.")

        executions, left_out = schwab_import.executions_from_transactions(body.get("transactions") or [])
        with IMPORT_LOCK:
            conn = self._connect()
            try:
                if not conn.execute("SELECT 1 FROM accounts WHERE id=?", (cfg["account_id"],)).fetchone():
                    return self._set(state="error", message=f"Journal account {cfg['account_id']} "
                                     f"does not exist. Fix EDGE_TRADER_ACCOUNT_ID in backend/.env.")
                trades, skipped = build_trades_from_executions(executions, cfg["account_id"], conn)
                imported, errors = self._store_trades(conn, cfg["account_id"], trades)
            finally:
                conn.close()
        self._first = False
        new_fills = len(executions) - skipped
        msg = (f"{new_fills} new fill(s), {imported} trade group(s) written." if new_fills
               else "No new fills.")
        if left_out:
            # A journal missing fills must say so (see schwab_import.INSTRUMENT_TYPES).
            msg += " Not imported (asset type the journal does not hold): " + ", ".join(
                f"{n} {kind}" for kind, n in sorted(left_out.items())) + "."
        if errors:
            msg += f" {len(errors)} trade group(s) could not be saved: {errors[0]['error'][:120]}"
        now = self._now().isoformat(timespec="seconds")
        return self._set(state="error" if errors else "ok", message=msg, last_ok_at=now,
                         last_new_fills=new_fills, last_imported=imported)

    # ---- loop ------------------------------------------------------------

    def start(self) -> None:
        if not config()["url"] or self._thread:
            return
        self._thread = threading.Thread(target=self._run, name="edge-trader-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.pull_once()
            self._wake.wait(config()["poll_s"])
            self._wake.clear()
