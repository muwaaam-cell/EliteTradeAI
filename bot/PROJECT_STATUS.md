# EliteTrade AI — project handoff and source of truth

Last verified: 2026-10-31 (UTC).

## Product direction

- Product: unattended trading bot, not an Android trading app.
- Broker: Pepperstone through cTrader Open API.
- Account platform: cTrader.
- Target instruments: discover every instrument available to the user's authorized Pepperstone account at runtime; do not hardcode a guessed list.
- Control channel requested: Telegram, with Android as the user's control device.
- Cost constraint: avoid paid services; no PC/VPS budget has been stated. Do not promise free 24/7 hosting without testing a real hosting option.
- Live trading is the eventual goal. Demo validation is a required engineering gate, not the final product.

## Repository

- Repository: https://github.com/muwaaam-cell/EliteTradeAI
- Working branch: pepperstone-ctrader-bot
- Legacy default branch: main (old Android prototype; do not overwrite or present it as the bot).
- Branch comparison last checked: 48 commits ahead of main, 0 behind (main preserved; recheck after future changes).
- Working bot source: https://github.com/muwaaam-cell/EliteTradeAI/tree/pepperstone-ctrader-bot/bot

## Spotware application

- Application ID: 46574.
- Latest user-reported status: Pending.
- Do not invent or register a redirect URI until an HTTPS callback endpoint actually exists and the application is approved.
- Never ask for passwords, one-time codes, client secrets, access tokens or refresh tokens in chat.
- Credentials must be entered only into a trusted secret manager/environment configuration, never committed to Git.

## Implemented and committed

- Python package foundation and configuration.
- Transparent baseline EMA20/EMA50 + RSI/ATR signal analysis.
- Broker-independent risk checks and cash risk-budget helper (not lot sizing).
- Fail-closed order-intent policy: stale quote, spread, volume, stop direction/distance, broker connection, symbol verification and explicit approval checks.
- Persistent local kill-switch flag.
- cTrader OAuth URL builder, cryptographically random state, callback state comparison, authorization-code exchange (GET) and refresh-token helper (POST), matching Spotware's documented methods.
- Encrypted-at-rest token vault using a separately managed Fernet key.
- Allow-listed Telegram control module with status, pause, resume and emergency kill commands; close-all explicitly reports unavailable until broker execution is verified.
- SQLite-backed, expiring, one-time OAuth state store (stores only SHA-256 state digests; atomic consume prevents replay).
- Spotware OpenApiPy transport lifecycle: SDK TCP client setup, application authentication, access-token account discovery, explicit account selection, account authentication, 10-second heartbeat scheduling and shutdown. No order-placement API is exposed by this adapter.
- Framework-neutral OAuth callback orchestration: server-side state issuance/consumption, authorization-code exchange and encrypted token-vault persistence; HTTP/TLS deployment remains separate and unverified.
- Unit tests for OAuth callback state handling, token persistence, state replay rejection, provider denial, OAuth state expiry/replay protection, token encryption/permissions, Telegram chat authorization and refresh HTTP method.
- Tests for strategy, risk, kill switch, execution policy and OAuth helper.
- GitHub Actions workflow to install dependencies and run unit tests.
- Documentation of cTrader OAuth and integration requirements.

## Verified tests and automated quality gates

- Previous baseline: GitHub Actions run 41150391234 completed successfully on commit 16ed7f31f8980cc2760357618d6b62f9ac727ad2. It installed dependencies and passed the unit-test job, including mocked SDK transport, OAuth, Telegram, token-vault, strategy, risk and execution-policy tests.
- Baseline run: https://github.com/muwaaam-cell/EliteTradeAI/actions/runs/41150391234
- On 2026-10-31, the workflow was expanded to run unit tests with branch coverage reporting, Ruff lint, Bandit source-security scanning, pip-audit dependency vulnerability scanning, and Python wheel build/import verification.
- New quality-gate runs were triggered by the workflow and packaging changes. Their results must be checked before claiming the new checks pass.
- Coverage is reported as an artifact; no minimum threshold is claimed until a measured baseline has been reviewed.
- Passing unit tests and static checks do not prove real Spotware connectivity, demo execution or live trading.

## Not implemented / not verified

- HTTPS OAuth callback HTTP server/deployment, TLS/host hardening, callback access-log redaction and runtime secret provisioning. Callback orchestration is implemented and unit-tested, but has not exchanged a real Spotware code or persisted real broker tokens.
- Live SDK transport connectivity, application authorization, account list retrieval and account authorization. The adapter implementation exists, but only its mocked lifecycle tests have passed.
- Pepperstone account connection.
- Runtime instrument discovery, tick/volume precision, contract size and conversion metadata.
- Live/historical market data, heartbeat/reconnect handling and multi-timeframe candle aggregation.
- Complete strategy validation/backtesting/out-of-sample analysis.
- Correct broker-aware position sizing.
- Order placement, modification, cancellation, position reconciliation and protective stop verification.
- Telegram control module exists and is unit-tested; deployment, long-running polling, alert routing and operational recovery are not verified.
- Continuous hosting and uptime.
- End-to-end demo execution; any live execution.

## Mandatory safety gates

1. Spotware application approved and exact redirect URI registered.
2. OAuth state validated; tokens stored encrypted outside Git.
3. Correct Pepperstone account selected and authenticated.
4. Account environment and all instrument metadata discovered from broker.
5. Live quotes and historical candles validated; stale/disconnected feed blocks decisions.
6. Position sizing computed from broker contract/tick/volume metadata and stop distance.
7. Daily equity drawdown, exposure, position-count, consecutive-loss, spread, slippage and duplicate-order protections tested.
8. Broker confirms stop-loss/take-profit and all positions/orders reconcile after reconnect.
9. Demo end-to-end tests pass for open, modify, close, restart and failure cases.
10. User explicitly approves live activation; live gate remains off until then.

## Next engineering sequence

1. Deploy a hardened HTTPS callback adapter, register its exact URI after Spotware approval, and provision runtime secrets.
2. Implement Spotware official Python SDK adapter and account-auth handshake.
3. Verify connection using a demo Pepperstone account.
4. Discover instruments and validate symbol metadata.
5. Subscribe to quotes and retrieve candles; implement heartbeat/reconnect.
6. Build broker-aware risk sizing and demo-only execution adapter.
7. Add Telegram alerts/commands with strict authorization and kill switch.
8. Run repeatable end-to-end demo tests.
9. Evaluate a genuinely free continuous runtime; if none is reliable, explain that 24/7 automation needs hosting and do not silently assume a free option.
10. Only after all gates, request explicit live approval.

## Official technical references

- https://help.ctrader.com/open-api/
- https://help.ctrader.com/open-api/account-authentication/
- https://help.ctrader.com/open-api/connection/
- https://help.ctrader.com/open-api/python-SDK/python-sdk-index/

## Continuation instruction for a future chat

Continue from this file and inspect the live GitHub branch before making claims. Do not repeat discovery work unnecessarily. Do not say the broker is connected, the bot is complete, or live trading works unless supported by a real integration test. Make commits on pepperstone-ctrader-bot, preserve main, run the GitHub Actions tests after code changes, and update this handoff file whenever project status materially changes.
