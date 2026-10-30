# cTrader Open API integration notes

## Verified against Spotware documentation

- OAuth 2.0 is used for account access and requires user consent.
- The accounts scope is read-only; trading scope permits trading operations.
- Authorization codes are short-lived (approximately one minute) and must be exchanged promptly.
- Access tokens normally last 30 days; refresh tokens renew them.
- After OAuth, the application authenticates itself, retrieves accounts available to the token, and authenticates the selected trading account.
- cTrader Open API supports demo and live accounts; Spotware recommends demo testing before live use.
- The official Python SDK package is ctrader-open-api. The Python SDK uses TCP; WebSocket is not supported by that SDK.
- Spotware recommends heartbeats every 10 seconds and serialized message handling.

## Application setup

1. Wait until Spotware approves application ID 46574.
2. Add a real HTTPS redirect URI to the approved application.
3. Store the exact registered URI in CTRADER_REDIRECT_URI.
4. Store client ID and client secret in the runtime secret manager, never in Git.
5. Start with a demo cTrader account and request only the permissions required for each test.
6. Verify OAuth state, token exchange, account list, selected account and account-auth response before requesting prices.

Do not use the Playground redirect URI in production. Do not invent a redirect URI before a real callback service exists.

## Current code scope

elite_trade/ctrader_oauth.py builds a consent URL with a cryptographically random state value, validates callback state, and implements token exchange/refresh helpers. Unit tests mock the token endpoint; they do not contact Spotware.

## Still not implemented or verified

- HTTPS callback server and durable encrypted token storage.
- cTrader SDK connection lifecycle, heartbeat, reconnection and account authentication.
- Pepperstone account verification.
- Broker symbol metadata and volume/price precision handling.
- Live quote subscription, historical candle retrieval and stale-data monitoring.
- Order submission, modification, cancellation and position reconciliation.
- Broker-confirmed protective stops and emergency position closure.
- End-to-end demo test and any live test.

## Official sources

- https://help.ctrader.com/open-api/account-authentication/
- https://help.ctrader.com/open-api/connection/
- https://help.ctrader.com/open-api/python-SDK/python-sdk-index/
- https://help.ctrader.com/open-api/
