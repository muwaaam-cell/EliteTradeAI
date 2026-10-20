package com.elitetradeai.app;

import java.util.List;

/**
 * Decision coordinator for agent opinions. It proposes a direction only;
 * it cannot approve risk or execute orders.
 */
public final class EliteIQ {
    public static final class Decision {
        public final AgentSignal.Direction direction;
        public final double confidence;
        public final String explanation;
        private Decision(AgentSignal.Direction direction, double confidence, String explanation) {
            this.direction = direction;
            this.confidence = confidence;
            this.explanation = explanation;
        }
    }

    public Decision combine(List<AgentSignal> signals, String symbol, double minimumConfidence) {
        if (signals == null || signals.isEmpty() || symbol == null || symbol.trim().isEmpty()
                || !Double.isFinite(minimumConfidence) || minimumConfidence < 0 || minimumConfidence > 1) {
            return waitDecision("Missing or invalid agent inputs");
        }
        double buy = 0.0, sell = 0.0;
        int matching = 0;
        for (AgentSignal signal : signals) {
            if (signal == null || !symbol.equals(signal.symbol)) continue;
            matching++;
            if (signal.direction == AgentSignal.Direction.BUY) buy += signal.confidence;
            else if (signal.direction == AgentSignal.Direction.SELL) sell += signal.confidence;
        }
        if (matching == 0) return waitDecision("No agent reports for " + symbol);
        if (Math.abs(buy - sell) < 0.15) return waitDecision("Agents disagree or provide insufficient consensus");
        double score = Math.max(buy, sell) / matching;
        if (score < minimumConfidence) return waitDecision("Consensus confidence below configured threshold");
        AgentSignal.Direction direction = buy > sell ? AgentSignal.Direction.BUY : AgentSignal.Direction.SELL;
        return new Decision(direction, Math.min(1.0, score),
                "Agent consensus suggests " + direction + "; Risk Guardian approval is still required");
    }

    private Decision waitDecision(String explanation) {
        return new Decision(AgentSignal.Direction.NEUTRAL, 0.0, explanation);
    }
}
