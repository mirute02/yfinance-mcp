"""An MCP server exposing Yahoo Finance fundamentals for Japanese and US equities.

Wraps yfinance behind Model Context Protocol tools so an assistant can pull
quotes, the three financial statements, key ratios, price history and analyst
recommendations. Japanese tickers use the Yahoo suffix form (7203.T); US
tickers are plain (AAPL).

Research and educational use only — see README for the terms that apply to the
underlying data.
"""

import hashlib
import json
import math
import os
import threading
import time
from collections import OrderedDict
from functools import wraps
from typing import Literal

import pandas as pd
import yfinance as yf
# mcp 2.x renamed FastMCP to MCPServer and moved it. The decorator and run()
# signatures this server relies on are unchanged, so support both rather than
# pinning users to whichever major happens to be current.
try:
    from mcp.server.fastmcp import FastMCP as MCPServer   # mcp 1.x
except ModuleNotFoundError:                                # pragma: no cover
    from mcp.server.mcpserver import MCPServer            # mcp 2.x

mcp = MCPServer("yfinance")

# ── Response cache ──────────────────────────────────────────
# Bounded and lock-protected: an MCP server is long-lived and FastMCP may call
# tools concurrently, so an unbounded plain dict would leak and race.

CACHE_TTL = 300           # seconds
HISTORY_CACHE_TTL = 600
CACHE_MAX_ENTRIES = 256

_cache: "OrderedDict[str, tuple[object, float]]" = OrderedDict()
_cache_lock = threading.Lock()


def _cache_key(func_name: str, args, kwargs) -> str:
    payload = json.dumps([args, kwargs], default=str, sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:32]
    return f"{func_name}:{digest}"


def _is_cacheable(result) -> bool:
    """Whether a tool result is worth remembering.

    Errors are obviously not. Neither are empty payloads: yfinance swallows
    upstream failures and hands back an empty frame instead of raising, so a
    Yahoo hiccup looks like a successful "no data" answer. Caching that would
    pin the outage in place for the whole TTL.
    """
    if not isinstance(result, dict) or "error" in result:
        return False
    for key in ("data", "recommendations", "rating_changes"):
        if key in result and not result[key]:
            return False
    return True


def cached(ttl: int = CACHE_TTL):
    """Memoise a tool's return value for `ttl` seconds.

    Errors and empty payloads are never cached — see _is_cacheable.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = _cache_key(func.__name__, args, kwargs)
            now = time.time()
            with _cache_lock:
                hit = _cache.get(key)
                if hit is not None:
                    data, stamp = hit
                    if now - stamp < ttl:
                        _cache.move_to_end(key)
                        return data
                    del _cache[key]

            result = func(*args, **kwargs)

            if _is_cacheable(result):
                with _cache_lock:
                    _cache[key] = (result, now)
                    _cache.move_to_end(key)
                    while len(_cache) > CACHE_MAX_ENTRIES:
                        _cache.popitem(last=False)
            return result

        return wrapper

    return decorator


# ── Rate limiting ───────────────────────────────────────────
# Yahoo throttles aggressive clients. Serialise upstream calls with a minimum
# gap so a burst of tool calls does not trip it.

MIN_INTERVAL = 0.5  # seconds between upstream requests

# A daily ceiling on upstream requests. This server is for answering questions
# about a company or two at a time; it is not a harvester. Yahoo's terms
# prohibit automated collection without permission and prohibit building a
# competing dataset, so the limit exists to make that intent explicit in the
# code rather than only in the README. Raise it via YFINANCE_MCP_DAILY_LIMIT if
# you genuinely need to and have satisfied yourself that you may.

DEFAULT_DAILY_LIMIT = 1000

try:
    DAILY_LIMIT = int(os.environ.get("YFINANCE_MCP_DAILY_LIMIT", DEFAULT_DAILY_LIMIT))
except ValueError:
    DAILY_LIMIT = DEFAULT_DAILY_LIMIT
DAILY_LIMIT = max(1, DAILY_LIMIT)

_rate_lock = threading.Lock()
_last_request = 0.0
_budget_lock = threading.Lock()
_budget_day = ""
_budget_used = 0


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def budget_state() -> tuple[int, int]:
    """(used, limit) for the current UTC day."""
    with _budget_lock:
        if _budget_day != _today():
            return 0, DAILY_LIMIT
        return _budget_used, DAILY_LIMIT


def spend_budget() -> None:
    """Account for one upstream request, or refuse once the day's budget is gone."""
    global _budget_day, _budget_used
    with _budget_lock:
        today = _today()
        if _budget_day != today:
            _budget_day, _budget_used = today, 0
        if _budget_used >= DAILY_LIMIT:
            raise ValueError(
                f"Daily upstream request budget exhausted ({DAILY_LIMIT} requests since "
                f"00:00 UTC). This server is meant for looking up a handful of companies, "
                f"not for bulk collection, which Yahoo's terms prohibit. Set "
                f"YFINANCE_MCP_DAILY_LIMIT if you need a different ceiling."
            )
        _budget_used += 1


