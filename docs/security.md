# Security — what this server can and cannot do

An MCP server runs as a subprocess of your assistant client, speaks over stdio,
and is trusted by whatever is on the other end. That shapes what matters here.

## 1. It holds no credentials

There is no API key, no account, no token, no config file. Yahoo's endpoints
used by `yfinance` are unauthenticated. Nothing in this repository or its
runtime state is worth stealing.

## 2. It is read-only, outbound only

Every tool performs an HTTP GET through `yfinance` and returns the result.
Nothing writes to disk, nothing executes a subprocess, nothing accepts an
inbound connection. The only state is an in-memory cache that dies with the
process.

## 3. Untrusted input reaches the network

`symbol` comes from the assistant, which may be relaying text from a document,
a web page or a user. It is passed to `yfinance`, which puts it in a URL path.

- The value is required to be a non-empty string and is stripped.
- `period` and `interval` are `Literal` types, so the MCP layer rejects
  anything outside the accepted sets before the tool runs.
- `limit` is range-checked.

`symbol` itself is **not** pattern-restricted, because valid tickers are a
moving target (`7203.T`, `BRK-B`, `^GSPC`, `BTC-USD`). The exposure is that a
crafted string becomes part of a URL to Yahoo. `yfinance` encodes it; this
server does not construct URLs itself. An attacker who controls `symbol`
can cause a request to a Yahoo path of limited shape, and read the answer —
which is what the tool does anyway.

## 4. Responses are made safe before they leave

Raw pandas values break clients. `NaN`, `NaT` and `Inf` become `null`,
timestamps become ISO strings, numpy scalars are unboxed, and anything else is
stringified. `pd.NaT` in particular is a `datetime` subclass, so a naive
`isinstance` check lets it through and the serialiser then fails *outside* the
reach of the tool's own error handling — a real crash path, now closed.

Responses are also capped: 400 price bars, because the MCP layer pretty-prints
and 2000 daily bars serialise to roughly half a megabyte.

## 5. Rate and volume limits

Upstream calls are paced at one per 0.5 s and capped at 1000 per UTC day.
This is not a security control against an attacker — anyone who can call the
tools can also just run `yfinance` themselves. It exists so the server cannot
*accidentally* become a bulk collector, which is the thing Yahoo's terms
actually restrict. See the data-terms section of the README.

## 6. What is not protected

- **Anything the assistant does with the data.** This server has no view of
  that and no way to constrain it.
- **The accuracy of the data.** It is Yahoo's, relayed. Do not make financial
  decisions on the strength of a `null` that might be a reporting gap rather
  than a zero.
- **Availability.** `yfinance` depends on undocumented endpoints; they change.
  When they do, tools return errors rather than wrong answers, which is the
  best that can be offered.
- **The dependency chain.** `yfinance` reaches Yahoo through `curl_cffi`, which
  impersonates a browser TLS fingerprint. That behaviour lives in the
  dependency, not here, but it is part of what you install.

## 7. Deliberately not done

- **No symbol allowlist.** It would have to be either wrong or enormous.
- **No persistence.** Nothing is written to disk, so there is no cache file to
  poison and no stale data surviving a restart.
- **No credential support**, even though authenticated Yahoo endpoints exist.
  Adding an account would give this server something to leak.
- **No automatic retry.** A failed upstream call returns an error. Retrying
  inside a tool multiplies load against terms that restrict exactly that.

## Reporting

Open an issue. This is a personal project with no security process behind it;
treat the disclosure accordingly.
