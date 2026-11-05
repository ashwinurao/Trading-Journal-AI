"""Import fills straight from Schwab, using the shared login in schwab_shared.py.

Schwab TRADE transactions are turned into the same execution dicts the CSV parsers
produce, then go through csv_parser.build_trades_from_executions, so grouping, duplicate
detection and P&L work exactly as for a Thinkorswim export.

Each TRADE carries one or more instrument transferItems (signed `amount` = quantity,
`price`, signed cash `cost`) plus CURRENCY items for fees. `cost` is signed like the
Thinkorswim AMOUNT column (buys negative, sells positive) and already includes the
contract multiplier. Fees ride on the first instrument leg of their transaction, the
same rule Edge Trader uses (companion/pnl.py).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import schwab_shared
from csv_parser import build_trades_from_executions

ET = ZoneInfo("America/New_York")
CHUNK_DAYS = 30
MAX_DAYS = 365
# Schwab returns at most this many transactions per request. A full page may be cut
# off, and a journal with fills silently missing looks complete, so the import refuses.
PAGE_LIMIT = 3000

# Schwab asset types the journal can hold. ETFs come as COLLECTIVE_INVESTMENT.
INSTRUMENT_TYPES = {
    "EQUITY": "STOCK",
    "COLLECTIVE_INVESTMENT": "STOCK",
    "OPTION": "OPTION",
    "FUTURE": "FUTURE",
}

# OCC option symbol: root padded to 6, YYMMDD, C/P, strike x 1000 in 8 digits.
_OCC = re.compile(r"^([A-Z0-9.]{1,6})\s*(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


def _qty(x: float):
    return int(x) if float(x).is_integer() else round(x, 6)


def _option_fields(ins: dict) -> dict | None:
    m = _OCC.match((ins.get("symbol") or "").upper())
    if m:
        root, yy, mm, dd, cp, strike = m.groups()
        return {"ticker": ins.get("underlyingSymbol") or root,
                "option_expiry": f"20{yy}-{mm}-{dd}",
                "option_strike": int(strike) / 1000,
                "option_type": "CALL" if cp == "C" else "PUT"}
    if ins.get("underlyingSymbol") and ins.get("strikePrice") and ins.get("putCall") and ins.get("expirationDate"):
        return {"ticker": ins["underlyingSymbol"],
                "option_expiry": str(ins["expirationDate"])[:10],
                "option_strike": float(ins["strikePrice"]),
                "option_type": str(ins["putCall"]).upper()}
    return None


def executions_from_transactions(txns: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """(execution dicts, {Schwab asset type: legs left out}) for a list of transactions."""
    out: list[dict] = []
    left_out: dict[str, int] = {}
    seen: set[str] = set()
    for t in txns:
        if t.get("type") != "TRADE" or t.get("status") not in (None, "VALID"):
            continue
        if t.get("activityId") is not None:
            if str(t["activityId"]) in seen:      # chunks overlap at their edges
                continue
            seen.add(str(t["activityId"]))
        items = t.get("transferItems") or []
        fees = sum(float(i.get("cost") or 0) for i in items
                   if (i.get("instrument") or {}).get("assetType") == "CURRENCY")
        when = datetime.strptime(t["time"], "%Y-%m-%dT%H:%M:%S%z").astimezone(ET)
        first = True
        leg = 0
        for it in items:
            ins = it.get("instrument") or {}
            kind = ins.get("assetType")
            if kind in (None, "CURRENCY"):
                continue
            qty = float(it.get("amount") or 0)
            price = float(it.get("price") or 0)
            if abs(qty) < 1e-9 or price <= 0:
                continue
            instr = INSTRUMENT_TYPES.get(kind)
            fields = {"ticker": (ins.get("symbol") or "").upper(), "option_expiry": None,
                      "option_strike": None, "option_type": None}
            if instr == "OPTION":
                fields = _option_fields(ins)
            elif instr == "FUTURE":
                fields["ticker"] = fields["ticker"].split(":")[0]
            if not instr or not fields or not fields["ticker"]:
                left_out[kind] = left_out.get(kind, 0) + 1
                continue
            leg += 1
            action = "BOT" if qty > 0 else "SOLD"
            cost = float(it.get("cost") or 0)
            amount = cost if cost else (-1 if qty > 0 else 1) * abs(qty) * price
            iso_date = when.date().isoformat()
            out.append({
                "action": action,
                "qty": _qty(abs(qty)),
                "price": price,
                "instrument_type": instr,
                **fields,
                "date": iso_date,
                "iso_date": iso_date,
                "time": when.strftime("%H:%M:%S"),
                "amount": round(amount, 2),
                "commission": round(abs(min(fees, 0.0)), 2) if first else 0.0,
                "raw_description": f"{action} {_qty(abs(qty))} {ins.get('symbol')} @{price} (Schwab)",
                # Schwab's id for the fill, so a later import knows it (csv_parser.get_existing_fill_keys).
                **({"fill_id": f"schwab:{t['activityId']}:{leg}"} if t.get("activityId") is not None else {}),
            })
            first = False
    return out, left_out


def accounts(get=None) -> list[dict]:
    """Schwab accounts as {hash, label}. The full account number never leaves the server."""
    rows = (get or schwab_shared.get)("/accounts/accountNumbers")
    return [{"hash": r["hashValue"], "label": f"Schwab ...{str(r['accountNumber'])[-4:]}"} for r in rows]


def fetch_transactions(account_hash: str, days: int, now: datetime | None = None, get=None) -> list[dict]:
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"Pick between 1 and {MAX_DAYS} days.")
    if not re.fullmatch(r"[A-Za-z0-9]+", account_hash or ""):
        raise ValueError("Pick a Schwab account.")
    get = get or schwab_shared.get
    end = now or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    txns: list[dict] = []
    while start < end:
        stop = min(start + timedelta(days=CHUNK_DAYS), end)
        page = get(f"/accounts/{account_hash}/transactions", {
            "startDate": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "endDate": stop.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "types": "TRADE",
        })
        if len(page) >= PAGE_LIMIT:
            raise ValueError(f"Schwab returned {len(page)} transactions for {start.date()} to "
                             f"{stop.date()}, its page limit, so some may be missing. Nothing "
                             f"was imported. Import a shorter range.")
        txns.extend(page)
        start = stop
    return txns


def import_trades(account_id: int, account_hash: str, days: int, conn, get=None) -> tuple[list[dict], int, dict]:
    """(trade dicts ready for DB insert, duplicate fills skipped, legs left out by type)."""
    executions, left_out = executions_from_transactions(fetch_transactions(account_hash, days, get=get))
    trades, skipped = build_trades_from_executions(executions, account_id, conn)
    return trades, skipped, left_out


def status(get=None) -> dict:
    """What the Import page shows: whether the shared login works, and the accounts."""
    out = {"configured": schwab_shared.configured(), "app_name": schwab_shared.app_name(),
           "ok": False, "hours_left": None, "accounts": [], "error": None}
    if not out["configured"]:
        out["error"] = ("Schwab import is not set up. Set SCHWAB_SHARED_TOKEN_FILE and "
                        "SCHWAB_SHARED_TOKEN_KEY in backend/.env (see README).")
        return out
    try:
        out["hours_left"] = round(schwab_shared.hours_left(schwab_shared.read_login()), 1)
        out["accounts"] = accounts(get)
        out["ok"] = True
    except (schwab_shared.AuthExpired, ValueError) as e:
        out["error"] = str(e)
    except Exception as e:      # network down, Schwab timeout
        out["error"] = f"Could not reach Schwab ({type(e).__name__}). Try again in a minute."
    return out