def rate_limit():
    """Pace upstream calls and charge one request against the daily budget."""
    global _last_request
    spend_budget()
    with _rate_lock:
        gap = time.time() - _last_request
        if gap < MIN_INTERVAL:
            time.sleep(MIN_INTERVAL - gap)
        _last_request = time.time()


# ── Helpers ─────────────────────────────────────────────────


def tool_errors(func):
    """Turn any unexpected exception into an {"error": ...} payload.

    Without this an upstream failure propagates as a raw traceback through the
    MCP transport, which the client cannot act on.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Upstream request failed ({type(e).__name__}): {e}"}

    return wrapper


def _json_safe(value):
    """Coerce one pandas/numpy value into something json.dumps can handle.

    pd.NaT is a datetime subclass, so an isinstance(..., pd.Timestamp) test lets
    it through and it then blows up inside the MCP layer's serialiser — after
    tool_errors has already returned. pd.isna() catches NaN, NaT and None alike.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass  # arrays and the like are not scalar-NA testable
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, pd.Timedelta):
        return str(value)
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if hasattr(value, "item"):  # numpy scalar
        try:
            return value.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def df_to_clean_dict(df, index_name: str | None = "index") -> list[dict]:
    """JSON-safe records from a DataFrame (or Series).

    NaN/NaT/Inf become None, Timestamps become ISO strings, numpy scalars are
    unboxed. Anything else is stringified rather than risking a serialiser
    failure downstream.

    index_name renames the column produced from the index; pass None to drop it
    when the index carries no meaning (a bare RangeIndex).
    """
    if df is None:
        return []
    if isinstance(df, pd.Series):
        df = df.to_frame()
    if not hasattr(df, "columns") or df.empty:
        return []
    # Statement frames come back with periods as columns; transpose so each
    # record is one period.
    if len(df.columns) > 0 and isinstance(df.columns[0], pd.Timestamp):
        df = df.T
    original_index = df.index.name
    frame = df.reset_index()
    if index_name is None:
        # A meaningless RangeIndex adds a column of row numbers; drop it.
        if original_index is None and "index" in frame.columns:
            frame = frame.drop(columns=["index"])
    else:
        # reset_index names the new column after the index, or "index" when the
        # index was unnamed. Rename whichever appeared.
        source = original_index if original_index in frame.columns else "index"
        if source in frame.columns:
            frame = frame.rename(columns={source: index_name})
    return [
        {str(k): _json_safe(v) for k, v in record.items()}
        for record in frame.to_dict(orient="records")
    ]


def validate_ticker(symbol: str) -> tuple[yf.Ticker, dict]:
    """Resolve a ticker, raising ValueError with guidance if it does not exist."""
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string, e.g. 'AAPL' or '7203.T'.")

    symbol = symbol.strip()
    rate_limit()
    ticker = yf.Ticker(symbol)
    try:
        info = ticker.info or {}
    except Exception:
        info = {}

    if info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
        price = None
        try:
            fast = ticker.fast_info
            price = fast.get("lastPrice") if hasattr(fast, "get") else getattr(fast, "last_price", None)
        except Exception:
            price = None
        if price is None:
            raise ValueError(
                f"Ticker {symbol!r} not found. Use the Yahoo suffix form for "
                "non-US listings (Japan: 7203.T, London: BP.L); US tickers are plain (AAPL)."
            )
    return ticker, info


