# NEXUS read-only bridge

The optional bridge lets NEXUS read EliteTrade connection status and descriptive
historical market summaries. It has no order, account-control, token, or settings
mutation endpoint. Trading remains disabled in this demo market-data runtime.

## Enable locally

Set these values in the protected local environment (never commit the token):

```sh
ELITETRADE_NEXUS_BRIDGE_ENABLED=1
ELITETRADE_NEXUS_BRIDGE_TOKEN=<random secret of at least 40 characters>
ELITETRADE_NEXUS_BRIDGE_HOST=127.0.0.1
ELITETRADE_NEXUS_BRIDGE_PORT=8765
```

The listener refuses non-loopback bind addresses. For a remote NEXUS node, put
an authenticated TLS reverse proxy or SSH tunnel in front of this loopback
listener. Do not expose port 8765 directly to the internet.

Endpoints:

- `GET /health` — liveness only.
- `GET /v1/status` — bearer-authenticated connection and execution-lock state.
- `GET /v1/market-summary?symbol=XAUUSD&timeframe=M15` — bearer-authenticated,
  descriptive regime and historical signal outcomes for configured symbols.

The symbol allowlist is derived from `CTRADER_MARKET_SYMBOLS`; the timeframe is
derived from `CTRADER_TIMEFRAME`. The bridge is off unless explicitly enabled.
