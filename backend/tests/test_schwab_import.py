"""Schwab import through another app's shared login (schwab_shared.py, schwab_import.py).

    cd backend && python -m pytest tests -q

The journal never signs in to Schwab and never writes the shared token file. Schwab TRADE
transactions become the same execution dicts as a Thinkorswim export, so P&L comes from
the same trade builder. Expected values below are worked by hand.
"""
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import schwab_import  # noqa: E402
import schwab_shared  # noqa: E402


def leg(symbol, asset, amount, price, cost, **ins):
    return {"instrument": {"assetType": asset, "symbol": symbol, **ins},
            "amount": amount, "price": price, "cost": cost}


def fee(cost):
    return {"instrument": {"assetType": "CURRENCY", "symbol": "CURRENCY_USD"}, "amount": 0, "cost": cost,
            "feeType": "COMMISSION"}


def txn(activity, when, *items, status="VALID", kind="TRADE"):
    return {"activityId": activity, "time": when, "type": kind, "status": status,
            "transferItems": list(items)}


# 2026-09-15 is EDT (UTC-4): 13:46:16Z is 09:46:16 in New York.
STOCK_DAY = [
    txn(1, "2026-09-15T13:46:16+0000", leg("TSLA", "EQUITY", 100, 250.00, -25000.00)),
    txn(2, "2026-09-15T13:50:48+0000", fee(-0.75), leg("TSLA", "EQUITY", -100, 250.50, 25050.00)),
]


# ── Transactions to executions ────────────────────────────────────────────────

def test_stock_round_trip_converts_time_side_amount_and_fees():
    ex, left_out = schwab_import.executions_from_transactions(STOCK_DAY)
    assert left_out == {}
    assert [(e["action"], e["qty"], e["price"], e["amount"], e["commission"], e["date"], e["time"])
            for e in ex] == [
        ("BOT", 100, 250.00, -25000.00, 0.0, "2026-09-15", "09:46:16"),
        ("SOLD", 100, 250.50, 25050.00, 0.75, "2026-09-15", "09:50:48"),
    ]
    assert {e["instrument_type"] for e in ex} == {"STOCK"}


def test_etf_counts_as_stock():
    ex, _ = schwab_import.executions_from_transactions(
        [txn(3, "2026-09-15T14:00:00+0000", leg("SOXL", "COLLECTIVE_INVESTMENT", 10, 30.0, -300.0))])
    assert ex[0]["instrument_type"] == "STOCK" and ex[0]["ticker"] == "SOXL"


def test_option_fields_come_from_the_occ_symbol():
    ex, _ = schwab_import.executions_from_transactions([txn(
        4, "2026-01-05T15:00:00+0000",   # EST (UTC-5): 10:00:00
        leg("COHR  270319P00260000", "OPTION", -2, 3.40, 680.0, underlyingSymbol="COHR"))])
    e, = ex
    assert (e["ticker"], e["option_expiry"], e["option_strike"], e["option_type"]) == \
        ("COHR", "2027-03-19", 260.0, "PUT")
    assert (e["action"], e["qty"], e["amount"], e["time"]) == ("SOLD", 2, 680.0, "10:00:00")


def test_future_keeps_root_and_multiplied_amount():
    # 1 MNQ at 21000.25 is $2/pt: cost -42000.50
    ex, _ = schwab_import.executions_from_transactions(
        [txn(5, "2026-09-15T13:31:00+0000", leg("/MNQZ26:XCME", "FUTURE", 1, 21000.25, -42000.50))])
    assert (ex[0]["ticker"], ex[0]["instrument_type"], ex[0]["amount"]) == ("/MNQZ26", "FUTURE", -42000.50)


def test_cancelled_non_trade_and_repeated_transactions_are_dropped():
    txns = STOCK_DAY + [STOCK_DAY[0],
                        txn(9, "2026-09-15T14:00:00+0000", leg("TSLA", "EQUITY", 5, 1.0, -5.0), status="CANCELED"),
                        txn(10, "2026-09-15T14:00:00+0000", leg("TSLA", "EQUITY", 5, 1.0, -5.0), kind="DIVIDEND")]
    ex, _ = schwab_import.executions_from_transactions(txns)
    assert len(ex) == 2


def test_asset_types_the_journal_cannot_hold_are_counted_not_hidden():
    ex, left_out = schwab_import.executions_from_transactions(
        [txn(11, "2026-09-15T14:00:00+0000", leg("912797XX1", "FIXED_INCOME", 1000, 99.5, -995.0))])
    assert ex == [] and left_out == {"FIXED_INCOME": 1}


def test_fetch_splits_the_range_and_refuses_a_full_page():
    calls = []

    def get(path, params):
        calls.append(params)
        return []
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    schwab_import.fetch_transactions("ABC123", 65, now=now, get=get)
    assert len(calls) == 3 and all(p["types"] == "TRADE" for p in calls)
    assert calls[0]["startDate"] == "2026-07-27T00:00:00.000Z"
    assert calls[-1]["endDate"] == "2026-09-30T00:00:00.000Z"

    with pytest.raises(ValueError, match="page limit"):
        schwab_import.fetch_transactions("ABC123", 5, now=now, get=lambda p, q: [{}] * 3000)
    with pytest.raises(ValueError):
        schwab_import.fetch_transactions("../orders", 5, now=now, get=get)


