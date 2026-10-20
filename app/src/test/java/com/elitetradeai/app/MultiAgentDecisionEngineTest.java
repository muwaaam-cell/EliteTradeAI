package com.elitetradeai.app;

import org.junit.Test;
import java.util.ArrayList;
import java.util.List;
import static org.junit.Assert.*;

public class MultiAgentDecisionEngineTest {
    private List<Candle> candles(boolean rising) {
        List<Candle> result = new ArrayList<>();
        for (int i = 0; i < 60; i++) {
            double close = rising ? 100 + i * 0.5 : 130 - i * 0.5;
            result.add(new Candle(1000L + i * 60000L, close - 0.1, close + 0.3,
                    close - 0.3, close, 10));
        }
        return result;
    }

    @Test public void requiresEnoughCandles() {
        try {
            new MultiAgentDecisionEngine().analyse(candles(true).subList(0, 20));
            fail("Expected insufficient history to be rejected");
        } catch (IllegalArgumentException expected) {
            assertTrue(expected.getMessage().contains("51"));
        }
    }

    @Test public void returnsExplainableVotesFromCompletedCandles() {
        MultiAgentDecisionEngine.Analysis result =
                new MultiAgentDecisionEngine().analyse(candles(true));
        assertEquals(3, result.votes.size());
        assertNotNull(result.explanation);
        assertTrue(result.emaFast > 0);
        assertTrue(result.emaSlow > 0);
        assertTrue(result.rsi >= 0 && result.rsi <= 100);
        assertTrue(result.atr > 0);
    }

    @Test public void rejectsInvalidCandleRange() {
        try {
            new Candle(1000, 100, 99, 90, 101, 1);
            fail("Expected invalid OHLC range");
        } catch (IllegalArgumentException expected) {
            assertNotNull(expected.getMessage());
        }
    }
}
