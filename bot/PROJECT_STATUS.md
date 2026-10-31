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
- Guarded adaptive compounding cash-risk budget: uses the lower of settled balance and current equity, so floating profit alone cannot increase risk and floating losses reduce it; supports an absolute cash-risk cap.
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
  mismatched symbol IDs and unresolved base/quote assets. It also resolves the
  account deposit currency from `ProtoOATrader.depositAssetId` and authenticated
  asset metadata; it does not guess a currency.
- `bot/tests/test_ctrader_instruments.py`: 9 tests for protocol mapping, account
  deposit-currency resolution and fail-closed handling of incomplete or inconsistent
  broker metadata.
- `bot/elite_trade/sizing.py`: pure fail-closed sizing function returning cTrader
  API volume cents. It requires an explicit quote-currency loss factor per volume
  cent, quote-to-deposit conversion rate, conversion freshness, per-volume cost
  reserves and a fixed-cost reserve for minimum fees.
  It caps at broker maximum, rounds down, and rejects a result below broker minimum.
- `bot/elite_trade/risk.py`: includes `calculate_compounding_risk_budget` and `calculate_snapshot_risk_budget`. The latter accepts only a validated `AccountRiskSnapshot`, calculates the budget using exact Decimal arithmetic from `min(settled_balance, equity)`, validates a 0–1% risk input and supports an absolute cash cap. It returns cash risk only, not volume. Tests cover account growth, floating profit/loss, invalid values, caps and rejection of unvalidated objects. It is now a tested pure-code bridge from the account snapshot model to the sizing budget; runtime strategy orchestration still needs to call it with fresh broker data.
- `bot/elite_trade/risk.py`: rejects non-finite equity/P&L, invalid position counters,
  and invalid or unset risk limits before permitting a trade intent. Cash risk budget
  calculation also rejects non-finite inputs.
- `bot/tests/test_sizing.py`: 15 unit tests for risk-budget bounds, stale/missing
  conversion data, broker limits, costs, and fail-closed inputs.
- `bot/tests/test_core.py`: additional tests for NaN/infinite risk snapshots,
  invalid risk limits and position counters.

These are committed code additions, not proof of broker-derived values. The
per-volume-cent P&L factor and quote-to-deposit conversion rate are still caller-
supplied inputs and have not yet been populated from authenticated Pepperstone
market/account data. The deposit-currency identifier can now be resolved from
account metadata, but live conversion-rate discovery is not implemented.
No broker connection, order submission, or live execution was added.

## Latest security hardening (2026-10-31)

- `telegram_control.py`: Telegram commands now require both the configured chat ID
  and sender ID to match, and the chat must be a one-to-one private chat. Group and
  supergroup commands are rejected, including messages in a group whose chat ID
  happens to be configured. This prevents other group members from issuing bot
  controls.
- `test_security_and_telegram.py`: added tests for group rejection, sender mismatch
  and non-numeric configured chat IDs.
- `execution_policy.py`: pre-trade validation now rejects NaN/infinite quote prices,
  timestamps, stop prices and limits; malformed side/volume values; and invalid
  quote-age, spread and stop-distance limits. These checks fail closed before any
  intent can pass policy.
- `test_execution_policy.py`: added malformed/non-finite input cases.
- Commits: `657d288`, `031ce3d`, `683fa0c`, `70fe313`.
- Final code commit `70fe3135987505151c3c7d9cde60037de68c599e` passed all four GitHub
  Actions jobs in run 41182510209: 86 unit tests, 75% total coverage, Ruff lint,
  Bandit source scan, dependency audit, wheel build/import and dependency checks.
  Run: https://github.com/muwaaam-cell/EliteTradeAI/actions/runs/41182510209.
  An earlier intermediate run failed while the sequential commits were still landing;
  the final commit's complete run above is the verified result.

## Account balance/equity snapshot mapping (2026-10-31)

- `bot/elite_trade/account_snapshot.py` maps authenticated cTrader `ProtoOATrader`
  balance and `ProtoOAGetPositionUnrealizedPnLRes` net P&L into exact Decimal
  deposit-currency values using matching `moneyDigits`.
- It rejects account-ID or precision mismatches, duplicate/invalid position IDs,
  non-positive equity and non-zero bonus fields until bonus accounting is fully
  verified. Equity is computed as balance plus broker-reported net unrealized P&L
  only when no bonus component is present.
- Eight mapper tests cover normal conversion, empty positions and fail-closed cases;
  transport tests cover paired requests, response account matching and snapshot
  delivery. `calculate_snapshot_risk_budget` now consumes the validated snapshot
  with exact Decimal arithmetic. No real broker account values have been received
  or verified, and no live strategy loop invokes this pipeline yet.
- Spotware references: https://help.ctrader.com/open-api/profit-loss-calculation/
  and https://help.ctrader.com/open-api/model-messages/.

## Verified tests and automated quality gates

