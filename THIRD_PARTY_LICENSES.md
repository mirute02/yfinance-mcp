# Third-party licences

This project is MIT ([LICENSE](LICENSE)). No third-party source is vendored
here. Licences below were read from the installed distributions' metadata, not
from project READMEs.

| Package | Licence | Role |
|---|---|---|
| [mcp](https://github.com/modelcontextprotocol/python-sdk) | MIT (Anthropic, PBC 2024) | MCP server framework (`FastMCP`) |
| [yfinance](https://github.com/ranaroussi/yfinance) | Apache-2.0 | Yahoo Finance client |
| [pandas](https://github.com/pandas-dev/pandas) | BSD-3-Clause | Data frames |

Transitive dependencies were surveyed for copyleft. The only weak-copyleft
entries are `frozendict` (LGPL-3, via yfinance) and `certifi` (MPL-2.0). Both
are imported rather than vendored, so neither conflicts with distributing this
project under MIT. No GPL or AGPL appears in the tree.

## Note on yfinance

`yfinance` is Apache-2.0 licensed software, but the data it retrieves is not
covered by that licence. Its own README states that it is "not affiliated,
endorsed, or vetted by Yahoo", is "intended for research and educational
purposes", and that "the Yahoo! finance API is intended for personal use only."
The README's data-terms section sets out what follows from that.

## Trademark

Yahoo and Yahoo Finance are trademarks of Yahoo. This project is not affiliated
with, endorsed by, or vetted by Yahoo.
