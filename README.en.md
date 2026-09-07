# yfinance-mcp

[日本語](README.md) | **English**

An [MCP](https://modelcontextprotocol.io) server that exposes Yahoo Finance fundamentals for **Japanese and US equities**, so an assistant can pull statements and ratios directly instead of being pasted numbers.

Japanese tickers use the Yahoo suffix form (`7203.T`); US tickers are plain (`AAPL`).

## Tools

| Tool | Returns |
|---|---|
| `get_stock_quote(symbol)` | Price, day range, 52-week range, market cap, PER, sector |
| `get_income_statement(symbol, quarterly=False)` | Income statement, annual or quarterly |
| `get_balance_sheet(symbol, quarterly=False)` | Balance sheet, annual or quarterly |
| `get_cash_flow(symbol, quarterly=False)` | Cash flow statement, annual or quarterly |
| `get_key_ratios(symbol)` | PER, PBR, ROE, ROA, margins, growth, leverage, dividend |
| `get_historical_prices(symbol, period="1y", interval="1d")` | OHLCV bars, most recent 400 |
| `get_analyst_recommendations(symbol)` | Consensus rating counts, four monthly snapshots |
| `get_rating_changes(symbol, limit=20)` | Dated upgrades and downgrades, newest first |
| `get_usage_budget()` | Upstream requests used today against the daily ceiling |

## Install

```bash
git clone https://github.com/mirute02/yfinance-mcp.git
cd yfinance-mcp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Python 3.10+.

## Register with an MCP client

Add to `.mcp.json` (Claude Code) or your client's config, substituting the absolute path:

```json
{
  "mcpServers": {
    "yfinance": {
      "command": "/path/to/yfinance-mcp/.venv/bin/python",
      "args": ["/path/to/yfinance-mcp/server.py"]
    }
  }
}
```

The server speaks stdio. Verify it standalone with:

```bash
.venv/bin/python server.py   # waits on stdin; Ctrl-C to exit
```

## Behaviour worth knowing

**Ratios Yahoo omits are derived.** Yahoo leaves `returnOnEquity` and `operatingMargins` empty for a number of listings. Where the statements allow it, those are computed and returned suffixed `_calculated`, so you can tell a derived figure from a reported one.

**Two different views of analyst opinion.** `get_analyst_recommendations` is a standing-rating tally — four rows, one per month, counting how many analysts say strong buy through strong sell. `get_rating_changes` is the event log: dated upgrades, downgrades and reiterations with firm and price target. Coverage of the latter is thin outside US listings.

**Responses are JSON-safe.** `NaN`, `NaT` and `Inf` become `null`, timestamps become ISO strings and numpy scalars are unboxed. `pd.NaT` in particular is a `datetime` subclass, so a naive type check lets it through and the MCP layer's serialiser then fails outside the reach of any error handling.

**Results are cached and requests are paced.** A 256-entry LRU holds responses for 5 minutes (10 for price history), and upstream calls are serialised with a 0.5 s minimum gap. Neither errors nor empty payloads are cached — yfinance swallows upstream failures and returns an empty frame instead of raising, so caching a "no data" answer would pin an outage in place for the whole TTL.

**Failures come back as data.** Tools return `{"error": "..."}` rather than raising, including for an unknown ticker, so the assistant can react instead of seeing a transport error.

**History is capped at 400 bars.** The MCP layer pretty-prints responses, so 2000 daily bars serialise to roughly half a megabyte — more than an assistant can use. When a request exceeds the cap, `truncated` is true and `available_points` reports how many bars existed; widen the interval rather than the period.

**It is deliberately not a harvester.** Upstream requests are paced at one per 0.5 s and capped at 1000 per UTC day; past that every tool returns an error until the budget resets. `get_usage_budget` reports where you stand. The ceiling is adjustable with `YFINANCE_MCP_DAILY_LIMIT`, but it is there because the scale of use is the whole question under Yahoo's terms — this server answers questions about a handful of companies, and the code says so rather than leaving it to a README promise.

## Data terms

This project is **not affiliated with, endorsed by, or vetted by Yahoo**. It builds on [yfinance](https://github.com/ranaroussi/yfinance), whose own README states that it is "intended for research and educational purposes" and that "the Yahoo! finance API is intended for personal use only."

Be clear about what that means. Yahoo's [Terms of Service](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html) §2.4(i) prohibit accessing or collecting data from their services "using any automated means" — scrapers, data mining tools and the like — "without our express, prior permission", and §2.5 prohibits use "for any commercial purpose". So it is the **method of access itself** that Yahoo restricts, not merely what you do with the data afterwards.

This server does nothing to circumvent that: it fetches one ticker per call, adds a 0.5 s minimum gap between upstream requests, caches responses and caps result sizes. But it is still automated access. **Treat it as personal, research and educational use, at your own risk.** If you need market data you can rely on commercially, license it from a vendor.

## Licence

MIT — see [LICENSE](LICENSE). `yfinance` is Apache-2.0; `mcp` is MIT.
