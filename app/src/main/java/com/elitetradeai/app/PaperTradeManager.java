package com.elitetradeai.app;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Paper-only position manager. Feed it a price from the currently selected market source.
 * It closes simulated positions when their stop-loss or take-profit is touched.
 * It never sends broker orders and never treats simulated quotes as live data.
 */
public final class PaperTradeManager {
    public enum ExitReason { STOP_LOSS, TAKE_PROFIT }

    public static final class Exit {
        public final String tradeId;
        public final String symbol;
        public final double exitPrice;
        public final double pnl;
        public final ExitReason reason;

        private Exit(String tradeId, String symbol, double exitPrice, double pnl, ExitReason reason) {
            this.tradeId = tradeId;
            this.symbol = symbol;
            this.exitPrice = exitPrice;
            this.pnl = pnl;
            this.reason = reason;
        }
    }

    private final PaperPortfolio portfolio;
    private final List<Exit> exits = new ArrayList<>();

    public PaperTradeManager(PaperPortfolio portfolio) {
        if (portfolio == null) throw new IllegalArgumentException("Paper portfolio is required");
        this.portfolio = portfolio;
    }

    /**
     * Evaluates every open trade for this symbol using the supplied observed price.
     * A gap through a stop/target is conservatively filled at the observed price,
     * rather than pretending a better fill was available.
     */
    public synchronized List<Exit> onPrice(String symbol, double price) {
        if (symbol == null || symbol.trim().isEmpty()) throw new IllegalArgumentException("Symbol required");
        if (!Double.isFinite(price) || price <= 0) throw new IllegalArgumentException("Price must be finite and positive");

        List<Exit> newlyClosed = new ArrayList<>();
        for (PaperTrade trade : portfolio.getTrades()) {
            if (trade.status != PaperTrade.Status.OPEN || !trade.symbol.equals(symbol)) continue;

            ExitReason reason = null;
            if (trade.side == PaperTrade.Side.BUY) {
                if (price <= trade.stopLoss) reason = ExitReason.STOP_LOSS;
                else if (price >= trade.takeProfit) reason = ExitReason.TAKE_PROFIT;
            } else {
                if (price >= trade.stopLoss) reason = ExitReason.STOP_LOSS;
                else if (price <= trade.takeProfit) reason = ExitReason.TAKE_PROFIT;
            }

            if (reason != null) {
                double pnl = portfolio.close(trade.id, price);
                Exit exit = new Exit(trade.id, trade.symbol, price, pnl, reason);
                exits.add(exit);
                newlyClosed.add(exit);
            }
        }
        return Collections.unmodifiableList(newlyClosed);
    }

    public synchronized List<Exit> getExitHistory() {
        return Collections.unmodifiableList(new ArrayList<>(exits));
    }
}
