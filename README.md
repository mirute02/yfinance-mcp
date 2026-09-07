# yfinance-mcp

**日本語** | [English](README.en.md)

**日本株・米国株**の財務データを Yahoo Finance から取得する [MCP](https://modelcontextprotocol.io) サーバー。数値を貼り付けて渡す代わりに、アシスタントが自分で財務諸表や指標を引けるようになる。

日本株は Yahoo のサフィックス形式（`7203.T`）、米国株はそのまま（`AAPL`）。

## ツール

| ツール | 返すもの |
|---|---|
| `get_stock_quote(symbol)` | 株価、日中値幅、52週レンジ、時価総額、PER、セクター |
| `get_income_statement(symbol, quarterly=False)` | 損益計算書（通期／四半期） |
| `get_balance_sheet(symbol, quarterly=False)` | 貸借対照表（通期／四半期） |
| `get_cash_flow(symbol, quarterly=False)` | キャッシュフロー計算書（通期／四半期） |
| `get_key_ratios(symbol)` | PER、PBR、ROE、ROA、利益率、成長率、負債比率、配当 |
| `get_historical_prices(symbol, period="1y", interval="1d")` | 過去の OHLCV（直近400本） |
| `get_analyst_recommendations(symbol)` | 格付けの集計（直近4か月分のスナップショット） |
| `get_rating_changes(symbol, limit=20)` | 日付付きの格上げ・格下げ履歴（新しい順） |
| `get_usage_budget()` | 本日の上流リクエスト消費数と日次上限 |

## インストール

```bash
git clone https://github.com/mirute02/yfinance-mcp.git
cd yfinance-mcp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Python 3.10 以上。

## MCP クライアントへの登録

`.mcp.json`（Claude Code）などに追加する。パスは絶対パスに置き換えること。

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

stdio で通信する。単体での起動確認は次のとおり。

```bash
.venv/bin/python server.py   # 標準入力待ちになる。Ctrl-C で終了
```

## 知っておくとよい挙動

**Yahoo が返さない指標は自前で計算する。** `returnOnEquity` や `operatingMargins` は空になる銘柄が少なくない。財務諸表から算出できる場合は計算し、`_calculated` を付けて返すので、実報告値と区別できる。

**アナリスト情報は2種類ある。** `get_analyst_recommendations` は現時点の格付け集計で、直近4か月分の「強気買い〜強気売り」の人数。`get_rating_changes` はイベント履歴で、日付・証券会社名・格付けの変更前後・目標株価が付く。後者は米国株以外では収録が薄い。

**レスポンスは JSON セーフ。** `NaN`・`NaT`・`Inf` は `null` に、Timestamp は ISO 文字列に、numpy のスカラは Python の型に変換する。特に `pd.NaT` は `datetime` のサブクラスなので単純な型判定をすり抜け、MCP層のシリアライザで落ちる。そこはエラーハンドリングの外側になる。

**キャッシュとレート制限がある。** 256件上限の LRU で5分間（価格履歴は10分）保持し、上流への呼び出しは 0.5 秒間隔に直列化する。エラーだけでなく**空の結果もキャッシュしない**。yfinance は上流の失敗を例外にせず空の DataFrame で返すため、「データなし」をキャッシュすると障害を TTL のあいだ固定してしまう。

**失敗はデータとして返る。** 存在しないティッカーを含め、例外を投げずに `{"error": "..."}` を返す。アシスタントが状況を理解して対処できる。

**履歴は400本が上限。** MCP層はレスポンスを整形して送るため、日足2000本では約0.5MBになりアシスタントが読み切れない。上限を超えた場合は `truncated` が真になり、`available_points` に本来の本数が入る。期間ではなく足種を広げて調整すること。

**意図的に大量収集はできないようにしてある。** 上流への要求は0.5秒に1回へ抑え、UTC日付で1日1000件を上限としている。超えると、リセットまで全ツールがエラーを返す。残量は `get_usage_budget` で確認できる。上限は `YFINANCE_MCP_DAILY_LIMIT` で変更できるが、そもそもこの制限を置いているのは、Yahoo の規約において**利用の規模こそが論点**だからである。このサーバーは数社について調べるためのものであり、それを README の約束ではなくコードで示している。

## データの利用条件

本プロジェクトは **Yahoo とは無関係で、承認も検証も受けていない**。[yfinance](https://github.com/ranaroussi/yfinance) を利用しており、yfinance 自身の README も「研究・教育目的を想定」「Yahoo! finance API は個人利用のみを想定している」と明記している。

その意味をはっきりさせておく。Yahoo の[利用規約](https://legal.yahoo.com/us/en/yahoo/terms/otos/index.html) §2.4(i) は、スクレイパーやデータマイニングツールを含む「自動的な手段」でのデータ取得を「明示的な事前許可なく」行うことを禁じており、§2.5 は「商用目的での利用」を禁じている。つまり Yahoo が制限しているのは、取得後の使い道だけではなく**アクセスの手段そのもの**である。

本サーバーはそれを回避する仕組みを持たない。1回の呼び出しで1銘柄を取得し、上流への要求は0.5秒以上あけ、結果はキャッシュし、サイズに上限を設けている。それでも自動アクセスであることに変わりはない。**個人の研究・教育目的として、自己責任で利用すること。** 業務で信頼できる市場データが必要なら、ベンダーからライセンスを購入すること。

## ライセンス

MIT — [LICENSE](LICENSE) を参照。`yfinance` は Apache-2.0、`mcp` は MIT。
