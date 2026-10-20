package com.elitetradeai.app;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Random;

/** Deterministic paper-market generator. Never fetches or represents live broker quotes. */
public final class SimulatedMarketEngine {
    private final Random random;
    private final Map<String, Double> lastPrices = new LinkedHashMap<>();
    private long tick;

    public SimulatedMarketEngine(long seed) {
        random = new Random(seed);
        lastPrices.put("XAUUSDm", 2350.40);
        lastPrices.put("BTCUSDm", 62450.00);
        lastPrices.put("EURUSDm", 1.0842);
        lastPrices.put("GBPUSDm", 1.2715);
        lastPrices.put("USOILm", 78.35);
    }

    public synchronized Map<String, MarketSnapshot> nextSnapshots(long timestampMillis) {
        if (timestampMillis <= 0) throw new IllegalArgumentException("Timestamp must be positive");
        Map<String, MarketSnapshot> result = new LinkedHashMap<>();
        for (Map.Entry<String, Double> entry : lastPrices.entrySet()) {
            double old = entry.getValue();
            double scale = Math.max(old * 0.0008, entry.getKey().contains("EUR") || entry.getKey().contains("GBP") ? 0.00005 : 0.01);
            double mid = Math.max(0.00001, old + (random.nextDouble() - 0.5) * scale);
            double spread = entry.getKey().contains("EUR") || entry.getKey().contains("GBP") ? 0.0001 : Math.max(0.01, mid * 0.0001);
            lastPrices.put(entry.getKey(), mid);
            result.put(entry.getKey(), new MarketSnapshot(entry.getKey(), mid - spread / 2.0, mid + spread / 2.0,
                    timestampMillis, MarketSnapshot.Source.SIMULATED));
        }
        tick++;
        return result;
    }

    public synchronized long getTickCount() { return tick; }
    public synchronized double getLastMidPrice(String symbol) {
        Double value = lastPrices.get(symbol);
        if (value == null) throw new IllegalArgumentException("Unknown simulated symbol: " + symbol);
        return value;
    }
}
