"""Automatic import from Edge Trader (backend/edge_trader_feed.py).

    cd backend && python -m pytest tests -q

Edge Trader serves Schwab's TRADE transactions at /api/fills with a `complete` flag. The
journal imports them through the same path as "Import from Schwab" and imports nothing
while Edge Trader's view is incomplete. Expected values below are worked by hand.
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import edge_trader_feed  # noqa: E402

NOW = datetime(2026, 9, 15, 15, 0, tzinfo=timezone.utc)


def leg(symbol, amount, price, cost):
    return {"instrument": {"assetType": "EQUITY", "symbol": symbol}, "amount": amount, "price": price, "cost": cost}


def fee(cost):
    return {"instrument": {"assetType": "CURRENCY", "symbol": "CURRENCY_USD"}, "amount": 0, "cost": cost}


def txn(activity, when, *items):
    return {"activityId": activity, "time": when, "type": "TRADE", "status": "VALID", "transferItems": list(items)}


# 2026-09-15 is EDT (UTC-4): 13:46:16Z is 09:46:16 in New York.
BUY = txn(1, "2026-09-15T13:46:16+0000", leg("TSLA", 200, 250.00, -50000.00))
SELL_A = txn(2, "2026-09-15T13:50:48+0000", fee(-0.75), leg("TSLA", -100, 250.50, 25050.00))
SELL_B = txn(3, "2026-09-15T13:50:48+0000", fee(-0.75), leg("TSLA", -100, 250.50, 25050.00))


class Resp:
    def __init__(self, code, body):
        self.status_code, self._body = code, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeEdgeTrader:
    """Answers /api/fills like companion/fills_feed.py and records what was asked."""

    def __init__(self):
        self.txns, self.complete, self.history = [], True, True
        self.code, self.error, self.asked = 200, None, []

    def __call__(self, url, params=None, timeout=None):
        self.asked.append((url, params["since"]))
        if self.error:
            raise self.error
        if self.code != 200:
            return Resp(self.code, {"error": "unexpected host"})
        return Resp(200, {"complete": self.complete, "history_complete": self.history,
                          "transactions_ok_at": "2026-09-15T14:59:00+00:00",
                          "transactions_error": None if self.complete else "HTTP 500",
                          "transactions": list(self.txns)})


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "journal.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    monkeypatch.setenv("EDGE_TRADER_URL", "http://192.168.1.10:8770/")
    monkeypatch.setenv("EDGE_TRADER_ACCOUNT_ID", "1")
    for name in ("database", "main"):
        sys.modules.pop(name, None)
    import database
    database.DB_PATH = str(db)
    import main
    database.init_db()
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO accounts (id, name, type) VALUES (1, 'Day', 'day_trading')")
    conn.commit()
    fake = FakeEdgeTrader()
    feed = edge_trader_feed.Feed(main._store_trades, database.get_db, get=fake, now=lambda: NOW)
    return feed, fake, str(db)


def trades(db):
    return sqlite3.connect(db).execute(
        "SELECT trade_group, side, gross_pnl, net_pnl, commissions, executions FROM trades").fetchall()


def test_off_without_a_url_and_nothing_is_asked(env, monkeypatch):
    feed, fake, _ = env
    monkeypatch.delenv("EDGE_TRADER_URL")
    st = feed.pull_once()
    assert st["enabled"] is False and st["state"] == "off" and "EDGE_TRADER_URL" in st["message"]
    assert fake.asked == []


def test_imports_new_fills_then_only_what_is_new(env):
    feed, fake, db = env
    fake.txns = [BUY, SELL_A]
    st = feed.pull_once()
    assert st["state"] == "ok" and st["last_new_fills"] == 2
    # The first pull asks for 30 days, later ones for the last 3 (Edge Trader's correction window).
    assert fake.asked[0] == ("http://192.168.1.10:8770/api/fills", "2026-08-16T15:00:00Z")

    fake.txns = [BUY, SELL_A, SELL_B]      # the second identical piece of the sell arrives
    st = feed.pull_once()
    assert fake.asked[1][1] == "2026-09-12T15:00:00Z"
    assert st["last_new_fills"] == 1
    (group, side, gross, net, fees, execs), = trades(db)
    # 200 x (250.50 - 250.00) = 100.00 gross, fees 0.75 + 0.75 = 1.50, net 98.50
    assert (group, side, gross, net, fees) == ("2026-09-15_TSLA_STOCK_1", "LONG", 100.0, 98.5, 1.5)
    assert len(json.loads(execs)) == 3

    st = feed.pull_once()
    assert st["last_new_fills"] == 0 and len(trades(db)) == 1


def test_incomplete_history_imports_nothing_and_keeps_the_first_window(env):
    feed, fake, db = env
    fake.txns, fake.complete, fake.history = [SELL_A], False, False
    st = feed.pull_once()
    assert st["state"] == "waiting" and "still loading" in st["message"]
    assert trades(db) == []
    fake.complete = fake.history = True
    feed.pull_once()
    assert fake.asked[1][1] == "2026-08-16T15:00:00Z"   # still the 30-day first pull


def test_stale_fills_poll_imports_nothing_and_says_so(env):
    feed, fake, db = env
    fake.txns, fake.complete = [BUY, SELL_A], False
    st = feed.pull_once()
    assert st["state"] == "waiting" and "not current" in st["message"] and "HTTP 500" in st["message"]
    assert trades(db) == []


def test_unreachable_edge_trader_is_an_error_not_a_crash(env):
    feed, fake, db = env
    fake.error = httpx.ConnectError("refused")
    st = feed.pull_once()
    assert st["state"] == "error" and "Could not reach Edge Trader" in st["message"]
    assert st["last_ok_at"] is None


def test_refused_host_names_the_setting_to_fix(env):
    feed, fake, _ = env
    fake.code = 403
    st = feed.pull_once()
    assert st["state"] == "error" and "EDGE_TRADER_HOSTS" in st["message"]


def test_missing_journal_account_is_refused(env, monkeypatch):
    feed, fake, db = env
    monkeypatch.setenv("EDGE_TRADER_ACCOUNT_ID", "7")
    fake.txns = [BUY, SELL_A]
    st = feed.pull_once()
    assert st["state"] == "error" and "Journal account 7" in st["message"]
    assert trades(db) == []
    monkeypatch.delenv("EDGE_TRADER_ACCOUNT_ID")
    assert "EDGE_TRADER_ACCOUNT_ID" in feed.pull_once()["message"]


def test_poll_interval_has_a_floor(monkeypatch):
    monkeypatch.setenv("EDGE_TRADER_POLL_S", "1")
    monkeypatch.setenv("EDGE_TRADER_FIRST_DAYS", "400")
    cfg = edge_trader_feed.config()
    assert (cfg["poll_s"], cfg["first_days"]) == (15, 60)


def test_status_endpoint_is_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("EDGE_TRADER_URL", raising=False)
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "j.db"))
    for name in ("database", "main"):
        sys.modules.pop(name, None)
    import database
    database.DB_PATH = str(tmp_path / "j.db")
    import main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        body = c.get("/api/edge-trader/status").json()
        assert body["enabled"] is False and body["state"] == "off"
        assert c.post("/api/edge-trader/pull").json()["state"] == "off"
