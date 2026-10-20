package com.elitetradeai.app;

/** Immutable OHLCV candle. Timestamp is epoch milliseconds; prices must be real inputs from a declared source. */
public final class Candle {
    public final long timestamp;
    public final double open, high, low, close, volume;

    public Candle(long timestamp, double open, double high, double low, double close, double volume) {
        if (timestamp <= 0) throw new IllegalArgumentException("Candle timestamp must be positive");
        if (!positive(open) || !positive(high) || !positive(low) || !positive(close))
            throw new IllegalArgumentException("OHLC prices must be finite and positive");
        if (!Double.isFinite(volume) || volume < 0) throw new IllegalArgumentException("Volume must be finite and non-negative");
        if (high < Math.max(open, close) || low > Math.min(open, close) || high < low)
            throw new IllegalArgumentException("Invalid OHLC range");
        this.timestamp = timestamp;
        this.open = open; this.high = high; this.low = low; this.close = close; this.volume = volume;
    }

    private static boolean positive(double value) { return Double.isFinite(value) && value > 0; }
}
