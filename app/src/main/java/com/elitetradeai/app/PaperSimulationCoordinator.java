package com.elitetradeai.app;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Paper-only analysis coordinator. It reads explicitly simulated snapshots,
 * combines independent opinions and reports a proposal; it never places orders.
 */
public final class PaperSimulationCoordinator {
    private final SimulatedMarketEngine market = new SimulatedMarketEngine(20261020L);
    private final AnalysisAgent trendAgent = new AnalysisAgent();
    private final EliteIQ eliteIQ = new EliteIQ();
    private final Map<String, Deque<Double>> history = new LinkedHashMap<>();

    public static final class Result {
        public final String symbol;
        public final double simulatedMid;
        public final AgentSignal.Direction proposal;
        public final String explanation;
        Result(String symbol, double simulatedMid, AgentSignal.Direction proposal, String explanation) {
            this.symbol = symbol; this.simulatedMid = simulatedMid;
            this.proposal = proposal; this.explanation = explanation;
        }
    }

    public synchronized List<Result> scan(long timestampMillis) {
        Map<String, MarketSnapshot> snapshots = market.nextSnapshots(timestampMillis);
        List<Result> results = new ArrayList<>();
        for (MarketSnapshot snapshot : snapshots.values()) {
            double mid = (snapshot.bid + snapshot.ask) / 2.0;
            Deque<Double> prices = history.computeIfAbsent(snapshot.symbol, key -> new ArrayDeque<>());
            prices.addLast(mid);
            while (prices.size() > 20) prices.removeFirst();
            if (prices.size() < 4 || !snapshot.isFresh(timestampMillis, 5000L)
                    || snapshot.source != MarketSnapshot.Source.SIMULATED) {
                results.add(new Result(snapshot.symbol, mid, AgentSignal.Direction.NEUTRAL,
                        "WAIT: need fresh simulated history; no order was placed"));
                continue;
            }
            double fast = average(prices, Math.max(2, prices.size() / 3));
            double slow = average(prices, prices.size());
            double previous = previous(prices);
            double rsi = mid > previous ? 60.0 : mid < previous ? 40.0 : 50.0;
            AgentSignal trend = trendAgent.evaluate(snapshot.symbol, fast, slow, rsi);
            AgentSignal momentum;
            if (mid > previous && fast > slow) {
                momentum = new AgentSignal("MomentumConfirmation", snapshot.symbol,
                        AgentSignal.Direction.BUY, 0.60, "Simulated momentum confirms trend");
            } else if (mid < previous && fast < slow) {
                momentum = new AgentSignal("MomentumConfirmation", snapshot.symbol,
                        AgentSignal.Direction.SELL, 0.60, "Simulated momentum confirms trend");
            } else {
                momentum = new AgentSignal("MomentumConfirmation", snapshot.symbol,
                        AgentSignal.Direction.NEUTRAL, 0.25, "No confirmation");
            }
            EliteIQ.Decision decision = eliteIQ.combine(java.util.Arrays.asList(trend, momentum),
                    snapshot.symbol, 0.55);
            results.add(new Result(snapshot.symbol, mid, decision.direction,
                    decision.explanation + "; SIMULATED DATA ONLY; no order was placed"));
        }
        return results;
    }

    private double average(Deque<Double> values, int count) {
        int n = Math.min(values.size(), count), skip = values.size() - n, i = 0;
        double sum = 0.0;
        for (double value : values) if (i++ >= skip) sum += value;
        return sum / n;
    }

    private double previous(Deque<Double> values) {
        double result = 0.0;
        int target = values.size() - 2, i = 0;
        for (double value : values) if (i++ == target) result = value;
        return result;
    }
}
