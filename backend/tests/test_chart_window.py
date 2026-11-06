"""Intraday chart window in New York time (GET /api/chart).

    cd backend && python -m pytest tests -q

The window used to be built with a fixed -04:00 offset (daylight time). From the first
Sunday of November New York is UTC-5, so the chart asked Alpaca for 08:30-15:00 ET and
missed the last trading hour.
"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


@pytest.fixture
def chart(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "journal.db"))
    for name in ("database", "main"):
        sys.modules.pop(name, None)
    import database
    database.DB_PATH = str(tmp_path / "journal.db")
    import main
    monkeypatch.setattr(main, "ALPACA_KEY", "test-key")
    monkeypatch.setattr(main, "ALPACA_SECRET", "test-secret")
    asked = []

    async def fake_fetch(client, url, params, headers, max_bars=5000):
        asked.append(params)
        return []
    monkeypatch.setattr(main, "_fetch_alpaca_bars", fake_fetch)
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        def get(path):
            asked.clear()
            assert c.get(path).status_code == 200
            return asked[0]["start"], asked[0]["end"]
        yield get


def test_winter_day_uses_eastern_standard_time(chart):
    # 2026-11-05 is EST (UTC-5): 09:30-16:00 ET is 14:30-21:00 UTC.
    assert chart("/api/chart/SPY/2026-11-05") == ("2026-11-05T09:30:00-05:00", "2026-11-05T16:00:00-05:00")


def test_summer_day_uses_eastern_daylight_time(chart):
    assert chart("/api/chart/SPY/2026-09-15") == ("2026-09-15T09:30:00-04:00", "2026-09-15T16:00:00-04:00")


def test_zoomed_out_window_across_the_clock_change_uses_each_days_offset(chart):
    # 5 days back from Tuesday 2026-11-03 starts on Friday 2026-10-30, still daylight time.
    assert chart("/api/chart/SPY/2026-11-03?days_back=5") == ("2026-10-30T09:30:00-04:00", "2026-11-03T16:00:00-05:00")
