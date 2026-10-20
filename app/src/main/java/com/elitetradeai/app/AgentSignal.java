package com.elitetradeai.app;

/** A strategy agent's opinion. Signals are informational until approved by RiskGuardian. */
public final class AgentSignal {
    public enum Direction { BUY, SELL, NEUTRAL }
    public final String agent;
    public final String symbol;
    public final Direction direction;
    public final double confidence;
    public final String reason;

    public AgentSignal(String agent, String symbol, Direction direction, double confidence, String reason) {
        if (agent == null || symbol == null || direction == null || reason == null) throw new IllegalArgumentException("Signal fields cannot be null");
        if (!Double.isFinite(confidence) || confidence < 0.0 || confidence > 1.0) throw new IllegalArgumentException("Confidence must be between 0 and 1");
        this.agent = agent; this.symbol = symbol; this.direction = direction; this.confidence = confidence; this.reason = reason;
    }
}
