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
- Fail-closed mapper for authenticated cTrader light/full symbol, asset and account-access metadata, with account-scoped transport discovery.
- Demo-only market-data composition that filters broker metadata to tradable symbols, subscribes to their spot quotes, maintains a freshness-bounded bid/ask cache, and clears prices when the authenticated session is lost. This remains mocked/unit-test scope until a real Pepperstone account is connected.

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


The latest adaptive-risk integration quality-gate run on 2026-11-01, GitHub Actions run `41309722555`
(commit `0eb155b68386e4b7aef8c3d2a362f0a56387a0aa`), passed all four jobs:
202 unit tests, 83% measured total coverage, Ruff lint, Bandit source scan,
pip-audit dependency vulnerability audit, and wheel build/import smoke tests. pip-audit reported no known vulnerabilities
in the declared runtime dependencies at scan time. The report is retained as a
workflow artifact; no minimum coverage threshold is claimed. This verifies the
tested code only, not broker connectivity, order execution or profitability.
See `bot/PROJECT_STATUS.md` for the current status and remaining integration gates. The Spotware OpenApiPy source is vendored under
`ctrader_open_api/` from upstream commit
`e944996e6fe9436b8b4a1b668da4ac41a826bb85`, with its MIT license included.
The CI checks demonstrate code/package quality only. They do not prove broker
connectivity, Pepperstone account authentication, real market data, demo order
execution or live trading.

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

The bot starts in analysis-only mode. The daily profit target defaults to $50 but can be changed in the private Telegram chat with `/target 500`, `/target 1500`, `/target 5000`, or another valid USD amount. The setting is persisted across restarts and cannot be changed after that day's protection or a daily lock activates. The $20 maximum giveback remains fixed. The target is a profit-protection trigger, not an automatic stop. The trading loop now applies adaptive cash-risk plans to candidate volume using caller-supplied broker-verified loss-per-unit and volume limits. The 25% post-target reduction is unit-tested. The runtime provider must still supply accurate broker-derived balances, market classification and instrument loss/volume metadata; no orders are submitted. Never put credentials, OAuth tokens, Telegram tokens or account identifiers in source files or commit them to Git. Use the host's secret/environment-variable manager.

Live trading must remain disabled until OAuth, account selection, symbol metadata, live quotes, order/position reconciliation, broker-confirmed protective stops, daily loss limits, duplicate-order protection, reconnect handling and emergency shutdown are implemented and verified on demo. Live activation also requires your explicit approval.

## Telegram control runtime (safe mode)

The private-chat controller now has a runnable entry point. From the `bot/`
directory, install the requirements and set the following environment values
(or place them in an untracked local `.env` file):

```dotenv
TELEGRAM_BOT_TOKEN=your_existing_bot_token
TELEGRAM_CHAT_ID=your_numeric_private_chat_id
ELITE_TRADING_TIMEZONE=UTC
ELITE_DAILY_TARGET_USD=50
ELITE_MAX_DAILY_LOSS_USD=50
ELITE_MAX_GIVEBACK_USD=20
ELITE_MAX_OPEN_POSITIONS=1
ELITE_KILL_SWITCH_FILE=state/KILL_SWITCH
ELITE_SESSION_DATABASE=state/daily_session.sqlite3
```

Start the control process with:

```bash
PYTHONPATH=. python -m elite_trade.telegram_runtime
```

It sends a startup notice, then polls Telegram for private-chat commands.
Supported controls include `/status`, `/target`, `/pause`, `/kill` and the
explicitly confirmed emergency reset. `/closeall` remains unavailable because
broker-side position closure is not implemented. The process does not
authenticate to cTrader, read market data, start a strategy loop or submit
orders. Keep the Telegram token private and never commit `.env`.

The local kill-switch file is persistent. Do not remove it to make the bot
start; use the explicit Telegram emergency reset only after reviewing why it
was activated. This runtime's kill-switch path is separate from the legacy
Termux `ELITE_STOP` file unless you deliberately configure the same path.

## OAuth callback integration

The framework-neutral callback orchestration is in `elite_trade/oauth_callback.py`. It issues state through the SQLite one-time store, consumes state before exchanging the short-lived code, and stores tokens only through the encrypted vault. It does not itself create an HTTPS listener or prove a Spotware connection.

When deploying an HTTP adapter, require HTTPS, validate the expected Host, redact callback query strings (especially `code` and `state`) from proxy/server access logs, apply request-size limits, and keep the vault key in the host's secret manager. Never expose callback query strings in application logs.

## Vendored SDK and transport status

The Spotware Python SDK source is maintained locally in `ctrader_open_api/` under its upstream MIT license. Its exact upstream commit is recorded in `ctrader_open_api/UPSTREAM.md`. Runtime dependencies are maintained separately in `requirements.txt` and checked by pip-audit.



`elite_trade/ctrader_transport.py` wraps Spotware's asynchronous Python SDK lifecycle for TCP connection, application authentication, access-token account discovery, explicit account selection, account authentication, heartbeat scheduling and shutdown. It intentionally contains no order-placement API. Its tests use mocked SDK/client objects; this is not a live Spotware integration test.

## Market-data runtime composition

`elite_trade/ctrader_market_data_runtime.py` composes the authenticated instrument catalogue with `CTraderMarketData`. It subscribes only to symbols whose authenticated metadata reports `is_tradable`, scopes spot events to the selected account, exposes only fresh bid/ask quotes, and discards its cache when the transport leaves the authenticated state. It does not create trade candidates or submit orders. Unit tests cover the mocked composition; this is not proof of a real Pepperstone quote feed.

## Current next milestone

Verify the SDK transport and market-data composition against the approved application and a Pepperstone demo account. Then wire verified quotes and fresh broker account snapshots into the trading-cycle candidate provider and pre-trade gate. Demo order execution remains blocked until that end-to-end risk path and broker-side protection/reconciliation are complete.

Official documentation: https://help.ctrader.com/open-api/
