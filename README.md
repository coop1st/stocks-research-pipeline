# Stocks

Personal project for tracking a watchlist, capturing TradingView alerts, and getting research/analysis via the **Trading Advisor** agent.

## Layout

```
Stocks/
├── .claude/agents/trading-advisor.md   Trading Advisor subagent definition
├── data/
│   ├── watchlists/    Tracked tickers (main.csv: symbol, notes, thesis)
│   ├── alerts/        TradingView alert payloads (JSON), manual for now
│   ├── research/      Saved write-ups from research sessions
│   └── db/stocks.db   SQLite DB of price history + fundamentals (see pipeline/)
├── pipeline/           Free-data pipeline: universe, daily OHLCV, SEC fundamentals
└── webhook/           Plan for wiring up live TradingView alerts (not built yet)
```

## Using the Trading Advisor

Open Claude Code in this folder (`Stocks/`) and just ask — the `trading-advisor` subagent is scoped to this project's data and will pick up watchlist reviews, alert interpretation, and market research automatically. It reads/writes under `data/`.

## Status

- [x] Folder structure
- [x] Trading Advisor agent
- [x] Data pipeline: free universe + daily OHLCV + SEC fundamentals into SQLite (see `pipeline/README.md`)
- [ ] Populate `data/watchlists/main.csv` with real tickers
- [ ] Live TradingView webhook (see `webhook/README.md` for the plan — deferred for now)
- [ ] Screener/valuation layer on top of `data/db/stocks.db` (pick stocks, judge "cheap enough")
