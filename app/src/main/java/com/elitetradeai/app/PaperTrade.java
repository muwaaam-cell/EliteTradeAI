package com.elitetradeai.app;

/** Local simulated position record. It has no broker or network execution capability. */
public final class PaperTrade {
    public enum Side { BUY, SELL }
    public enum Status { OPEN, CLOSED }
    public final String id;
    public final String symbol;
    public final Side side;
    public final double entryPrice;
    public final double stopLoss;
    public final double takeProfit;
    public final double riskPercent;
    public final long openedAtMillis;
    public Status status = Status.OPEN;

    public PaperTrade(String id, String symbol, Side side, double entryPrice, double stopLoss,
                      double takeProfit, double riskPercent, long openedAtMillis) {
        if (id == null || symbol == null || side == null || id.trim().isEmpty() || symbol.trim().isEmpty()) throw new IllegalArgumentException("Trade identity required");
        if (!Double.isFinite(entryPrice) || entryPrice <= 0 || !Double.isFinite(stopLoss) || !Double.isFinite(takeProfit)) throw new IllegalArgumentException("Prices must be finite and entry must be positive");
        if (side == Side.BUY && !(stopLoss < entryPrice && takeProfit > entryPrice)) throw new IllegalArgumentException("BUY requires stop-loss below entry and take-profit above entry");
        if (side == Side.SELL && !(stopLoss > entryPrice && takeProfit < entryPrice)) throw new IllegalArgumentException("SELL requires stop-loss above entry and take-profit below entry");
        if (!Double.isFinite(riskPercent) || riskPercent <= 0 || riskPercent > RiskGuardian.HARD_MAX_RISK_PERCENT) throw new IllegalArgumentException("Risk exceeds hard limit");
        this.id=id; this.symbol=symbol; this.side=side; this.entryPrice=entryPrice; this.stopLoss=stopLoss; this.takeProfit=takeProfit; this.riskPercent=riskPercent; this.openedAtMillis=openedAtMillis;
    }
}
