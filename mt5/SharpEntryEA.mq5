#property strict
#property version "1.00"
#property description "SharpEntry risk-controlled EMA/RSI/ATR Expert Advisor"
#include <Trade/Trade.mqh>
CTrade trade;

input bool InpEnableLiveTrading=false;
input ENUM_TIMEFRAMES InpTimeframe=PERIOD_M15;
input int InpFastEMA=20, InpSlowEMA=50, InpRSIPeriod=14, InpATRPeriod=14;
input double InpBuyRSIMin=52.0, InpSellRSIMax=48.0;
input double InpStopATRMultiple=2.0, InpRewardRisk=2.0;
input double InpRiskPercent=0.25, InpMaxRiskPercent=0.50, InpDailyLossLimitPercent=2.0;
input int InpMaxTradesPerDay=3, InpMaxSpreadPoints=40, InpSlippagePoints=20;
input ulong InpMagicNumber=28102026;
input bool InpOnePositionPerSymbol=true;

int hFast=INVALID_HANDLE,hSlow=INVALID_HANDLE,hRSI=INVALID_HANDLE,hATR=INVALID_HANDLE;
datetime lastBar=0;
int dayKey=-1,tradesToday=0;
double dayStartEquity=0.0;
bool dailyLock=false;

int MakeDayKey(datetime when){ MqlDateTime d; TimeToStruct(when,d); return d.year*10000+d.mon*100+d.day; }
void ResetDailyStateIfNeeded(){
 int today=MakeDayKey(TimeCurrent());
 if(today!=dayKey){ dayKey=today; dayStartEquity=AccountInfoDouble(ACCOUNT_EQUITY); tradesToday=0; dailyLock=false; Print("SharpEntry daily counters reset."); }
 if(dayStartEquity>0.0 && (dayStartEquity-AccountInfoDouble(ACCOUNT_EQUITY))/dayStartEquity*100.0>=InpDailyLossLimitPercent){ dailyLock=true; Print("SharpEntry DAILY LOCK: equity loss limit reached."); }
}
bool IsNewBar(){ datetime t[1]; if(CopyTime(_Symbol,InpTimeframe,0,1,t)!=1)return false; if(t[0]==lastBar)return false; lastBar=t[0]; return true; }
bool ReadValue(int h,int shift,double &v){ double a[1]; if(CopyBuffer(h,0,shift,1,a)!=1)return false; v=a[0]; return MathIsValidNumber(v); }
bool HasOurPosition(){
 for(int i=PositionsTotal()-1;i>=0;--i){ ulong ticket=PositionGetTicket(i); if(ticket==0)continue;
  if(PositionGetString(POSITION_SYMBOL)==_Symbol && (ulong)PositionGetInteger(POSITION_MAGIC)==InpMagicNumber)return true; }
 return false;
}
double NormalizeVolume(double v){
 double mn=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN),mx=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX),step=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
 if(step<=0||mn<=0)return 0; v=MathMin(v,mx); v=MathFloor(v/step)*step; if(v<mn)return 0;
 int digits=0; double s=step; while(digits<8 && MathAbs(s-MathRound(s))>1e-8){s*=10;digits++;} return NormalizeDouble(v,digits);
}
double LotsForRisk(double entry,double stop){
 double equity=AccountInfoDouble(ACCOUNT_EQUITY),pct=MathMax(0.0,MathMin(InpRiskPercent,InpMaxRiskPercent));
 double ts=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE),tv=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE_LOSS);
 if(tv<=0)tv=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
 if(equity<=0||pct<=0||ts<=0||tv<=0||MathAbs(entry-stop)<=0)return 0;
 double lossPerLot=(MathAbs(entry-stop)/ts)*tv; if(lossPerLot<=0)return 0;
 return NormalizeVolume((equity*pct/100.0)/lossPerLot);
}
int OnInit(){
 if(InpFastEMA<2||InpSlowEMA<=InpFastEMA||InpRiskPercent<=0||InpMaxRiskPercent<=0||InpDailyLossLimitPercent<=0||InpStopATRMultiple<=0||InpRewardRisk<=0)return INIT_PARAMETERS_INCORRECT;
 hFast=iMA(_Symbol,InpTimeframe,InpFastEMA,0,MODE_EMA,PRICE_CLOSE); hSlow=iMA(_Symbol,InpTimeframe,InpSlowEMA,0,MODE_EMA,PRICE_CLOSE);
 hRSI=iRSI(_Symbol,InpTimeframe,InpRSIPeriod,PRICE_CLOSE); hATR=iATR(_Symbol,InpTimeframe,InpATRPeriod);
 if(hFast==INVALID_HANDLE||hSlow==INVALID_HANDLE||hRSI==INVALID_HANDLE||hATR==INVALID_HANDLE)return INIT_FAILED;
 trade.SetExpertMagicNumber(InpMagicNumber); trade.SetDeviationInPoints(InpSlippagePoints); trade.SetTypeFillingBySymbol(_Symbol);
 ResetDailyStateIfNeeded(); Print("SharpEntry initialized on ",_Symbol,". Live execution enabled=",InpEnableLiveTrading);
 return INIT_SUCCEEDED;
}
void OnDeinit(const int reason){ if(hFast!=INVALID_HANDLE)IndicatorRelease(hFast); if(hSlow!=INVALID_HANDLE)IndicatorRelease(hSlow); if(hRSI!=INVALID_HANDLE)IndicatorRelease(hRSI); if(hATR!=INVALID_HANDLE)IndicatorRelease(hATR); }
void OnTick(){
 ResetDailyStateIfNeeded();
 if(!InpEnableLiveTrading||dailyLock||tradesToday>=InpMaxTradesPerDay)return;
 if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)||!MQLInfoInteger(MQL_TRADE_ALLOWED)||!AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))return;
 if(InpOnePositionPerSymbol&&HasOurPosition())return; if(!IsNewBar())return;
 double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK),bid=SymbolInfoDouble(_Symbol,SYMBOL_BID),point=SymbolInfoDouble(_Symbol,SYMBOL_POINT);
 if(ask<=0||bid<=0||point<=0)return; int spread=(int)MathRound((ask-bid)/point); if(spread>InpMaxSpreadPoints)return;
 double f1,f2,s1,s2,rsi,atr;
 if(!ReadValue(hFast,1,f1)||!ReadValue(hFast,2,f2)||!ReadValue(hSlow,1,s1)||!ReadValue(hSlow,2,s2)||!ReadValue(hRSI,1,rsi)||!ReadValue(hATR,1,atr)||atr<=0)return;
 bool buy=(f2<=s2&&f1>s1&&rsi>=InpBuyRSIMin),sell=(f2>=s2&&f1<s1&&rsi<=InpSellRSIMax); if(!buy&&!sell)return;
 int digits=(int)SymbolInfoInteger(_Symbol,SYMBOL_DIGITS); double minStop=(double)SymbolInfoInteger(_Symbol,SYMBOL_TRADE_STOPS_LEVEL)*point;
 if(buy){
  double entry=ask,sl=NormalizeDouble(entry-MathMax(atr*InpStopATRMultiple,minStop+point),digits),tp=NormalizeDouble(entry+(entry-sl)*InpRewardRisk,digits),lots=LotsForRisk(entry,sl);
  if(lots<=0){Print("SharpEntry: volume below broker minimum or tick values unavailable; skipped.");return;}
  if(trade.Buy(lots,_Symbol,0.0,sl,tp,"SharpEntry EMA RSI ATR BUY")){tradesToday++;Print("SharpEntry BUY opened. Lots=",lots," SL=",sl," TP=",tp);}
  else Print("BUY rejected: ",trade.ResultRetcode()," ",trade.ResultRetcodeDescription());
 }else{
  double entry=bid,sl=NormalizeDouble(entry+MathMax(atr*InpStopATRMultiple,minStop+point),digits),tp=NormalizeDouble(entry-(sl-entry)*InpRewardRisk,digits),lots=LotsForRisk(entry,sl);
  if(lots<=0){Print("SharpEntry: volume below broker minimum or tick values unavailable; skipped.");return;}
  if(trade.Sell(lots,_Symbol,0.0,sl,tp,"SharpEntry EMA RSI ATR SELL")){tradesToday++;Print("SharpEntry SELL opened. Lots=",lots," SL=",sl," TP=",tp);}
  else Print("SELL rejected: ",trade.ResultRetcode()," ",trade.ResultRetcodeDescription());
 }
}