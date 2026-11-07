# EliteTrade AI — Pepperstone / cTrader bot

This repository contains the Python trading-bot implementation under `bot/`. The separate `SharpEntry/` Android project is not the trading engine and does not connect to Pepperstone.

## Current integration status

**Broker target:** Pepperstone through cTrader Open API (Spotware).  
**Execution mode:** disabled. No live or demo orders can be submitted by the current trading loop.

Implemented foundations in `bot/elite_trade/` include:

- cTrader application/account authentication transport and explicit account selection.
- Account-scoped symbol and asset catalogue retrieval, with strict metadata validation.
- Account balance/equity and open-position P&L snapshot parsing.
- cTrader quote subscription request and spot-event forwarding.
- A validated spot-quote cache that decodes bid/ask using instrument precision, rejects unknown symbols and wrong-account events, and expires stale quotes.
- Account-scoped cTrader execution-event and order-error event forwarding. Supported execution events can be decoded and applied to the durable local ledger when supplied a verified enum map.
- Position/order reconciliation request forwarding, with the request validated against the actual generated Spotware protobuf schema.
- Daily target and giveback protection, daily loss lock, kill switch and adaptive risk sizing.
- Durable duplicate-resistant order-intent ledger with restart persistence, uncertain-submission reconciliation and cumulative partial-fill tracking.
- Validated cTrader protobuf builders for MARKET entry requests, position SL/TP amendment requests and position-close requests. Tests instantiate the real generated Spotware protobuf types. These builders do not send requests, validate broker-side execution outcomes, or confirm that stops are active.
- Fail-closed pre-trade checks; the trading loop manages existing positions before evaluating new entries.

## What is not yet complete

The following are prerequisites to calling this a functioning Pepperstone trading bot:

1. Spotware application approval and verified Pepperstone demo-account OAuth credentials.
2. A runtime that connects the authenticated transport, catalogue, quote cache, account snapshots and strategy inputs.
3. Broker-derived price/contract-value and account-currency conversion calculations for stop-loss cash risk. Do not substitute generic pip-value assumptions.
4. A durable, idempotent broker order/execution adapter that submits requests and processes accepted, rejected, partial-fill and completed states. The local ledger models partial fills, and authenticated execution events can be decoded and applied to it; no broker order submission, request/response lifecycle orchestration or position-protection confirmation is wired yet.
5. Broker-confirmed stop-loss placement and modification, close handling, and authoritative restart/reconnect reconciliation.
6. End-to-end tests against a Pepperstone demo account covering order submission through final position closure.
7. Operational hosting, health monitoring, Telegram controls/alerts and safe secret management.

Until these items are implemented and verified, this code is an integration foundation—not an operational autonomous trading service. CI/unit tests do not prove broker connectivity, execution quality or profitability.

## Safety rules

- Live trading is hard-locked by an internal release-certification constant (`LIVE_RELEASE_CERTIFIED = False`); environment flags cannot bypass it. Do not change this latch until the complete demo execution lifecycle and manual safety review have passed.
- Missing, stale, inconsistent or unverified broker data must block new entries.
- Existing-position management and emergency close capability must not depend on permission to open new trades.
- Never commit cTrader client secrets, access/refresh tokens, Telegram tokens or account credentials. Supply secrets through a protected runtime secret store or environment configuration.
- Do not increase risk to recover losses or chase a daily target.

## Project layout

- `bot/elite_trade/` — Python trading engine, risk controls and cTrader integration.
- `bot/tests/` — automated tests.
- `SharpEntry/` — separate Android project; not the Pepperstone execution service.

## Development checks

Run the Python checks from `bot/` using the project's configured test/lint/security commands. The branch workflow is configured for pushes to `pepperstone-ctrader-bot` and pull requests targeting `main`. Verified GitHub Actions run #253 on commit `3b46417` passed all four jobs: 235 unit tests, 83% measured coverage, Ruff lint, Bandit source scan, dependency audit (no known vulnerabilities reported), and wheel build/import. These CI results validate the tested code paths only; they do not verify Pepperstone connectivity, live order execution, broker-side protection, execution quality or profitability. Broker demo verification remains a separate release gate.
