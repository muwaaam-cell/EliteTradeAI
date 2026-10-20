# EliteTrade AI — Android foundation (paper mode)

EliteTrade AI is being developed in stages as a native Android trading research terminal. This repository currently contains an early UI prototype plus the first build and safety-model foundation. It is not a finished trading system.

## Build a debug APK using only your phone
1. Open the **Actions** tab in this repository.
2. Select **Build EliteTrade AI APK**.
3. If the workflow has not run automatically, open the workflow and tap **Run workflow** on this branch.
4. Open the run and wait for it to finish.
5. If successful, download the **EliteTradeAI-debug-apk** artifact and extract `app-debug.apk` from the ZIP.

GitHub Actions usage and artifact availability depend on GitHub's current account limits. No payment details should be needed for ordinary public-repository builds. Do not publish API keys, passwords, or broker credentials.

## Safety and current limitations
- Paper/demo mode only. No live order execution.
- Market prices in the existing screen are illustrative and simulated, not live quotes.
- Exness and MT5 are not connected.
- The virtual balance is not money and cannot be withdrawn.
- The new AgentSignal, MarketSnapshot, PaperTrade, and RiskGuardian classes are an initial foundation, not a completed multi-agent engine.
- The app has not yet been verified by a successful CI build. Check the Actions log before treating an APK as build-verified.

## Planned stages
1. Build verification and unit tests.
2. Deterministic simulated data and paper portfolio/journal.
3. Independent analysis agents and strategy comparison.
4. Risk Guardian integration and kill switch.
5. Multi-timeframe charts, event intelligence, and data-quality monitoring.
6. Investigate free public data sources before connecting any feed.

No paid service or live broker integration is required for the current stage.
