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
- Position/order reconciliation request forwarding.
- Daily target and giveback protection, daily loss lock, kill switch and adaptive risk sizing.
- Durable duplicate-resistant order-intent ledger with restart persistence and uncertain-submission reconciliation.
- Fail-closed pre-trade checks; the trading loop manages existing positions before evaluating new entries.

## What is not yet complete

The following are prerequisites to calling this a functioning Pepperstone trading bot:

1. Spotware application approval and verified Pepperstone demo-account OAuth credentials.
2. A runtime that connects the authenticated transport, catalogue, quote cache, account snapshots and strategy inputs.
3. Broker-derived price/contract-value and account-currency conversion calculations for stop-loss cash risk. Do not substitute generic pip-value assumptions.
4. A durable, idempotent order/execution adapter with accepted, rejected, partial-fill and completed states. The current ledger does not submit orders and does not yet model partial fills.
5. Broker-confirmed stop-loss placement and modification, close handling, and authoritative restart/reconnect reconciliation.
6. End-to-end tests against a Pepperstone demo account covering order submission through final position closure.
7. Operational hosting, health monitoring, Telegram controls/alerts and safe secret management.

Until these items are implemented and verified, this code is an integration foundation—not an operational autonomous trading service. CI/unit tests do not prove broker connectivity, execution quality or profitability.

## Safety rules

- Live trading must remain disabled until the complete demo execution lifecycle has passed.
- Missing, stale, inconsistent or unverified broker data must block new entries.
- Existing-position management and emergency close capability must not depend on permission to open new trades.
- Never commit cTrader client secrets, access/refresh tokens, Telegram tokens or account credentials. Supply secrets through a protected runtime secret store or environment configuration.
- Do not increase risk to recover losses or chase a daily target.

## Project layout

- `bot/elite_trade/` — Python trading engine, risk controls and cTrader integration.
- `bot/tests/` — automated tests.
- `SharpEntry/` — separate Android project; not the Pepperstone execution service.

## Development checks

Run the Python checks from `bot/` using the project's configured test/lint/security commands. A passing unit suite validates only the tested software behavior; broker demo verification remains a separate release gate.
