package com.elitetradeai.app;

import org.junit.Test;
import static org.junit.Assert.*;

public class PaperTradeManagerTest {
    @Test public void buyClosesAtTakeProfitAndUpdatesBalance() {
        PaperPortfolio portfolio = new PaperPortfolio(1000);
        PaperTrade trade = portfolio.open("XAUUSDm", PaperTrade.Side.BUY, 100.0, 95.0, 110.0,
                1.0, 1L, new RiskGuardian(), true, false, 0.0);
        PaperTradeManager manager = new PaperTradeManager(portfolio);

        assertTrue(manager.onPrice("XAUUSDm", 105.0).isEmpty());
        assertEquals(PaperTrade.Status.OPEN, trade.status);
        PaperTradeManager.Exit exit = manager.onPrice("XAUUSDm", 110.0).get(0);

        assertEquals(PaperTradeManager.ExitReason.TAKE_PROFIT, exit.reason);
        assertEquals(20.0, exit.pnl, 0.000001);
        assertEquals(1020.0, portfolio.getBalance(), 0.000001);
        assertEquals(PaperTrade.Status.CLOSED, trade.status);
    }

    @Test public void sellClosesAtStopLossAndUpdatesBalance() {
        PaperPortfolio portfolio = new PaperPortfolio(1000);
        PaperTrade trade = portfolio.open("BTCUSDm", PaperTrade.Side.SELL, 100.0, 105.0, 90.0,
                1.0, 1L, new RiskGuardian(), true, false, 0.0);
        PaperTradeManager manager = new PaperTradeManager(portfolio);

        PaperTradeManager.Exit exit = manager.onPrice("BTCUSDm", 106.0).get(0);

        assertEquals(PaperTradeManager.ExitReason.STOP_LOSS, exit.reason);
        // Risk is 1% of $1,000 = $10 at the $5 stop distance.
        // The observed exit is $6 beyond entry, so the conservative gap fill is -$12.
        assertEquals(-12.0, exit.pnl, 0.000001);
        assertEquals(988.0, portfolio.getBalance(), 0.000001);
        assertEquals(PaperTrade.Status.CLOSED, trade.status);
    }

    @Test public void ignoresOtherSymbolsAndRejectsInvalidPrices() {
        PaperPortfolio portfolio = new PaperPortfolio(1000);
        portfolio.open("XAUUSDm", PaperTrade.Side.BUY, 100.0, 95.0, 110.0,
                1.0, 1L, new RiskGuardian(), true, false, 0.0);
        PaperTradeManager manager = new PaperTradeManager(portfolio);

        assertTrue(manager.onPrice("BTCUSDm", 120.0).isEmpty());
        try {
            manager.onPrice("XAUUSDm", Double.NaN);
            fail("Expected invalid price to be rejected");
        } catch (IllegalArgumentException expected) {
            assertNotNull(expected.getMessage());
        }
    }
}
