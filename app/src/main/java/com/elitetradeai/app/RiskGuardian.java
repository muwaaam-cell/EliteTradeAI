package com.elitetradeai.app;

/** Hard safety gate for paper-trade proposals. Never submits broker orders. */
public final class RiskGuardian {
    public static final double DEFAULT_RISK_PERCENT = 0.5;
    public static final double HARD_MAX_RISK_PERCENT = 2.0;
    public static final double DEFAULT_DAILY_DRAWDOWN_LIMIT_PERCENT = 3.0;

    public static final class Decision {
        public final boolean approved;
        public final String reason;
        private Decision(boolean approved, String reason) { this.approved = approved; this.reason = reason; }
    }

    public Decision evaluate(boolean paperMode, boolean killSwitchOn, boolean marketDataValid,
                             boolean stopLossPresent, double riskPercent, int openPositions,
                             int openPositionsForSymbol, double dailyDrawdownPercent) {
        if (!paperMode) return new Decision(false, "Paper mode must remain enabled");
        if (killSwitchOn) return new Decision(false, "Kill switch is active");
        if (!marketDataValid) return new Decision(false, "Market data is missing or invalid");
        if (!stopLossPresent) return new Decision(false, "A stop-loss is mandatory");
        if (!Double.isFinite(riskPercent) || riskPercent <= 0 || riskPercent > HARD_MAX_RISK_PERCENT)
            return new Decision(false, "Risk must be above zero and no more than 2%");
        if (openPositions >= 3) return new Decision(false, "Maximum of 3 open paper positions reached");
        if (openPositionsForSymbol >= 1) return new Decision(false, "Only one open position per symbol is allowed");
        if (!Double.isFinite(dailyDrawdownPercent) || dailyDrawdownPercent >= DEFAULT_DAILY_DRAWDOWN_LIMIT_PERCENT)
            return new Decision(false, "Daily drawdown limit reached or invalid");
        return new Decision(true, "Approved for paper simulation only");
    }
}