- Earlier full quality-gate run: GitHub Actions run 41182157899 passed on commit 8e03a0221c4fbc410ec005b5abd89d52bd936118.
- Previous security-hardening quality run: GitHub Actions run 41182510209 passed on commit 70fe3135987505151c3c7d9cde60037de68c599e.
- Previous full quality-gate run: GitHub Actions run 41183925932 passed on commit 091e94a4d2868f5f852d724fe89bb751dd139447 (112 tests, 78% coverage).
- Previous full quality-gate run: GitHub Actions run 41184007147 passed on commit 629be83aca5cc55a8326898ff83c9c6764658f23 (115 tests, 78% coverage).
- Latest full quality-gate run: GitHub Actions run 41184176213 passed on commit d16f98211fa43b5d3d199e2a3617d50ad451e778.
- Latest run: https://github.com/muwaaam-cell/EliteTradeAI/actions/runs/41184176213
- All four jobs passed: 120 Python unit tests, Ruff lint, Bandit source-security scan, pip-audit dependency vulnerability scan, Python wheel build/import verification, and pip dependency-consistency checks.
- Measured total test coverage: 78% (coverage report retained as a workflow artifact). No minimum coverage threshold is claimed yet.
- The workflow also imports the vendored Spotware SDK and instantiates a generated protobuf request message successfully.
- Bandit passes with narrow, documented suppressions for fixed public HTTPS endpoint strings/urllib calls; these are not credentials or user-controlled destinations.
- pip-audit found no known vulnerabilities in the maintained runtime dependency set at the time of this run.
- These checks establish code/package quality for the tested commit only. They do not prove real Spotware connectivity, Pepperstone account authentication, market data, demo order execution or live trading.
- Previous passing baseline run 41177210308 remains historical only; the newer run above supersedes it.

## Current branch verification detail (2026-10-31)

- Code/test branch commit `f4a0ac3a99bcda4d2ec98a3afa9ea6fded7b10ee` passed GitHub Actions run 41184426110 (run #126): all four jobs passed, including 120 unit tests at 79% total coverage, Ruff lint, Bandit source scan, dependency audit, wheel build/import and dependency-consistency checks. The snapshot single-in-flight regression assertion is included in this test suite.
- The latest status-document commit `42f42cc357c5ed69e835d4ce9ec38dcd97704bc0` was independently checked in GitHub Actions run 41184801163 (run #127). All four jobs passed again; 120 tests passed at 79% coverage and pip-audit reported no known vulnerabilities.
- Latest verified run: https://github.com/muwaaam-cell/EliteTradeAI/actions/runs/41184801163.
- These green checks establish tested source/package quality only. The bot entry point still reports broker authentication and market data as NOT CONNECTED and order execution as DISABLED. No external cTrader/Pepperstone integration has been demonstrated.

## cTrader market-data foundation (2026-10-31)

- `bot/elite_trade/market_data.py` now converts cTrader spot events into validated
  bid/ask snapshots and trendbars into OHLC candles. It uses Spotware's documented
  relative-price scale (divide protocol price units by 100000), applies the symbol's
  decimal precision, checks partial/crossed quotes, validates source timestamps and
  checks trendbar low-plus-delta OHLC consistency.
- The quote model preserves local receive time separately from the optional broker
  event timestamp. A partial bid-only or ask-only event is explicitly marked
  incomplete and is not suitable for trading decisions.
- `bot/elite_trade/ctrader_transport.py` supports quote subscriptions only after
  account authentication, validates unique positive integer symbol IDs without
  coercing strings, floats or booleans, forwards spot events only for the authenticated
  account, and fails closed on account mismatch or cTrader protocol error responses.
- The transport can now request `ProtoOATraderRes` and
  `ProtoOAGetPositionUnrealizedPnLRes` for the authenticated account, match both
  responses to that account, emit a validated `AccountRiskSnapshot` only after both
  arrive, reject concurrent snapshot requests, and discard pending responses on
  disconnect. This orchestration is unit-tested with protocol-shaped fixtures only.
- Tests cover price conversion, partial and crossed quotes, malformed timestamps,
  trendbar OHLC conversion, subscription gating, invalid symbol-ID types,
  wrong-account events and API errors.
- Official Spotware data guide: https://help.ctrader.com/open-api/symbol-data/
- Official Spotware model reference: https://help.ctrader.com/open-api/model-messages/
- Relevant code commits: `51fd49d`, `431d781`, `465898f`, `1704c6d`,
  `ff939df`, `70c0238`, `3e0282a`, `6100bff`.
- Latest quality run `41183218865` passed all four GitHub Actions jobs on
  `6100bffc8af6acbebfdd51f42bfa41c78cffe5b3`: 98 unit tests, 77% total coverage,
  Ruff lint, Bandit source scan, dependency audit, and wheel build/import.
  Run: https://github.com/muwaaam-cell/EliteTradeAI/actions/runs/41183218865.
  The immediately preceding transport-code commit `3e0282a` also passed its
  lint, tests and package checks; the final test commit's complete four-job run
  above is the current verified result.

## Not implemented / not verified

- HTTPS OAuth callback HTTP server/deployment, TLS/host hardening, callback access-log redaction and runtime secret provisioning. Callback orchestration is implemented and unit-tested, but has not exchanged a real Spotware code or persisted real broker tokens.
- Live SDK transport connectivity, application authorization, account list retrieval and account authorization. The adapter implementation exists, but only its mocked lifecycle tests have passed.
- Pepperstone account connection.
- Runtime instrument discovery, tick/volume precision, contract size and conversion metadata.
- Live/historical market-data subscription has an initial implementation and unit tests; real broker data has not been received. Historical candle request orchestration, multi-timeframe aggregation, subscription lifecycle and robust reconnect/recovery remain incomplete.
- Complete strategy validation/backtesting/out-of-sample analysis.
- Wiring the snapshot-to-budget-to-volume pipeline into the live strategy runtime with freshness enforcement and verified conversion/P&L factors.
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
