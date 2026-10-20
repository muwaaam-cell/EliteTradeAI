package com.elitetradeai.app;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Explainable multi-agent decision engine. Agents analyse supplied candle data only.
 * This class creates research proposals; it does not place orders.
 */
public final class MultiAgentDecisionEngine {
    public enum Direction { BUY, SELL, WAIT }

    public static final class AgentVote {
        public final String agent;
        public final Direction vote;
        public final String reason;
        private AgentVote(String agent, Direction vote, String reason) {
            this.agent = agent; this.vote = vote; this.reason = reason;
        }
    }

    public static final class Analysis {
        public final Direction decision;
        public final int agreementPercent;
        public final double emaFast, emaSlow, rsi, atr;
        public final List<AgentVote> votes;
        public final String explanation;

        private Analysis(Direction decision, int agreementPercent, double emaFast, double emaSlow,
                         double rsi, double atr, List<AgentVote> votes, String explanation) {
            this.decision = decision; this.agreementPercent = agreementPercent;
            this.emaFast = emaFast; this.emaSlow = emaSlow; this.rsi = rsi; this.atr = atr;
            this.votes = Collections.unmodifiableList(new ArrayList<>(votes));
            this.explanation = explanation;
        }
    }

    public Analysis analyse(List<Candle> completedCandles) {
        if (completedCandles == null || completedCandles.size() < 51)
            throw new IllegalArgumentException("At least 51 completed candles are required");
        double fast = TechnicalIndicators.ema(completedCandles, 20);
        double slow = TechnicalIndicators.ema(completedCandles, 50);
        double rsi = TechnicalIndicators.rsi(completedCandles, 14);
        double atr = TechnicalIndicators.atr(completedCandles, 14);
        double close = completedCandles.get(completedCandles.size() - 1).close;

        List<AgentVote> votes = new ArrayList<>();
        Direction trend = fast > slow ? Direction.BUY : fast < slow ? Direction.SELL : Direction.WAIT;
        votes.add(new AgentVote("Trend Agent", trend,
                trend == Direction.BUY ? "EMA 20 is above EMA 50" :
                trend == Direction.SELL ? "EMA 20 is below EMA 50" : "EMAs are equal; no trend edge"));

        Direction momentum = rsi >= 52 && rsi < 70 ? Direction.BUY :
                rsi <= 48 && rsi > 30 ? Direction.SELL : Direction.WAIT;
        votes.add(new AgentVote("Momentum Agent", momentum,
                momentum == Direction.BUY ? "RSI supports bullish momentum without an extreme reading" :
                momentum == Direction.SELL ? "RSI supports bearish momentum without an extreme reading" :
                "RSI is neutral or at an extreme; momentum agent abstains"));

        double atrPercent = atr / close * 100.0;
        Direction volatility = atrPercent > 0 && atrPercent <= 3.0 ? trend : Direction.WAIT;
        votes.add(new AgentVote("Volatility Agent", volatility,
                volatility == Direction.WAIT ? "Volatility is zero, unusually high, or trend is unclear" :
                "ATR is within the configured research range"));

        int buy = 0, sell = 0;
        for (AgentVote vote : votes) {
            if (vote.vote == Direction.BUY) buy++;
            if (vote.vote == Direction.SELL) sell++;
        }
        Direction decision = buy >= 2 ? Direction.BUY : sell >= 2 ? Direction.SELL : Direction.WAIT;
        int agreement = Math.max(buy, sell) * 100 / votes.size();
        String why = decision == Direction.WAIT
                ? "No directional majority. Research result only; wait for better alignment."
                : "Directional majority from " + Math.max(buy, sell) + " of " + votes.size()
                  + " agents. This is not an order and has not passed the Risk Guardian.";
        return new Analysis(decision, agreement, fast, slow, rsi, atr, votes, why);
    }
}