def compute_ratios_from_financials(ticker: yf.Ticker, info: dict) -> dict:
    """Derive ratios Yahoo omits for some listings, notably Japanese ones."""
    ratios: dict = {}
    if info.get("returnOnEquity") is None:
        try:
            rate_limit()
            net_income = ticker.financials.loc["Net Income"].iloc[0]
            rate_limit()
            equity = ticker.balance_sheet.loc["Stockholders Equity"].iloc[0]
            if equity:
                ratios["roe_calculated"] = round(float(net_income / equity), 4)
        except (KeyError, IndexError, TypeError, ZeroDivisionError, ValueError):
            pass
    if info.get("operatingMargins") is None:
        try:
            rate_limit()
            financials = ticker.financials
            operating_income = financials.loc["Operating Income"].iloc[0]
            revenue = financials.loc["Total Revenue"].iloc[0]
            if revenue:
                ratios["operating_margin_calculated"] = round(
                    float(operating_income / revenue), 4
                )
        except (KeyError, IndexError, TypeError, ZeroDivisionError, ValueError):
            pass
    return ratios


def _statement(symbol: str, quarterly: bool, annual_attr: str, quarterly_attr: str) -> dict:
    ticker, _ = validate_ticker(symbol)
    rate_limit()
    frame = getattr(ticker, quarterly_attr if quarterly else annual_attr)
    return {
        "symbol": symbol,
        "period": "quarterly" if quarterly else "annual",
        "data": df_to_clean_dict(frame, "period_end"),
    }


QUOTE_FIELDS = (
    "shortName", "longName", "symbol", "currency", "exchange",
    "regularMarketPrice", "currentPrice", "regularMarketOpen",
    "regularMarketDayHigh", "regularMarketDayLow", "regularMarketVolume",
    "previousClose", "fiftyTwoWeekHigh", "fiftyTwoWeekLow", "marketCap",
    "trailingPE", "forwardPE", "dividendYield", "sector", "industry",
)

RATIO_FIELDS = (
    "trailingPE", "forwardPE", "priceToBook", "returnOnEquity", "returnOnAssets",
    "operatingMargins", "profitMargins", "grossMargins", "revenueGrowth",
    "earningsGrowth", "debtToEquity", "currentRatio", "quickRatio",
    "dividendYield", "payoutRatio", "beta", "trailingEps", "forwardEps",
    "bookValue", "enterpriseToRevenue", "enterpriseToEbitda",
)

# Declared as Literal so the MCP client gets an enum in the tool schema and can
# reject a bad value before a call is even made.
Period = Literal["1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max"]
Interval = Literal["1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"]

VALID_PERIODS = ("1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y", "10y", "ytd", "max")
VALID_INTERVALS = ("1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo")
MAX_HISTORY_POINTS = 400


# ── MCP tools ───────────────────────────────────────────────


@mcp.tool()
@tool_errors
@cached()
def get_stock_quote(symbol: str) -> dict:
    """Current price and headline figures for a listed company.

    symbol: Yahoo ticker. Japan uses the .T suffix (7203.T); US is plain (AAPL).
    """
    _, info = validate_ticker(symbol)
    return {k: info[k] for k in QUOTE_FIELDS if info.get(k) is not None}


@mcp.tool()
@tool_errors
@cached()
def get_income_statement(symbol: str, quarterly: bool = False) -> dict:
    """Income statement. Set quarterly=True for quarterly rather than annual periods."""
    return _statement(symbol, quarterly, "financials", "quarterly_financials")


@mcp.tool()
@tool_errors
@cached()
def get_balance_sheet(symbol: str, quarterly: bool = False) -> dict:
    """Balance sheet. Set quarterly=True for quarterly rather than annual periods."""
    return _statement(symbol, quarterly, "balance_sheet", "quarterly_balance_sheet")


@mcp.tool()
@tool_errors
@cached()
def get_cash_flow(symbol: str, quarterly: bool = False) -> dict:
    """Cash flow statement. Set quarterly=True for quarterly rather than annual periods."""
    return _statement(symbol, quarterly, "cashflow", "quarterly_cashflow")


