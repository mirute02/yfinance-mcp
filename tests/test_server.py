"""Offline tests. Nothing here reaches the network."""

import importlib.util
import json
import math
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("srv", ROOT / "server.py")
srv = importlib.util.module_from_spec(spec)
sys.modules["srv"] = srv
spec.loader.exec_module(srv)


# ── JSON safety ─────────────────────────────────────────────

def test_nat_becomes_null_and_stays_serialisable():
    """pd.NaT is a datetime subclass; a naive isinstance check lets it through
    and the MCP serialiser then fails outside any error handling."""
    frame = pd.DataFrame({"d": [pd.NaT, pd.Timestamp("2025-01-01")]})
    records = srv.df_to_clean_dict(frame)
    assert records[0]["d"] is None
    json.dumps(records)


def test_nat_as_a_column_label_is_serialisable():
    frame = pd.DataFrame({pd.NaT: [1], pd.Timestamp("2025-01-01"): [2]}, index=["Revenue"])
    json.dumps(srv.df_to_clean_dict(frame))


@pytest.mark.parametrize("value", [
    pd.Timedelta("1 days"), np.int64(5), np.float32(1.5), np.bool_(True),
    float("nan"), float("inf"), pd.Timestamp("2025-01-01", tz="Asia/Tokyo"),
])
def test_awkward_scalars_survive_json(value):
    json.dumps(srv.df_to_clean_dict(pd.DataFrame({"v": [value]})))


def test_series_does_not_raise():
    assert len(srv.df_to_clean_dict(pd.Series([1, 2, 3]))) == 3


@pytest.mark.parametrize("empty", [None, pd.DataFrame()])
def test_empty_inputs_give_empty_list(empty):
    assert srv.df_to_clean_dict(empty) == []


def test_index_column_is_renamed():
    frame = pd.DataFrame({"v": [1]}, index=pd.Index([pd.Timestamp("2025-01-01")], name="GradeDate"))
    assert "date" in srv.df_to_clean_dict(frame, "date")[0]


def test_meaningless_range_index_is_dropped():
    assert "index" not in srv.df_to_clean_dict(pd.DataFrame({"v": [1]}), None)[0]


# ── Cache ───────────────────────────────────────────────────

def test_errors_are_not_cached():
    srv._cache.clear()
    calls = []

    @srv.cached(ttl=60)
    def failing():
        calls.append(1)
        return {"error": "transient"}

    failing(); failing()
    assert len(calls) == 2


def test_empty_payloads_are_not_cached():
    """yfinance swallows upstream failures and returns an empty frame, so a
    'no data' answer must not pin an outage in place for the whole TTL."""
    srv._cache.clear()
    calls = []

    @srv.cached(ttl=60)
    def empty():
        calls.append(1)
        return {"symbol": "X", "data": []}

    empty(); empty()
    assert len(calls) == 2


def test_real_payloads_are_cached():
    srv._cache.clear()
    calls = []

    @srv.cached(ttl=60)
    def full():
        calls.append(1)
        return {"symbol": "X", "data": [{"a": 1}]}

    full(); full()
    assert len(calls) == 1


def test_cache_is_bounded():
    srv._cache.clear()

    @srv.cached(ttl=60)
    def many(i):
        return {"i": i}

    for i in range(srv.CACHE_MAX_ENTRIES + 64):
        many(i)
    assert len(srv._cache) <= srv.CACHE_MAX_ENTRIES


def test_cache_survives_concurrent_use():
    srv._cache.clear()

    @srv.cached(ttl=60)
    def many(i):
        return {"i": i}

    errors = []

    def hammer():
        try:
            for i in range(200):
                many(i % 300)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not errors


# ── Input validation ────────────────────────────────────────

@pytest.mark.parametrize("symbol", ["", "   ", None, 123])
def test_bad_symbols_are_refused(symbol):
    with pytest.raises(ValueError):
        srv.validate_ticker(symbol)


@pytest.mark.parametrize("period", ["bogus", "", "1decade"])
def test_bad_period_is_refused_before_any_request(period):
    assert "error" in srv.get_historical_prices("AAPL", period=period)


@pytest.mark.parametrize("limit", [0, 101, "20", True, 3.5, -1])
def test_bad_limit_is_refused(limit):
    assert "error" in srv.get_rating_changes("AAPL", limit=limit)


def test_unexpected_exceptions_become_error_dicts():
    @srv.tool_errors
    def boom():
        raise RuntimeError("upstream exploded")

    result = boom()
    assert "error" in result and "RuntimeError" in result["error"]


# ── Usage budget ────────────────────────────────────────────

def test_budget_refuses_past_the_ceiling(monkeypatch):
    monkeypatch.setattr(srv, "DAILY_LIMIT", 3)
    monkeypatch.setattr(srv, "MIN_INTERVAL", 0)
    srv._budget_day, srv._budget_used = srv._today(), 0
    for _ in range(3):
        srv.rate_limit()
    with pytest.raises(ValueError, match="budget"):
        srv.rate_limit()


def test_budget_resets_on_a_new_day(monkeypatch):
    monkeypatch.setattr(srv, "DAILY_LIMIT", 3)
    srv._budget_day, srv._budget_used = "2000-01-01", 3
    assert srv.budget_state() == (0, 3)


def test_budget_holds_under_concurrency(monkeypatch):
    monkeypatch.setattr(srv, "DAILY_LIMIT", 50)
    monkeypatch.setattr(srv, "MIN_INTERVAL", 0)
    srv._budget_day, srv._budget_used = srv._today(), 0
    granted = []

    def worker():
        for _ in range(20):
            try:
                srv.rate_limit()
                granted.append(1)
            except ValueError:
                pass

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(granted) == 50


def test_usage_budget_tool_reports_state(monkeypatch):
    monkeypatch.setattr(srv, "DAILY_LIMIT", 10)
    srv._budget_day, srv._budget_used = srv._today(), 4
    report = srv.get_usage_budget()
    assert report["requests_used_today"] == 4
    assert report["remaining"] == 6


# ── Rate limiting ───────────────────────────────────────────

def test_calls_are_paced(monkeypatch):
    monkeypatch.setattr(srv, "DAILY_LIMIT", 1000)
    srv._budget_day, srv._budget_used = srv._today(), 0
    srv._last_request = 0.0
    start = time.time()
    srv.rate_limit()
    srv.rate_limit()
    assert time.time() - start >= srv.MIN_INTERVAL * 0.9


# ── Documentation matches implementation ────────────────────

def test_every_tool_appears_in_both_readmes():
    source = (ROOT / "server.py").read_text()
    import re
    tools = re.findall(
        r"@mcp\.tool\(\)\s*(?:@[\w.]+(?:\([^)]*\))?\s*)*def (\w+)", source
    )
    assert len(tools) >= 8
    for name in ("README.md", "README.en.md"):
        text = (ROOT / name).read_text()
        missing = [t for t in tools if f"`{t}(" not in text]
        assert not missing, f"{name} omits {missing}"