# ── Shared login ──────────────────────────────────────────────────────────────

def write_login(path, key, refresh="R1", access="A1", issued=None, access_expires_at=None):
    payload = {"creation_timestamp": issued or time.time() - 3600,
               "token": {"access_token": access, "refresh_token": refresh, "id_token": "I",
                         "expires_in": 1800, "expires_at": access_expires_at or time.time() + 1200,
                         "token_type": "Bearer", "scope": "api"}}
    path.write_bytes(Fernet(key).encrypt(json.dumps(payload).encode()))


@pytest.fixture
def shared(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    path = tmp_path / "token.fernet"
    monkeypatch.setenv("SCHWAB_SHARED_TOKEN_FILE", str(path))
    monkeypatch.setenv("SCHWAB_SHARED_TOKEN_KEY", key)
    monkeypatch.setenv("SCHWAB_SHARED_APP_NAME", "OpenVector")
    monkeypatch.setenv("SCHWAB_APP_KEY", "k" * 32)
    monkeypatch.setenv("SCHWAB_APP_SECRET", "s" * 16)
    monkeypatch.setattr(schwab_shared, "_ours", None)
    return path, key


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


def test_fresh_shared_access_token_is_used_without_calling_schwab(shared):
    path, key = shared
    write_login(path, key)
    assert schwab_shared.access_token(post=lambda *a, **k: pytest.fail("refreshed")) == "A1"


def test_stale_access_token_is_refreshed_in_memory_and_the_file_is_never_written(shared):
    path, key = shared
    write_login(path, key, access_expires_at=time.time() - 10)
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    sent = []

    def post(url, **kw):
        sent.append((url, kw["data"]))
        return Resp(200, {"access_token": "OURS", "expires_in": 1800})
    assert schwab_shared.access_token(post=post) == "OURS"
    assert sent == [(schwab_shared.TOKEN_URL, {"grant_type": "refresh_token", "refresh_token": "R1"})]
    assert schwab_shared.access_token(post=lambda *a, **k: pytest.fail("refreshed twice")) == "OURS"
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_login_older_than_seven_days_says_where_to_sign_in(shared):
    path, key = shared
    write_login(path, key, issued=time.time() - 7 * 86400 - 60)
    with pytest.raises(schwab_shared.AuthExpired, match="sign in to Schwab again in OpenVector"):
        schwab_shared.access_token(post=lambda *a, **k: pytest.fail("refreshed"))


def test_wrong_key_is_reported_without_the_secret(shared):
    path, key = shared
    write_login(path, key)
    other = Fernet.generate_key().decode()
    with pytest.raises(schwab_shared.AuthExpired) as e:
        schwab_shared.read_login(path, other)
    assert other not in str(e.value) and "InvalidToken" in str(e.value)


def test_refused_refresh_says_where_to_sign_in(shared):
    path, key = shared
    write_login(path, key, access_expires_at=time.time() - 10)
    with pytest.raises(schwab_shared.AuthExpired, match="OpenVector"):
        schwab_shared.access_token(post=lambda *a, **k: Resp(400, {"error": "invalid_grant"}))


def test_only_allowed_get_paths_leave_the_client(shared):
    path, key = shared
    write_login(path, key)
    for bad in ("/accounts/ABC/orders", "/accounts/ABC/transactions/1", "/userPreference"):
        with pytest.raises(PermissionError):
            schwab_shared.get(bad, get_fn=lambda *a, **k: pytest.fail("sent"))
    seen = []
    schwab_shared.get("/accounts/accountNumbers",
                      get_fn=lambda url, **kw: seen.append((url, kw["headers"]["Authorization"])) or Resp(200, []))
    assert seen == [(schwab_shared.API_BASE + "/accounts/accountNumbers", "Bearer A1")]


# ── Endpoint: import into the journal ─────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    db = tmp_path / "journal.db"
    monkeypatch.setenv("DATABASE_PATH", str(db))
    for name in ("database", "main"):
        sys.modules.pop(name, None)
    import database
    database.DB_PATH = str(db)
    import main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        conn = sqlite3.connect(db)
        if not conn.execute("SELECT 1 FROM accounts WHERE id=1").fetchone():
            conn.execute("INSERT INTO accounts (id, name, type) VALUES (1, 'Day', 'day_trading')")
            conn.commit()
        c.db = str(db)
        yield c


def test_import_stores_the_round_trip_and_skips_it_the_second_time(client, monkeypatch):
    monkeypatch.setattr(schwab_import, "fetch_transactions", lambda h, d, **kw: STOCK_DAY)
    r = client.post("/api/import-schwab", json={"account_id": 1, "schwab_account": "ABC123", "days": 7})
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1
    rows = sqlite3.connect(client.db).execute(
        "SELECT trade_group, side, gross_pnl, net_pnl, commissions FROM trades").fetchall()
    # 100 x (250.50 - 250.00) = 50.00 gross, less 0.75 fees = 49.25 net
    assert rows == [("2026-09-15_TSLA_STOCK_1", "LONG", 50.0, 49.25, 0.75)]

    again = client.post("/api/import-schwab", json={"account_id": 1, "schwab_account": "ABC123", "days": 7}).json()
    assert (again["imported"], again["skipped"]) == (0, 2)


def test_import_reports_left_out_fills(client, monkeypatch):
    monkeypatch.setattr(schwab_import, "fetch_transactions", lambda h, d, **kw: [
        txn(11, "2026-09-15T14:00:00+0000", leg("912797XX1", "FIXED_INCOME", 1000, 99.5, -995.0))])
    body = client.post("/api/import-schwab", json={"account_id": 1, "schwab_account": "ABC123"}).json()
    assert body["left_out"] == {"FIXED_INCOME": 1} and "1 FIXED_INCOME" in body["message"]


def test_expired_login_is_a_clear_error_and_nothing_is_stored(client, monkeypatch):
    def expired(*a, **k):
        raise schwab_shared.AuthExpired("The Schwab login ran out: sign in to Schwab again in OpenVector.")
    monkeypatch.setattr(schwab_import, "fetch_transactions", expired)
    r = client.post("/api/import-schwab", json={"account_id": 1, "schwab_account": "ABC123"})
    assert r.status_code == 400 and "OpenVector" in r.json()["error"]
    assert sqlite3.connect(client.db).execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


def test_status_without_setup_explains_what_to_set(client, monkeypatch):
    monkeypatch.delenv("SCHWAB_SHARED_TOKEN_FILE", raising=False)
    body = client.get("/api/schwab/status").json()
    assert body["configured"] is False and "SCHWAB_SHARED_TOKEN_FILE" in body["error"]


def test_status_shows_accounts_by_last_four_digits_only(shared, monkeypatch):
    path, key = shared
    write_login(path, key)
    st = schwab_import.status(get=lambda p: [{"accountNumber": "12345678", "hashValue": "H1"}])
    assert st["ok"] and st["accounts"] == [{"hash": "H1", "label": "Schwab ...5678"}]
    assert "12345678" not in json.dumps(st)


# ── Fill ids: twin fills that arrive in two imports ───────────────────────────
# One order sold in two identical pieces: same second, size and price, two Schwab
# activityIds. Automatic imports can see the first piece before the second.

TWIN_BUY = txn(21, "2026-09-15T13:46:16+0000", leg("TSLA", "EQUITY", 200, 250.00, -50000.00))
TWIN_A = txn(22, "2026-09-15T13:50:48+0000", fee(-0.75), leg("TSLA", "EQUITY", -100, 250.50, 25050.00))
TWIN_B = txn(23, "2026-09-15T13:50:48+0000", fee(-0.75), leg("TSLA", "EQUITY", -100, 250.50, 25050.00))


def _import(client, monkeypatch, txns):
    monkeypatch.setattr(schwab_import, "fetch_transactions", lambda h, d, **kw: txns)
    r = client.post("/api/import-schwab", json={"account_id": 1, "schwab_account": "ABC123", "days": 7})
    assert r.status_code == 200, r.text
    return r.json()


def test_fill_id_is_schwab_activity_id_and_leg():
    ex, _ = schwab_import.executions_from_transactions([TWIN_A])
    assert [e["fill_id"] for e in ex] == ["schwab:22:1"]


def test_identical_twin_fill_in_a_later_import_is_kept(client, monkeypatch):
    first = _import(client, monkeypatch, [TWIN_BUY, TWIN_A])
    assert first["imported"] == 1
    second = _import(client, monkeypatch, [TWIN_BUY, TWIN_A, TWIN_B])
    assert second["skipped"] == 2              # the buy and the first piece, by fill id
    rows = sqlite3.connect(client.db).execute(
        "SELECT trade_group, side, gross_pnl, net_pnl, commissions, executions FROM trades").fetchall()
    assert len(rows) == 1
    group, side, gross, net, fees, execs = rows[0]
    # 200 x (250.50 - 250.00) = 100.00 gross, fees 0.75 + 0.75 = 1.50, net 98.50
    assert (group, side, gross, net, fees) == ("2026-09-15_TSLA_STOCK_1", "LONG", 100.0, 98.5, 1.5)
    assert sorted(e["fill_id"] for e in json.loads(execs)) == ["schwab:21:1", "schwab:22:1", "schwab:23:1"]


def test_fill_stored_without_an_id_is_not_doubled_by_the_same_fill_with_one(client, monkeypatch):
    # Trades imported before fill ids (or from a CSV) carry no id: match them by fingerprint.
    no_ids = [dict(t, activityId=None) for t in STOCK_DAY]
    assert _import(client, monkeypatch, no_ids)["imported"] == 1
    again = _import(client, monkeypatch, STOCK_DAY)
    assert (again["imported"], again["skipped"]) == (0, 2)
    (net,), = sqlite3.connect(client.db).execute("SELECT net_pnl FROM trades").fetchall()
    assert net == 49.25
