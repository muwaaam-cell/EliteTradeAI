# Stage 2 — Simulated market and paper portfolio

## Added

- `SimulatedMarketEngine`: seeded, repeatable price changes for five illustrative symbols. Each snapshot is tagged `SIMULATED`.
- `PaperPortfolio`: in-memory simulated positions, risk-gated opening, closing, and a simple trade history.
- `AnalysisAgent`: baseline moving-average/RSI opinion generator. It does not execute orders.
- `EliteIQ`: a basic consensus coordinator that compares same-symbol agent opinions and defaults to WAIT when input is missing, confidence is below threshold, or agents disagree.
- JUnit tests are configured and the latest CI run completed `testDebugUnitTest` successfully.

## Important limitations

- Prices are artificial and not connected to Exness, MT5, or an external market feed.
- The portfolio is in-memory only; restarting the app loses its journal.
- P&L is a simplified risk-distance estimate, not broker-grade contract/tick-value accounting.
- The technical agent is educational and not evidence of profitable trading.
- EliteIQ only combines provided opinions; it does not independently fetch data, execute trades, or override RiskGuardian.
- UI integration remains incomplete; the existing Trade screen does not yet use this new pipeline end to end.

## Next

1. Connect simulated snapshots to a single paper decision loop and enforce RiskGuardian for every proposed order.
2. Integrate the loop visibly in the app and add a persistent journal.
3. Add robust order sizing, restart/recovery tests, and realistic instrument/accounting rules.
4. Add backtesting/strategy competition and monitoring only after the paper path is coherent.
