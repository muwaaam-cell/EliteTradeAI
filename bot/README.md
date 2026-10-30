# EliteTrade AI — Pepperstone cTrader bot

This branch starts the **external Python trading-bot** implementation. It is separate from the legacy Android prototype on `main`.

## Current status

- Strategy/risk foundation: initial implementation.
- cTrader Open API connection and OAuth callback: not implemented yet.
- Pepperstone account authentication: not connected or tested.
- Market data and symbol discovery: not connected or tested.
- Order execution: deliberately disabled.
- Live trading: disabled.

## Requirements

- Python 3.11+
- A cTrader Open API application approved by Spotware.
- The application's client ID and client secret, stored only as environment secrets.
- A cTrader account linked to Pepperstone and user-approved OAuth access.
- A continuously running Python process for an external bot. A phone app or a short-lived serverless function is not an always-on trading host.

Install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

On Windows, activate with `.venv\\Scripts\\activate`.

## Safety

The bot starts in analysis-only mode. Never put credentials, OAuth tokens, Telegram tokens, or account identifiers in source files or commit them to Git. Use your host's secret/environment-variable manager.

Before any live execution is enabled, the project must implement and verify OAuth, account selection, broker symbol metadata, live prices, order/position reconciliation, protective stop-loss handling, daily loss limits, duplicate-order protection, reconnect handling, and a kill switch. Demo testing and explicit user approval are required first.

## Next implementation milestone

Implement the official Spotware cTrader Open API Python SDK adapter and OAuth callback after the application is approved and its real redirect URI is configured. Official docs: https://help.ctrader.com/open-api/
