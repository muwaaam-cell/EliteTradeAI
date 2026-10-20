# EliteTrade AI — Android foundation (paper mode)

EliteTrade AI is being developed in stages as a native Android trading research terminal. The current branch contains an early UI prototype, a Gradle build setup, initial market/risk models, a baseline analysis agent, and the first Elite IQ consensus coordinator. It is not a finished trading system.

## Latest build check

GitHub Actions run [#22 — Fix missing class closing brace in MainActivity](https://github.com/muwaaam-cell/EliteTradeAI/actions/runs/39429046754) completed successfully on 2026-10-20. The run passed the Maven dependency checks, completed `testDebugUnitTest`, built the debug APK, and uploaded the `EliteTradeAI-debug-apk` artifact. This confirms compilation and the included unit-test task for that commit; it does not prove the app is feature-complete or profitable.

## Build/download using only your phone

1. Open the [Actions tab](https://github.com/muwaaam-cell/EliteTradeAI/actions).
2. Open a successful **Build EliteTrade AI APK** run.
3. In **Artifacts**, download **EliteTradeAI-debug-apk**.
4. Extract `app-debug.apk` from the ZIP and install it on Android. Android may ask permission to install apps from your browser or file manager.

GitHub Actions usage and artifact availability depend on current account limits. Do not publish API keys, passwords, or broker credentials.

## Safety and current limitations

- Paper/demo mode only. No live order execution.
- Market prices in the existing screen are illustrative and simulated, not live quotes.
- Exness and MT5 are not connected.
- The virtual balance is not money and cannot be withdrawn.
- The new AgentSignal, MarketSnapshot, PaperTrade, RiskGuardian, and EliteIQ classes are foundational components, not a completed autonomous engine.
- Passing a CI build and unit tests does not establish trading profitability, broker compatibility, or uninterrupted Android background execution.

## Development status

- Build foundation: Gradle project and automated debug APK workflow.
- Analysis foundation: baseline trend/momentum analysis and Elite IQ agent-consensus decision logic.
- Safety foundation: risk gate and unit tests for paper-mode enforcement, risk/exposure limits, market snapshot validation, and signal consensus.
- Still pending: integration of the simulation and agent pipeline into app screens, persisted journal, backtesting/strategy competition, event intelligence, multi-timeframe analysis, visible kill switch, and secure broker-demo integration.

## Next milestones

1. Integrate analysis and Risk Guardian into a single paper-trading decision flow.
2. Add persistence, deterministic paper execution and realistic trade accounting.
3. Add backtesting, strategy competition, multi-timeframe analysis, and event/data-quality monitors.
4. Verify all user-facing safety controls and restart recovery.
5. Investigate a secure and compatible demo-only broker bridge; keep live execution disabled.

No paid service or live broker integration is required for the current stage.
