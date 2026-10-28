package com.elitetradeai.app;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.UUID;

/** In-memory simulated portfolio. No broker calls or real-money balances. */
public final class PaperPortfolio {
    private final double startingBalance;
    private double balance;
    private final List<PaperTrade> trades = new ArrayList<>();

    public PaperPortfolio(double startingBalance) {
        if (!Double.isFinite(startingBalance) || startingBalance <= 0) throw new IllegalArgumentException("Starting balance must be positive");
        this.startingBalance = startingBalance;
        this.balance = startingBalance;
    }

    public synchronized PaperTrade open(String symbol, PaperTrade.Side side, double entry, double stopLoss,
                                        double takeProfit, double riskPercent, long timestampMillis,
                                        RiskGuardian guardian, boolean marketDataValid, boolean killSwitchOn,
                                        double dailyDrawdownPercent) {
        if (guardian == null) throw new IllegalArgumentException("Risk Guardian is required");
        int open = 0, sameSymbol = 0;
        for (PaperTrade trade : trades) if (trade.status == PaperTrade.Status.OPEN) {
            open++;
            if (trade.symbol.equals(symbol)) sameSymbol++;
        }
        RiskGuardian.Decision decision = guardian.evaluate(true, killSwitchOn, marketDataValid, true,
                riskPercent, open, sameSymbol, dailyDrawdownPercent);
        if (!decision.approved) throw new IllegalStateException(decision.reason);
        PaperTrade trade = new PaperTrade(UUID.randomUUID().toString(), symbol, side, entry, stopLoss,
                takeProfit, riskPercent, timestampMillis);
        trades.add(trade);
        return trade;
    }

    /** Closes a simulated position at a supplied simulated exit price. */
    public synchronized double close(String tradeId, double exitPrice) {
        if (!Double.isFinite(exitPrice) || exitPrice <= 0) throw new IllegalArgumentException("Exit price must be positive");
        for (PaperTrade trade : trades) {
            if (trade.id.equals(tradeId) && trade.status == PaperTrade.Status.OPEN) {
                double signedMove = trade.side == PaperTrade.Side.BUY ? exitPrice - trade.entryPrice : trade.entryPrice - exitPrice;
                double riskDistance = Math.abs(trade.entryPrice - trade.stopLoss);
                double estimatedRiskCash = balance * (trade.riskPercent / 100.0);
                double pnl = (signedMove / riskDistance) * estimatedRiskCash;
                balance += pnl;
                trade.status = PaperTrade.Status.CLOSED;
                return pnl;
            }
        }
        throw new IllegalArgumentException("Open paper trade not found");
    }

    public synchronized double getBalance() { return balance; }
    public double getStartingBalance() { return startingBalance; }
    public synchronized int getOpenCount() {
        int count = 0;
        for (PaperTrade trade : trades) if (trade.status == PaperTrade.Status.OPEN) count++;
        return count;
    }
    public synchronized List<PaperTrade> getTrades() { return Collections.unmodifiableList(new ArrayList<>(trades)); }
}