@mcp.tool()
@tool_errors
@cached()
def get_key_ratios(symbol: str) -> dict:
    """Valuation, profitability and leverage ratios.

    Ratios Yahoo does not report for a listing are derived from the statements
    where possible and suffixed with _calculated.
    """
    ticker, info = validate_ticker(symbol)
    result = {k: info[k] for k in RATIO_FIELDS if info.get(k) is not None}
    result.update(compute_ratios_from_financials(ticker, info))
    result["symbol"] = symbol
    result["shortName"] = info.get("shortName") or info.get("longName")
    return result


@mcp.tool()
@tool_errors
@cached(ttl=HISTORY_CACHE_TTL)
def get_historical_prices(symbol: str, period: Period = "1y", interval: Interval = "1d") -> dict:
    """Historical OHLCV bars.

    Intraday intervals are only available for recent periods.

    At most 400 of the most recent bars are returned. That ceiling is about
    what an assistant can actually read: the MCP layer pretty-prints responses,
    so 2000 daily bars serialise to roughly half a megabyte. When the request
    would exceed it, `truncated` is true and `available_points` reports how many
    bars existed — widen the interval rather than the period to see more.
    """
    if period not in VALID_PERIODS:
        raise ValueError(f"period must be one of: {', '.join(VALID_PERIODS)}")
    if interval not in VALID_INTERVALS:
        raise ValueError(f"interval must be one of: {', '.join(VALID_INTERVALS)}")

    ticker, _ = validate_ticker(symbol)
    rate_limit()
    data = df_to_clean_dict(ticker.history(period=period, interval=interval), "date")
    available = len(data)
    truncated = available > MAX_HISTORY_POINTS
    if truncated:
        data = data[-MAX_HISTORY_POINTS:]
    return {
        "symbol": symbol,
        "period": period,
        "interval": interval,
        "data_points": len(data),
        "available_points": available,
        "truncated": truncated,
        "data": data,
    }


@mcp.tool()
@tool_errors
@cached()
def get_analyst_recommendations(symbol: str) -> dict:
    """Analyst consensus: how many analysts rate the stock strong buy / buy /
    hold / sell / strong sell.

    Four rows, one per month: "0m" is the current month, "-1m" the month
    before, and so on. This is a snapshot of standing ratings, not a log of
    rating changes — for that use get_rating_changes.
    """
    ticker, _ = validate_ticker(symbol)
    rate_limit()
    return {
        "symbol": symbol,
        "recommendations": df_to_clean_dict(ticker.recommendations, None),
    }


@mcp.tool()
@tool_errors
@cached()
def get_rating_changes(symbol: str, limit: int = 20) -> dict:
    """Dated analyst rating changes: upgrades, downgrades and reiterations.

    Each entry carries the firm, the grade moved from and to, the action and
    any price target change. Returns the most recent `limit` entries
    (1-100, newest first).
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100.")
    ticker, _ = validate_ticker(symbol)
    rate_limit()
    data = df_to_clean_dict(ticker.upgrades_downgrades, "date")
    total = len(data)
    # yfinance does not guarantee the ordering of this frame (it currently
    # arrives newest-first), so sort explicitly rather than slicing an end.
    data.sort(key=lambda row: row.get("date") or "", reverse=True)
    return {
        "symbol": symbol,
        "returned": min(total, limit),
        "available": total,
        "rating_changes": data[:limit],
    }


@mcp.tool()
@tool_errors
def get_usage_budget() -> dict:
    """How many upstream requests this server has made today, and its ceiling.

    The server refuses further requests once the ceiling is reached. It exists
    to keep usage to the lookup-a-few-companies scale that Yahoo's terms
    contemplate, rather than bulk collection.
    """
    used, limit = budget_state()
    return {
        "requests_used_today": used,
        "daily_limit": limit,
        "remaining": max(0, limit - used),
        "resets_at": "00:00 UTC",
        "note": (
            "This server is for looking up individual companies. Yahoo's terms "
            "prohibit automated collection without permission and prohibit "
            "building a competing dataset."
        ),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
