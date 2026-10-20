package com.elitetradeai.app;

import java.util.List;

/** Deterministic technical indicators calculated from supplied completed candles. */
public final class TechnicalIndicators {
    private TechnicalIndicators() {}

    public static double ema(List<Candle> candles, int period) {
        require(candles, period);
        double alpha = 2.0 / (period + 1.0);
        double value = candles.get(0).close;
        for (int i = 1; i < candles.size(); i++) value = alpha * candles.get(i).close + (1.0 - alpha) * value;
        return value;
    }

    public static double rsi(List<Candle> candles, int period) {
        require(candles, period + 1);
        double gains = 0, losses = 0;
        int start = candles.size() - period;
        for (int i = start; i < candles.size(); i++) {
            double delta = candles.get(i).close - candles.get(i - 1).close;
            if (delta > 0) gains += delta; else losses -= delta;
        }
        if (losses == 0) return gains == 0 ? 50.0 : 100.0;
        double rs = (gains / period) / (losses / period);
        return 100.0 - (100.0 / (1.0 + rs));
    }

    public static double atr(List<Candle> candles, int period) {
        require(candles, period + 1);
        double sum = 0;
        int start = candles.size() - period;
        for (int i = start; i < candles.size(); i++) {
            Candle current = candles.get(i), previous = candles.get(i - 1);
            double trueRange = Math.max(current.high - current.low,
                    Math.max(Math.abs(current.high - previous.close), Math.abs(current.low - previous.close)));
            sum += trueRange;
        }
        return sum / period;
    }

    private static void require(List<Candle> candles, int count) {
        if (candles == null || candles.size() < count) throw new IllegalArgumentException("Not enough completed candles");
        long previous = 0;
        for (Candle c : candles) {
            if (c == null || c.timestamp <= previous) throw new IllegalArgumentException("Candles must be non-null and chronological");
            previous = c.timestamp;
        }
    }
}
