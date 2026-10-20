package com.elitetradeai.app;

import org.junit.Test;
import static org.junit.Assert.*;

public class RiskAndSignalTest {
    @Test public void guardianApprovesOnlySafePaperProposal() {
        RiskGuardian guardian = new RiskGuardian();
        RiskGuardian.Decision ok = guardian.evaluate(true, false, true, true, 0.5, 0, 0, 0.0);
        assertTrue(ok.approved);
        assertFalse(guardian.evaluate(false, false, true, true, 0.5, 0, 0, 0.0).approved);
        assertFalse(guardian.evaluate(true, true, true, true, 0.5, 0, 0, 0.0).approved);
        assertFalse(guardian.evaluate(true, false, false, true, 0.5, 0, 0, 0.0).approved);
        assertFalse(guardian.evaluate(true, false, true, false, 0.5, 0, 0, 0.0).approved);
    }

    @Test public void guardianEnforcesRiskAndExposureCaps() {
        RiskGuardian guardian = new RiskGuardian();
        assertFalse(guardian.evaluate(true, false, true, true, 2.01, 0, 0, 0.0).approved);
        assertFalse(guardian.evaluate(true, false, true, true, 0.5, 3, 0, 0.0).approved);
        assertFalse(guardian.evaluate(true, false, true, true, 0.5, 1, 1, 0.0).approved);
        assertFalse(guardian.evaluate(true, false, true, true, 0.5, 0, 0, 3.0).approved);
        assertFalse(guardian.evaluate(true, false, true, true, Double.NaN, 0, 0, 0.0).approved);
    }

    @Test public void snapshotRejectsInvalidAndIdentifiesSimulatedSource() {
        MarketSnapshot snapshot = new MarketSnapshot("XAUUSDm", 2350.0, 2350.2, 1000L, MarketSnapshot.Source.SIMULATED);
        assertEquals(MarketSnapshot.Source.SIMULATED, snapshot.source);
        assertTrue(snapshot.isFresh(1500L, 500L));
        assertFalse(snapshot.isFresh(2000L, 500L));
        try {
            new MarketSnapshot("XAUUSDm", 2351.0, 2350.0, 1000L, MarketSnapshot.Source.SIMULATED);
            fail("Crossed bid/ask must be rejected");
        } catch (IllegalArgumentException expected) { }
    }

    @Test public void analysisAgentWaitsOnMixedSignals() {
        AnalysisAgent agent = new AnalysisAgent();
        assertEquals(AgentSignal.Direction.BUY, agent.evaluate("XAUUSDm", 101, 100, 60).direction);
        assertEquals(AgentSignal.Direction.SELL, agent.evaluate("XAUUSDm", 99, 100, 40).direction);
        assertEquals(AgentSignal.Direction.NEUTRAL, agent.evaluate("XAUUSDm", 101, 100, 75).direction);
        assertEquals(AgentSignal.Direction.NEUTRAL, agent.evaluate("XAUUSDm", 101, 100, Double.NaN).direction);
    }
}
