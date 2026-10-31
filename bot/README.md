# EliteTrade AI — Pepperstone cTrader bot

This is the external Python bot codebase on the separate `pepperstone-ctrader-bot` branch. The legacy Android prototype on `main` is unchanged.

## Verified code currently in this branch

- Strategy/risk foundation: EMA/RSI/ATR analysis, risk checks, stale-quote/spread/volume policy and local kill switch.
- cTrader OAuth helper: consent URL construction, cryptographically random state, callback-state comparison, authorization-code exchange and refresh-token exchange.
- Encrypted token vault and one-time, expiring OAuth state store.
- Framework-neutral OAuth callback orchestration. It is not an HTTPS callback server.
- Spotware OpenApiPy transport lifecycle adapter for TCP connection, application authentication, account discovery, explicit account selection, account authentication, heartbeat scheduling and shutdown.
- Allow-listed Telegram controls for status, pause, resume and emergency kill. Close-all is deliberately unavailable until broker execution exists.
- Automated Python unit tests and GitHub Actions quality checks.

## Independent review: use the correct branch

Repository: https://github.com/muwaaam-cell/EliteTradeAI

Working branch: `pepperstone-ctrader-bot`

The default `main` branch is the older Android prototype. An audit of `main` alone is not an audit of this bot. Start with `bot/PROJECT_STATUS.md`, then inspect the actual files in `bot/elite_trade/`, `bot/tests/` and `.github/workflows/bot-tests.yml`.

Ask reviewers to distinguish implemented code, mocked tests, real external connectivity and live order execution. Do not treat documentation as proof that a feature works.

## Automated checks

GitHub Actions runs on pushes and pull requests to `pepperstone-ctrader-bot`:

- Python unit tests with branch coverage reporting.
- Ruff lint checks.
- Bandit source-security scan.
- pip-audit dependency vulnerability scan.
- Python wheel build and installed-package import smoke test.

The coverage report is retained as a workflow artifact. No minimum coverage threshold is claimed until a measured baseline has been reviewed. The Spotware OpenApiPy source is vendored under `ctrader_open_api/` from upstream commit `e944996e6fe9436b8b4a1b668da4ac41a826bb85`. The upstream MIT license is included in that directory. This avoids the upstream wheel's stale exact pins on vulnerable transitive dependencies. CI imports the vendored SDK and generated protobuf classes and audits the maintained runtime dependency set. This does not replace a real broker integration test. A green workflow means these automated checks passed for that commit; it does not certify broker connectivity or trading safety.

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

## Vendored SDK and transport status

The Spotware Python SDK source is maintained locally in `ctrader_open_api/` under its upstream MIT license. Its exact upstream commit is recorded in `ctrader_open_api/UPSTREAM.md`. Runtime dependencies are maintained separately in `requirements.txt` and checked by pip-audit.



`elite_trade/ctrader_transport.py` wraps Spotware's asynchronous Python SDK lifecycle for TCP connection, application authentication, access-token account discovery, explicit account selection, account authentication, heartbeat scheduling and shutdown. It intentionally contains no order-placement API. Its tests use mocked SDK/client objects; this is not a live Spotware integration test.

## Current next milestone

Verify the SDK transport against the approved application and a Pepperstone demo account, then implement runtime instrument metadata discovery and quote subscriptions. Only then can we implement and test demo order execution.

Official documentation: https://help.ctrader.com/open-api/
