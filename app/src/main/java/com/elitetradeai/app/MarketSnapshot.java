package com.elitetradeai.app;

/** Market observation with explicit provenance; simulated values can never masquerade as live quotes. */
public final class MarketSnapshot {
    public enum Source { SIMULATED, PUBLIC_FEED, BROKER }
    public final String symbol;
    public final double bid;
    public final double ask;
    public final long timestampMillis;
    public final Source source;

    public MarketSnapshot(String symbol, double bid, double ask, long timestampMillis, Source source) {
        if (symbol == null || symbol.trim().isEmpty() || source == null) throw new IllegalArgumentException("Symbol and source are required");
        if (!Double.isFinite(bid) || !Double.isFinite(ask) || bid <= 0 || ask < bid) throw new IllegalArgumentException("Invalid bid/ask prices");
        if (timestampMillis <= 0) throw new IllegalArgumentException("Timestamp must be positive");
        this.symbol = symbol; this.bid = bid; this.ask = ask; this.timestampMillis = timestampMillis; this.source = source;
    }

    public boolean isFresh(long nowMillis, long maxAgeMillis) {
        return nowMillis >= timestampMillis && maxAgeMillis >= 0 && nowMillis - timestampMillis <= maxAgeMillis;
    }
}
