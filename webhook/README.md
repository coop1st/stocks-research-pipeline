# Live webhook (deferred)

TradingView alerts can POST a JSON payload to a webhook URL when a condition fires, but that URL must be public HTTPS — a plain localhost server isn't reachable from TradingView's servers. We're deferring this until the rest of the project (watchlist + Trading Advisor workflow) is in use. Decision to make when we pick this up:

- **Local server + tunnel** — run a small receiver on this PC, expose it on demand with a tool like Cloudflare Tunnel or ngrok. Free, no account needed for a basic tunnel, but the URL only works while the PC and tunnel are both running, and free tunnel URLs typically change each time you restart them (TradingView alert config would need updating to match).
- **Free cloud host** — deploy the receiver to something like Render, Fly.io, or a Cloudflare Worker for a stable always-on URL. More setup, needs an account with the host.

Either way, the receiver's job is simple: accept a POST, validate/parse the JSON body, and write it to `../data/alerts/` as a timestamped file for the Trading Advisor agent to read.

## Expected alert payload

TradingView alert messages are whatever text/JSON you configure in the alert itself. A reasonable convention for this project:

```json
{
  "symbol": "NASDAQ:AAPL",
  "condition": "crossing above 200 SMA",
  "price": 227.50,
  "time": "2026-08-11T14:30:00Z"
}
```

Pine Script alert message fields like `{{ticker}}`, `{{close}}`, `{{time}}` can populate this automatically — worth revisiting the exact template once we're ready to wire this up.
