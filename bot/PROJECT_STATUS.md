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
- Branch comparison immediately before this handoff refresh: 71 commits ahead of main, 0 behind (main preserved).
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
- Vendored Spotware OpenApiPy source (MIT license) from upstream commit e944996e6fe9436b8b4a1b668da4ac41a826bb85, with modern runtime dependencies maintained separately.
- Spotware OpenApiPy transport lifecycle: SDK TCP client setup, application authentication, access-token account discovery, explicit account selection, account authentication, 10-second heartbeat scheduling and shutdown. No order-placement API is exposed by this adapter.
- Framework-neutral OAuth callback orchestration: server-side state issuance/consumption, authorization-code exchange and encrypted token-vault persistence; HTTP/TLS deployment remains separate and unverified.
- Unit tests for OAuth callback state handling, token persistence, state replay rejection, provider denial, OAuth state expiry/replay protection, token encryption/permissions, Telegram chat authorization and refresh HTTP method.
- Tests for strategy, risk, kill switch, execution policy and OAuth helper.
- GitHub Actions workflow to install dependencies and run unit tests.
- Documentation of cTrader OAuth and integration requirements.

## Recent engineering commits (2026-10-31)

The following changes are committed on `pepperstone-ctrader-bot` after reviewing
Spotware's current Open API model definitions:

- `bot/elite_trade/instrument.py`: immutable cTrader instrument/account metadata
  snapshot, retaining protocol volume in integer cents of a unit; separately
  validates symbol trading mode and account access rights; validates short-selling
  permission; derives display-price precision from `digits`; provides directional
  price rounding and fail-closed volume-step rounding.
- `bot/tests/test_instrument.py`: 16 unit tests for cTrader-native volume units,
  metadata validation, permissions, and price rounding.
- `bot/elite_trade/ctrader_instruments.py`: pure mapper from authenticated
  `ProtoOASymbol`, `ProtoOALightSymbol`, `ProtoOATrader` and `ProtoOAAsset`
  messages into the validated metadata model. It rejects missing protocol fields,
  mismatched symbol IDs and unresolved base/quote assets.
- `bot/tests/test_ctrader_instruments.py`: 6 tests for protocol mapping and fail-closed
  handling of incomplete or inconsistent broker metadata.
- `bot/elite_trade/sizing.py`: pure fail-closed sizing function returning cTrader
  API volume cents. It requires an explicit quote-currency loss factor per volume
  cent, quote-to-deposit conversion rate, conversion freshness, and cost reserves.
  It caps at broker maximum, rounds down, and rejects a result below broker minimum.
- `bot/tests/test_sizing.py`: 13 unit tests for risk-budget bounds, stale/missing
  conversion data, broker limits, costs, and fail-closed inputs.

These are committed code additions, not proof of broker-derived values. The
per-volume-cent P&L factor and conversion rate are caller-supplied inputs and have
not yet been populated from an authenticated Pepperstone account. The new tests
have not yet been independently confirmed by a completed GitHub Actions run.
No broker connection, order submission, or live execution was added.

## Verified tests and automated quality gates

- Latest full quality-gate run: GitHub Actions run 41177210308 completed successfully on commit 40251b90c8b51f5bcc4dda17383484c1b6d3c7e9.
- Run: https://github.com/muwaaam-cell/EliteTradeAI/actions/runs/41177210308
- All four jobs passed: 33 Python unit tests with branch coverage, Ruff lint, Bandit source-security scan, pip-audit dependency vulnerability scan, Python wheel build/import verification, and pip dependency-consistency checks.
- Measured total test coverage: 69% (coverage report retained as a workflow artifact). No minimum coverage threshold is claimed yet.
- The workflow also imports the vendored Spotware SDK and instantiates a generated protobuf request message successfully.
- Bandit passes with narrow, documented suppressions for fixed public HTTPS endpoint strings/urllib calls; these are not credentials or user-controlled destinations.
- pip-audit found no known vulnerabilities in the maintained runtime dependency set at the time of this run.
- These checks establish code/package quality for the tested commit only. They do not prove real Spotware connectivity, Pepperstone account authentication, market data, demo order execution or live trading.

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
2. Complete a hardened HTTPS callback server/deployment and register its exact URI after Spotware approval.
3. Complete real OAuth exchange and verify encrypted token persistence using the approved application.
4. Verify the existing SDK transport against a Pepperstone demo account, including application and account authentication.
5. Discover instruments and validate symbol metadata, tick/volume precision and conversion data.
6. Subscribe to live quotes and retrieve historical candles; implement real reconnect/recovery handling.
7. Build broker-aware risk sizing and demo-only execution, protective-stop confirmation and position reconciliation.
8. Add Telegram polling/alerts and operational recovery; test kill switch against the live bot process.
9. Run repeatable end-to-end demo tests for open, modify, close, restart and failure cases.
10. Evaluate a genuinely free continuous runtime; only after all gates, request explicit live approval.

## Official technical references

- https://help.ctrader.com/open-api/
- https://help.ctrader.com/open-api/account-authentication/
- https://help.ctrader.com/open-api/connection/
- https://help.ctrader.com/open-api/python-SDK/python-sdk-index/

## Continuation instruction for a future chat

Continue from this file and inspect the live GitHub branch before making claims. Do not repeat discovery work unnecessarily. Do not say the broker is connected, the bot is complete, or live trading works unless supported by a real integration test. Make commits on pepperstone-ctrader-bot, preserve main, run the GitHub Actions tests after code changes, and update this handoff file whenever project status materially changes.
