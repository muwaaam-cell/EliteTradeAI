# EliteTrade AI — Pepperstone cTrader bot

This is the external Python bot codebase on the separate `pepperstone-ctrader-bot` branch. The legacy Android prototype on `main` is unchanged.

## Verified code currently in this branch

- Strategy/risk foundation: EMA/RSI/ATR analysis, risk checks, stale-quote/spread/volume policy and local kill switch.
- cTrader OAuth helper: consent URL construction, cryptographically random state, callback-state comparison, authorization-code exchange and refresh-token exchange.
- Automated unit-test workflow: GitHub Actions installs dependencies and runs the Python unit tests.
- Broker connection and trading: **not connected**. The OAuth helper has only mocked unit tests and has not contacted Spotware.

## Requirements

- Python 3.11+
- A cTrader Open API application approved by Spotware.
- Application client ID and client secret, stored only as environment secrets.
- A Pepperstone cTrader account and user-approved OAuth access.
- A continuously running Python process for unattended operation. A mobile app or short-lived serverless function is not an always-on trading host.

Install:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
PYTHONPATH=. python -m unittest discover -s tests -v
```

On Windows, activate with `.venv\\Scripts\\activate`.

## Safety

The bot starts in analysis-only mode. Never put credentials, OAuth tokens, Telegram tokens or account identifiers in source files or commit them to Git. Use the host's secret/environment-variable manager.

Live trading must remain disabled until OAuth, account selection, symbol metadata, live quotes, order/position reconciliation, broker-confirmed protective stops, daily loss limits, duplicate-order protection, reconnect handling and emergency shutdown are implemented and verified on demo. Live activation also requires your explicit approval.

## OAuth callback integration

The framework-neutral callback orchestration is in `elite_trade/oauth_callback.py`. It issues state through the SQLite one-time store, consumes state before exchanging the short-lived code, and stores tokens only through the encrypted vault. It does not itself create an HTTPS listener or prove a Spotware connection.

When deploying an HTTP adapter, require HTTPS, validate the expected Host, redact callback query strings (especially `code` and `state`) from proxy/server access logs, apply request-size limits, and keep the vault key in the host's secret manager. Never expose callback query strings in application logs.

## SDK transport status

`elite_trade/ctrader_transport.py` now wraps Spotware's asynchronous Python SDK lifecycle for TCP connection, application authentication, account discovery, explicit account selection, account authentication, heartbeat scheduling and shutdown. It intentionally contains no order-placement API. Its tests use mocked SDK/client objects; this is not a live Spotware integration test.

## Current next milestone

Verify the SDK transport against the approved application and a Pepperstone demo account, then implement runtime instrument metadata discovery and quote subscriptions. Only then can we implement and test demo order execution.

Official documentation: https://help.ctrader.com/open-api/
