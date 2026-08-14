# TradingView account requirements for webhooks

Researched 2026-08-11. Verify against TradingView's current pricing page before acting, since plans/limits can change.

## Minimum plan

**Essential** (paid, ~$12.95/mo billed annually as of April 2026). The free **Basic** plan does not support webhooks at all — only popup and email notifications.

## Prerequisite

Two-factor authentication (2FA) must be enabled on the TradingView account to use the webhook URL field in an alert, regardless of plan tier.

## Essential plan alert limits

- **20 active alerts** at a time (free Basic plan allows only 1).
- **Alerts expire after ~2 months** and must be recreated/renewed — they don't run indefinitely. (Premium and higher tiers have non-expiring alerts.)
- If 20 alerts or 2-month expiration becomes limiting, the next tier up is **Plus** (~$24.95/mo annual), which raises the alert cap and is aimed at users automating with webhooks more heavily.

## Alert types supported (Essential and up)

- Price alerts (crosses a level)
- Indicator alerts (e.g. RSI/MACD/MA crosses a threshold)
- Drawing alerts (price crosses a trendline/Fib/horizontal line)
- Pine Script `alertcondition()` custom alerts
- Cross-instrument alerts (alert based on a different symbol's behavior)

## Delivery methods (Essential and up)

- Popup, sound, email
- **Webhook** (POST to a URL) — what this project needs
- Mobile push
- SMS (extra cost, US only)

## Implication for this project

Essential is sufficient to start: it unlocks webhooks and 20 alerts is plenty for a personal watchlist. Just remember alerts need renewing roughly every 2 months, or a stale alert could silently stop firing.
