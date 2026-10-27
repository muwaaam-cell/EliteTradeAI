# SharpEntry FastForex WebSocket feed (starter)

This is a **standalone Node.js feed client**, not part of the Android app process.

## Run

1. Install a current Node.js LTS release.
2. In this directory, run `npm install`.
3. Set `FASTFOREX_API_KEY` in your local shell or deployment secret store. Do not commit the key into GitHub or put it in the Android app.
4. Run `npm start`.

Example for a POSIX shell:

```sh
export FASTFOREX_API_KEY="your_key_here"
npm install
npm start
```

## Important limitations

- The WebSocket endpoint, authentication message, subscription message, stream names, and symbol format are copied from the supplied starter snippet and have **not been verified against FastForex documentation or tested with a real account**.
- Confirm the provider's current documentation and your plan's permissions before relying on this client.
- This starter prints incoming messages to the console. It does not yet normalize/store prices, expose an authenticated API to the Android app, or connect to Exness/MetaTrader 5.
- The sample subscribes only to EUR/USD and XAU/USD. It does not cover all instruments on an Exness account.
- It places no trades. Do not treat a connected WebSocket as proof that prices are valid or that live trading is enabled.
