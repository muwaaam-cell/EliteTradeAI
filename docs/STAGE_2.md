# Stage 2 — Simulated market and paper portfolio

## Added
- `SimulatedMarketEngine`: seeded, repeatable price changes for five illustrative symbols. Each snapshot is tagged `SIMULATED`.
- `PaperPortfolio`: in-memory simulated positions, risk-gated opening, closing, and a simple trade history.
- `AnalysisAgent`: baseline moving-average/RSI opinion generator. It does not execute orders.

## Important limitations
- Prices are artificial and not connected to Exness, MT5, or an external market feed.
- The portfolio is in-memory only; restarting the app loses its journal.
- P&L is a simplified risk-distance estimate, not broker-grade contract/tick-value accounting.
- The technical agent is educational and not evidence of profitable trading.
- UI integration and automated tests remain follow-up tasks.

## Next
1. Add automated unit tests for risk, market provenance, and paper trade lifecycle.
2. Integrate the simulation into the Android screens with clear SIMULATED labels.
3. Add persistence for paper journal, then strategy competition and risk monitoring.
