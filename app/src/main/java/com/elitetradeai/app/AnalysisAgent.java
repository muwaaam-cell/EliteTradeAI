package com.elitetradeai.app;

/** Small baseline agent; it only emits an opinion and cannot place or approve trades. */
public final class AnalysisAgent {
    public AgentSignal evaluate(String symbol, double fastAverage, double slowAverage, double rsi) {
        if (symbol == null || symbol.trim().isEmpty() || !Double.isFinite(fastAverage)
                || !Double.isFinite(slowAverage) || !Double.isFinite(rsi) || rsi < 0 || rsi > 100) {
            return new AgentSignal("TrendMomentum", symbol == null ? "UNKNOWN" : symbol,
                    AgentSignal.Direction.NEUTRAL, 0.0, "Invalid or incomplete indicators; wait");
        }
        if (fastAverage > slowAverage && rsi >= 50 && rsi < 70) {
            return new AgentSignal("TrendMomentum", symbol, AgentSignal.Direction.BUY, 0.60,
                    "Fast average above slow average and RSI supports upward momentum; paper research only");
        }
        if (fastAverage < slowAverage && rsi <= 50 && rsi > 30) {
            return new AgentSignal("TrendMomentum", symbol, AgentSignal.Direction.SELL, 0.60,
                    "Fast average below slow average and RSI supports downward momentum; paper research only");
        }
        return new AgentSignal("TrendMomentum", symbol, AgentSignal.Direction.NEUTRAL, 0.25,
                "Indicators are mixed or extended; wait for a clearer setup");
    }
}
