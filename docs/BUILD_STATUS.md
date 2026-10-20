# EliteTrade AI build status

## Foundation branch
This branch adds the first Android Gradle project structure and a GitHub Actions workflow intended to build a debug APK. It also adds initial typed models for strategy signals, market-data provenance, simulated positions, and a hard-limit risk gate.

## What is and is not implemented
- The existing Android activity is still an early demo terminal.
- Displayed market prices remain illustrative simulated values.
- Exness/MT5 are not connected.
- PaperTrade is a data model only; it does not yet provide a complete order lifecycle, persistent journal, or performance analytics.
- AgentSignal is a data model only; independent trend, momentum, event, and strategy agents are not yet integrated.
- RiskGuardian is a first-pass validation gate, not a certified risk system.
- No live orders, broker credentials, or network execution are implemented.

## Next milestones
1. Verify the Android build in Actions and fix any build errors.
2. Add unit tests for the risk gate and paper-trade validation.
3. Add a deterministic simulated market generator and paper portfolio/journal.
4. Implement independent analysis agents and a strategy competition interface.
5. Add chart/timeframe and event intelligence with explicit data provenance.
6. Review free public data providers before adding any network integration.

All development remains paper-only until the user explicitly changes scope after testing and safety review.
