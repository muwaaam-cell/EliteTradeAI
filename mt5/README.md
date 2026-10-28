# SharpEntry MT5 Expert Advisor

This directory contains an MQL5 Expert Advisor source intended to submit trades through a MetaTrader 5 terminal connected to Exness.

## Implemented in this first version
- EMA crossover (20/50 default) with RSI confirmation.
- ATR-based stop loss and reward/risk take profit.
- Position sizing based on equity, stop distance, tick size and tick value.
- Risk-per-trade cap, daily equity-loss lockout, maximum trades per day, one position per symbol, and spread guard.
- Uses the chart symbol, so attach to the exact broker symbol, including suffixes such as XAUUSDm.
- Live order submission is OFF by default. Enable explicitly using the EA input only after testing.

## Not yet verified
This source has NOT been compiled in MetaEditor or tested on an Exness demo account. It is not connected to the Android app or FastForex. It trades from quotes received by the MT5 terminal. Spread thresholds must be tuned per symbol. Broker contract settings, minimum lot, tick values, stop levels, commissions and execution results must be checked. The daily loss lockout blocks new entries but does not close existing positions. There is no news filter, remote phone kill switch, trailing stop, or portfolio-wide risk aggregation.

The standard MT5 Android app cannot run an EA. Automated execution needs an MT5 desktop terminal or another compatible always-on execution host. Do not enable live trading before compiling and testing on demo. No bot can guarantee profits.

## Install and test
1. In desktop MT5 choose File -> Open Data Folder -> MQL5 -> Experts.
2. Copy SharpEntryEA.mq5 there and open it in MetaEditor.
3. Compile and resolve any errors.
4. Attach to one symbol chart. Leave live execution disabled while testing in Strategy Tester and on an Exness demo account.
5. Verify order sizing, SL/TP, spread guard, daily lockout and trade logs.
6. Only after independent testing, explicitly enable the live execution input and ensure Algo Trading and account permissions are enabled.

Never commit broker passwords, investor passwords, account numbers, or API keys.
