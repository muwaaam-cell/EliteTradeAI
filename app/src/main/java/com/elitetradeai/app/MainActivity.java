package com.elitetradeai.app;
import android.app.*;import android.os.*;import android.speech.tts.TextToSpeech;import android.graphics.Color;import android.graphics.Typeface;import android.graphics.drawable.GradientDrawable;import android.view.*;import android.widget.*;import java.util.*;import java.text.*;
public class MainActivity extends Activity {
 int bg=Color.rgb(10,16,26),panel=Color.rgb(19,28,42),panel2=Color.rgb(27,39,56),white=Color.rgb(238,244,250),muted=Color.rgb(145,162,181),green=Color.rgb(69,222,177),red=Color.rgb(255,105,120);
 String tab="Market",symbol="XAUUSDm"; double balance=10000; ArrayList<String> positions=new ArrayList<>(); LinearLayout root,body; DecimalFormat df=new DecimalFormat("#,##0.00"); PaperSimulationCoordinator paperCoordinator=new PaperSimulationCoordinator(); TextToSpeech speech; boolean voiceEnabled=true; boolean speechReady=false; ArrayList<String> latestScan=new ArrayList<>(); int scanCount=0; ArrayList<String> latestLiveScan=new ArrayList<>(); String liveScanStatus="Not scanned yet"; boolean liveScanning=false; long lastLiveScanAt=0;
 public void onCreate(Bundle b){super.onCreate(b);getWindow().setStatusBarColor(bg);getWindow().setNavigationBarColor(bg);voiceEnabled=getSharedPreferences("elite_voice",MODE_PRIVATE).getBoolean("enabled",true);speech=new TextToSpeech(this,status->{if(status==TextToSpeech.SUCCESS){int result=speech.setLanguage(Locale.US);speechReady=result!=TextToSpeech.LANG_MISSING_DATA&&result!=TextToSpeech.LANG_NOT_SUPPORTED;}});loadCachedLiveScan();render();}
 @Override public void onDestroy(){if(speech!=null){speech.stop();speech.shutdown();}super.onDestroy();}
 void speak(String message){if(!voiceEnabled||!speechReady||speech==null||message==null||message.trim().isEmpty())return;speech.speak(message,TextToSpeech.QUEUE_FLUSH,null,"elite_signal_voice");}
 void speakLatestScan(){if(latestLiveScan.isEmpty()){Toast.makeText(this,"Run a fresh scan first.",Toast.LENGTH_SHORT).show();return;}StringBuilder message=new StringBuilder("EliteTrade AI market report. ");for(String line:latestLiveScan){String[] parts=line.split("\\\\n");String first=parts.length>0?parts[0]:line;message.append(first.replace("|"," signal ")).append(". ");if(line.contains("Data error"))message.append("Data error. No signal available. ");else if(line.contains("Illustrative SL:"))message.append("The entry, stop loss and take profit levels are illustrative and do not include broker spread or slippage. ");}message.append("Research only. This is not a guarantee of profit.");if(speechReady)speech.speak(message.toString(),TextToSpeech.QUEUE_FLUSH,null,"elite_signal_voice");else Toast.makeText(this,"Phone text-to-speech is not ready. Check Android text-to-speech settings.",Toast.LENGTH_LONG).show();}
 void loadCachedLiveScan(){android.content.SharedPreferences p=getSharedPreferences("elite_signal_cache",MODE_PRIVATE);int count=p.getInt("count",0);lastLiveScanAt=p.getLong("saved_at",0);for(int i=0;i<count&&i<2;i++){String value=p.getString("result_"+i,null);if(value!=null&&!value.isEmpty())latestLiveScan.add(value);}if(!latestLiveScan.isEmpty()){liveScanStatus="Offline-capable · showing last saved scan";}}
 void saveCachedLiveScan(){android.content.SharedPreferences p=getSharedPreferences("elite_signal_cache",MODE_PRIVATE);android.content.SharedPreferences.Editor e=p.edit();e.putInt("count",Math.min(2,latestLiveScan.size()));e.putLong("saved_at",lastLiveScanAt);for(int i=0;i<latestLiveScan.size()&&i<2;i++)e.putString("result_"+i,latestLiveScan.get(i));e.apply();}
 GradientDrawable shape(int c,int r){GradientDrawable d=new GradientDrawable();d.setColor(c);d.setCornerRadius(r);return d;}
 TextView tx(String s,int z,int c,boolean bold){TextView t=new TextView(this);t.setText(s);t.setTextSize(z);t.setTextColor(c);if(bold)t.setTypeface(null,Typeface.BOLD);t.setGravity(Gravity.CENTER_VERTICAL);return t;}
 LinearLayout col(){LinearLayout l=new LinearLayout(this);l.setOrientation(1);return l;} LinearLayout row(){LinearLayout l=new LinearLayout(this);l.setOrientation(0);l.setGravity(Gravity.CENTER_VERTICAL);return l;}
 void pad(View v,int a,int b,int c,int d){v.setPadding(a,b,c,d);} void gap(LinearLayout l,int h){View v=new View(this);l.addView(v,new LinearLayout.LayoutParams(1,h));}
 void card(LinearLayout p,View v){v.setBackground(shape(panel,22));pad(v,15,14,15,14);LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.bottomMargin=11;p.addView(v,lp);}
 TextView btn(String s,int c){TextView t=tx(s,13,c,true);t.setGravity(17);pad(t,10,12,10,12);t.setBackground(shape(panel2,15));return t;}
 void render(){root=col();root.setBackgroundColor(bg);setContentView(root);LinearLayout head=col();pad(head,17,14,17,8);LinearLayout rr=row();LinearLayout title=col();title.addView(tx("ELITETRADE AI",20,white,true));title.addView(tx("MULTI-AGENT MARKET INTELLIGENCE",10,green,true));rr.addView(title,new LinearLayout.LayoutParams(0,-2,1));TextView bd=tx("● DEMO",11,green,true);bd.setBackground(shape(Color.rgb(20,56,52),18));pad(bd,10,7,10,7);rr.addView(bd);head.addView(rr);TextView notice=tx("SIGNAL-ONLY  ·  PUBLIC FEED LIMITED  ·  NO BROKER EXECUTION",10,muted,true);pad(notice,10,10,10,10);notice.setBackground(shape(panel,12));LinearLayout.LayoutParams n=new LinearLayout.LayoutParams(-1,-2);n.topMargin=10;head.addView(notice,n);root.addView(head);
 ScrollView sc=new ScrollView(this);body=col();pad(body,15,7,15,18);sc.addView(body);root.addView(sc,new LinearLayout.LayoutParams(-1,0,1));LinearLayout nav=row();nav.setBackgroundColor(panel);for(String s:new String[]{"Market","Chart","AI Chat","LAB","Trade","Account"}){TextView b=tx(s,11,tab.equals(s)?green:muted,true);b.setGravity(17);pad(b,1,17,1,17);nav.addView(b,new LinearLayout.LayoutParams(0,-2,1));b.setOnClickListener(v->{tab=s;render();});}root.addView(nav);
 if(tab.equals("Market"))market();else if(tab.equals("Chart"))chart();else if(tab.equals("AI Chat"))aiChat();else if(tab.equals("LAB"))lab();else if(tab.equals("Trade"))trade();else account();}
 void market(){LinearLayout h=col();h.addView(tx("EXNESS ACCOUNT STATUS",12,muted,true));h.addView(tx("NOT CONNECTED",26,red,true));h.addView(tx("No account balance is available yet. Connect and verify a genuine broker data route before showing account figures.",12,white,false));card(body,h);body.addView(tx("MARKET WATCH",12,muted,true));gap(body,8);
 String[] sy={"XAUUSD","BTCUSD"};String[] pr={"—","—"};for(int i=0;i<sy.length;i++){final String s=sy[i];LinearLayout r=row();LinearLayout l=col();l.addView(tx(s,16,white,true));l.addView(tx("No verified live quote loaded · tap LAB to scan public feed",10,muted,false));r.addView(l,new LinearLayout.LayoutParams(0,-2,1));TextView p=tx(pr[i],15,white,true);p.setGravity(Gravity.RIGHT);r.addView(p);pad(r,0,12,0,12);body.addView(r);View line=new View(this);line.setBackgroundColor(panel2);body.addView(line,new LinearLayout.LayoutParams(-1,1));r.setOnClickListener(v->{symbol=s;tab="Chart";render();});}gap(body,12);body.addView(tx("No fake prices are displayed here. Public-feed scan results are not Exness quotes and cannot execute trades.",12,muted,false));}
 void chart(){body.addView(tx(symbol,24,white,true));body.addView(tx("LIVE CHART STATUS: NOT CONNECTED",12,red,true));gap(body,14);LinearLayout c=col();c.setBackground(shape(panel,18));pad(c,12,15,12,15);c.addView(tx("No chart is drawn until real candle data is loaded. Use LAB to fetch the available public-feed scan.",12,muted,false));body.addView(c);gap(body,12);LinearLayout t=row();for(String s:new String[]{"M1","M5","M15","H1","H4","D1"})t.addView(btn(s,white),new LinearLayout.LayoutParams(0,-2,1));body.addView(t);gap(body,12);LinearLayout i=col();i.addView(tx("INDICATORS",11,muted,true));i.addView(tx("EMA 20 / EMA 50 · RSI 14 · ATR 14",14,white,false));i.addView(tx("Indicators unavailable until verified candles are loaded.",12,muted,false));card(body,i);}

 void aiChat(){
  body.addView(tx("ELITE AI · LIVE CHAT",22,white,true));
  body.addView(tx("AI MODEL STATUS: NOT CONNECTED",12,red,true));
  gap(body,8);
  LinearLayout intro=col();intro.setBackground(shape(panel,18));pad(intro,14,14,14,14);
  intro.addView(tx("Your trading copilot",16,white,true));gap(intro,6);
  intro.addView(tx("This chat interface is being prepared, but a real language-model connection has not been configured. I will not pretend a scripted reply is real AI. No paid API or server is connected.",13,muted,false));
  card(body,intro);
  LinearLayout capabilities=col();capabilities.setBackground(shape(panel,18));pad(capabilities,14,14,14,14);
  capabilities.addView(tx("WHAT MUST CONNECT BEFORE CHAT IS REAL",12,green,true));gap(capabilities,7);
  capabilities.addView(tx("• A real AI model endpoint or compatible local model\n• Internet access if the model is online\n• Fresh market data supplied with timestamps\n• Clear separation between AI analysis and broker execution",13,white,false));
  card(body,capabilities);
  EditText question=new EditText(this);question.setSingleLine(false);question.setMinLines(2);question.setTextColor(white);question.setHintTextColor(muted);question.setHint("Ask about a setup, risk, or market…");question.setBackground(shape(panel2,14));pad(question,12,10,12,10);
  body.addView(question,new LinearLayout.LayoutParams(-1,-2));gap(body,8);
  TextView send=btn("CHECK AI CONNECTION",green);body.addView(send);
  send.setOnClickListener(v->{String q=question.getText().toString().trim();if(q.isEmpty()){Toast.makeText(this,"Type a question first.",Toast.LENGTH_SHORT).show();return;}new AlertDialog.Builder(this).setTitle("AI is not connected yet").setMessage("Your question was not sent to an AI model. To provide real conversational answers without misleading you, we must connect an actual model. No question or account data has been transmitted.").setPositiveButton("OK",null).show();});
  gap(body,12);
  body.addView(tx("Next: choose a genuinely free local model or an available online model. We will not add a subscription flow or claim this is live AI until verified.",12,muted,false));
 }
 void analysis(){LinearLayout a=col();a.addView(tx("RULE-BASED DEMO ANALYSIS",12,muted,true));gap(a,8);a.addView(tx("Status: WAIT",24,green,true));a.addView(tx("Trend: not verified\nRSI: illustrative neutral reading\nVolatility: simulated estimate\nNo live market feed is connected.",14,white,false));card(body,a);LinearLayout b=col();b.addView(tx("WHY WAIT?",12,muted,true));gap(b,6);b.addView(tx("Simulated or conflicting inputs should not trigger real orders.",14,white,false));card(body,b);body.addView(tx("Educational demo only · Not financial advice.",12,muted,false));}
 EditText field(String value){EditText e=new EditText(this);e.setSingleLine(true);e.setText(value);e.setTextColor(white);e.setInputType(8194);e.setBackground(shape(panel2,12));pad(e,10,5,10,5);return e;}
 void input(String label,EditText e){body.addView(tx(label,10,muted,true));body.addView(e,new LinearLayout.LayoutParams(-1,-2));gap(body,7);}
 void trade(){body.addView(tx("PAPER TRADE TICKET",20,white,true));body.addView(tx("Local demo only. No broker orders are sent.",12,muted,false));gap(body,12);EditText entry=field("2350.40"),sl=field("2345.00"),tp=field("2360.00"),lot=field("0.01");input("ENTRY PRICE",entry);input("STOP LOSS",sl);input("TAKE PROFIT",tp);input("LOT SIZE (MAX 0.10)",lot);LinearLayout sides=row();TextView buy=btn("PAPER BUY",green),sell=btn("PAPER SELL",red);sides.addView(buy,new LinearLayout.LayoutParams(0,-2,1));sides.addView(sell,new LinearLayout.LayoutParams(0,-2,1));body.addView(sides);buy.setOnClickListener(v->order("BUY",entry,sl,tp,lot));sell.setOnClickListener(v->order("SELL",entry,sl,tp,lot));gap(body,15);body.addView(tx("OPEN PAPER POSITIONS",12,muted,true));if(positions.isEmpty())body.addView(tx("No open demo positions.",13,muted,false));for(String p:new ArrayList<>(positions)){LinearLayout c=col();c.addView(tx(p,14,white,true));TextView close=btn("Close paper position",white);close.setOnClickListener(v->{positions.remove(p);render();});c.addView(close);card(body,c);}}
 void order(String side,EditText e,EditText s,EditText t,EditText l){try{double en=Double.parseDouble(e.getText().toString()),st=Double.parseDouble(s.getText().toString()),ta=Double.parseDouble(t.getText().toString()),lo=Double.parseDouble(l.getText().toString());if(en<=0||st<=0||ta<=0||lo<=0||lo>.1||(side.equals("BUY")&&(st>=en||ta<=en))||(side.equals("SELL")&&(st<=en||ta>=en))||Math.abs(en-st)*lo*100>balance*.02){Toast.makeText(this,"Invalid values or blocked by 2% demo risk limit.",Toast.LENGTH_LONG).show();return;}String p=side+" "+symbol+" · "+lo+" lots · entry "+en+" · SL "+st+" · TP "+ta;positions.add(p);Toast.makeText(this,"Paper order saved. No real order sent.",Toast.LENGTH_LONG).show();render();}catch(Exception ex){Toast.makeText(this,"Enter valid numeric values.",Toast.LENGTH_LONG).show();}}
 void lab(){
  body.addView(tx("ELITE INTELLIGENCE COMMAND",20,white,true));
  body.addView(tx("MULTI-AGENT MARKET RESEARCH  /  SIGNAL-ONLY",11,green,true));
  gap(body,12);
  LinearLayout hero=col();hero.setBackground(shape(Color.rgb(16,34,46),22));pad(hero,16,16,16,16);
  LinearLayout hr=row();LinearLayout ht=col();ht.addView(tx("SYSTEM STATUS",10,muted,true));ht.addView(tx("RESEARCH MODE",18,white,true));hr.addView(ht,new LinearLayout.LayoutParams(0,-2,1));
  TextView live=tx("● NO BROKER EXECUTION",10,green,true);live.setBackground(shape(Color.rgb(22,55,51),16));pad(live,10,8,10,8);hr.addView(live);hero.addView(hr);
  gap(hero,8);hero.addView(tx("One decision pipeline. Multiple specialist checks. Any broker can be added through a verified data adapter; a broker is not connected just because its name appears here.",12,white,false));card(body,hero);
  LinearLayout voiceCard=col();voiceCard.setBackground(shape(panel,18));pad(voiceCard,14,14,14,14);
  voiceCard.addView(tx("VOICE MARKET ASSISTANT",13,white,true));
  voiceCard.addView(tx(voiceEnabled?"Voice alerts are ON · Android text-to-speech":"Voice alerts are OFF",11,muted,false));gap(voiceCard,8);
  LinearLayout voiceButtons=row();TextView toggleVoice=btn(voiceEnabled?"TURN VOICE OFF":"TURN VOICE ON",green);TextView speakNow=btn("SPEAK LATEST REPORT",white);
  voiceButtons.addView(toggleVoice,new LinearLayout.LayoutParams(0,-2,1));voiceButtons.addView(speakNow,new LinearLayout.LayoutParams(0,-2,1));voiceCard.addView(voiceButtons);
  toggleVoice.setOnClickListener(v->{voiceEnabled=!voiceEnabled;getSharedPreferences("elite_voice",MODE_PRIVATE).edit().putBoolean("enabled",voiceEnabled).apply();if(!voiceEnabled&&speech!=null)speech.stop();render();});
  speakNow.setOnClickListener(v->{if(!voiceEnabled){Toast.makeText(this,"Turn voice on first.",Toast.LENGTH_SHORT).show();return;}speakLatestScan();});
  body.addView(voiceCard);gap(body,12);
  body.addView(tx("AGENT NETWORK",12,muted,true));gap(body,7);
  String[][] agents={
   {"CHIEF","Decision coordinator","Combines evidence; final output can be BUY, SELL or WAIT","ACTIVE"},
   {"SCAN","Market discovery","Current public feed scanner covers XAUUSD and BTCUSD only","LIMITED"},
   {"VET","Signal verification","Checks trend/momentum agreement and feed freshness","BASIC"},
   {"QUANT","Strategy statistics","Historical test is early; no proven edge yet","TESTING"},
   {"RISK GUARDIAN","Risk veto","No signal is guaranteed; illustrative levels exclude verified broker costs","GUARD"},
   {"CRAWLER","Source discovery","Biquote public candles only; no universal broker feed connected","LIMITED"},
   {"SOCIAL / EVENTS","News and event context","No verified news/calendar source connected","OFFLINE"},
   {"FILLS / SIZE","Execution and position sizing","Disabled: no broker connection and no orders are sent","LOCKED"}
  };
  for(int k=0;k<agents.length;k+=2){
   LinearLayout pair=row();
   for(int j=k;j<Math.min(k+2,agents.length);j++){
    String[] a=agents[j];LinearLayout box=col();box.setBackground(shape(panel,18));pad(box,12,12,12,12);
    TextView state=tx(a[3],9,a[3].equals("ACTIVE")||a[3].equals("GUARD")?green:muted,true);box.addView(state);
    gap(box,4);box.addView(tx(a[0],13,white,true));gap(box,3);box.addView(tx(a[1],10,green,true));gap(box,5);box.addView(tx(a[2],10,muted,false));
    LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(0,-2,1);lp.rightMargin=j%2==0?7:0;lp.leftMargin=j%2==1?7:0;pair.addView(box,lp);
   }
   LinearLayout.LayoutParams pp=new LinearLayout.LayoutParams(-1,-2);pp.bottomMargin=10;body.addView(pair,pp);
  }
  body.addView(tx("MARKET RADAR",12,muted,true));gap(body,7);
  LinearLayout liveCard=col();liveCard.setBackground(shape(panel,20));pad(liveCard,14,14,14,14);
  liveCard.addView(tx("PUBLIC FEED · XAUUSD / BTCUSD",13,white,true));
  liveCard.addView(tx("Source: Biquote · not verified against any broker · no trade execution",10,muted,false));gap(liveCard,8);
  TextView liveStatus=tx("STATUS: "+liveScanStatus+(lastLiveScanAt>0?"\nLast saved: "+new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss",java.util.Locale.US).format(new java.util.Date(lastLiveScanAt)):""),11,liveScanning?green:muted,false);
  liveCard.addView(liveStatus);
  TextView liveButton=btn(liveScanning?"SCANNING...":"SCAN AVAILABLE LIVE FEED",green);liveCard.addView(liveButton);
  liveButton.setOnClickListener(v->{if(!liveScanning)runLiveScan();});
  gap(liveCard,7);
  if(latestLiveScan.isEmpty()) liveCard.addView(tx("No scan saved. Internet is required for fresh data. No current signal is inferred while offline.",11,white,false));
  else {
   for(String line:latestLiveScan){TextView item=tx(line,12,white,false);pad(item,0,9,0,9);liveCard.addView(item);View dv=new View(this);dv.setBackgroundColor(panel2);liveCard.addView(dv,new LinearLayout.LayoutParams(-1,1));}
   liveCard.addView(tx("Saved scan only; it may be stale and is not a current entry.",10,muted,false));
  }
  card(body,liveCard);
  body.addView(tx("COVERAGE & CONNECTORS",12,muted,true));gap(body,7);
  LinearLayout coverage=col();coverage.setBackground(shape(panel,18));pad(coverage,14,14,14,14);
  String[][] feeds={
   {"PUBLIC CANDLES","PARTIAL","Gold + Bitcoin only"},
   {"EXNESS / MT5","NOT CONNECTED","No account quotes or execution"},
   {"OTHER FOREX / CFD BROKERS","NOT CONNECTED","Needs each broker's feed/API or exported data"},
   {"CRYPTO EXCHANGES","NOT CONNECTED","Exchange-specific symbols and APIs required"},
   {"STOCKS / FUTURES / INDICES","NOT CONNECTED","Requires licensed/accessible data sources"}
  };
  for(String[] f:feeds){LinearLayout line=row();LinearLayout names=col();names.addView(tx(f[0],11,white,true));names.addView(tx(f[2],10,muted,false));line.addView(names,new LinearLayout.LayoutParams(0,-2,1));TextView badge=tx(f[1],9,f[1].equals("PARTIAL")?green:muted,true);badge.setGravity(Gravity.RIGHT|Gravity.CENTER_VERTICAL);line.addView(badge);pad(line,0,8,0,8);coverage.addView(line);}
  card(body,coverage);
  body.addView(tx("DECISION STANDARD",12,muted,true));gap(body,7);
  LinearLayout standard=col();standard.setBackground(shape(panel,18));pad(standard,14,14,14,14);
  standard.addView(tx("A sharp entry must pass all gates",14,white,true));gap(standard,6);
  standard.addView(tx("1  Fresh, timestamped candles\n2  Higher-timeframe trend agrees\n3  Entry-timeframe momentum confirms\n4  Spread and slippage are known\n5  Stop distance and position risk are valid\n6  Signal is logged for later review",12,white,false));gap(standard,7);
  standard.addView(tx("If a required gate is missing or conflicts, the correct output is WAIT. The app will not invent a high-confidence setup to look impressive.",11,green,true));card(body,standard);
  body.addView(tx("Current reality: this is a broker-agnostic design foundation, not a universal live-market connection. Signals are research only; no positions are opened.",11,muted,false));
 }
 void runLiveScan(){
  if(liveScanning)return;
  liveScanning=true;liveScanStatus="Fetching public candles...";
  render();
  new Thread(()->{
   ArrayList<String> result=new ArrayList<>();
   String status="Scan complete";
   try{
    for(String s:new String[]{"XAUUSD","BTCUSD"}){
     try{result.add(PublicFeedAnalyzer.scan(s).toDisplayText());}
     catch(Exception ex){result.add(s+"  |  WAIT\\nData error: "+ex.getMessage()+"\\nNo signal generated for this market.");}
    }
   }catch(Exception ex){status="Scan failed safely";}
   final String finalStatus=status;
   runOnUiThread(()->{boolean allFailed=!result.isEmpty();for(String line:result){if(!line.contains("Data error:"))allFailed=false;}
    if(allFailed&&!latestLiveScan.isEmpty()){liveScanStatus="Offline/feed error · showing last saved results";}
    else {latestLiveScan.clear();latestLiveScan.addAll(result);lastLiveScanAt=System.currentTimeMillis();saveCachedLiveScan();liveScanStatus=finalStatus+" · "+new java.text.SimpleDateFormat("HH:mm:ss",java.util.Locale.US).format(new java.util.Date());}
    liveScanning=false;render();if(voiceEnabled&&speechReady){StringBuilder announcement=new StringBuilder("EliteTrade AI scan complete. ");for(String line:latestLiveScan){String first=line.split("\\\\n")[0];announcement.append(first.replace("|"," signal ")).append(". ");}announcement.append("Research only. Check the full report before acting.");speak(announcement.toString());}});
  }).start();
 }
 void account(){
  body.addView(tx("BROKER CONNECTION SETUP",20,white,true));
  body.addView(tx("Profile only · no broker login or password is stored here",12,muted,false));gap(body,12);
  LinearLayout profile=col();profile.addView(tx("ACCOUNT CONNECTION",12,muted,true));profile.addView(tx("NOT CONNECTED",16,red,true));profile.addView(tx("Account type will be detected from a verified broker connection; no Demo/Live selector is required.",13,white,false));card(body,profile);
  EditText broker=field("Exness");EditText server=field("MT5 server name from your broker");EditText login=field("Optional account label (not a password)");
  input("BROKER NAME",broker);input("MT5 SERVER NAME",server);input("ACCOUNT LABEL (OPTIONAL — NOT A PASSWORD)",login);
  TextView save=btn("Check connection requirements",green);body.addView(save);save.setOnClickListener(v->{String b=broker.getText().toString().trim(),sv=server.getText().toString().trim();if(b.isEmpty()||sv.isEmpty()){Toast.makeText(this,"Enter broker and MT5 server name.",Toast.LENGTH_LONG).show();return;}new AlertDialog.Builder(this).setTitle("Connection not established").setMessage("These details identify the broker/server only. This app currently has no verified MT5 execution bridge, so it cannot log in, read account data, or place orders. No credentials were sent or stored.").setPositiveButton("OK",null).show();});
  gap(body,12);LinearLayout security=col();security.addView(tx("WHY THERE IS NO PASSWORD FIELD",12,green,true));security.addView(tx("A real autonomous Exness/MT5 bot needs a secure execution bridge and a verified connection. Never paste your trading password, investor password, API secret, or one-time code into this demo app or chat. This profile form does not connect to a broker.",13,white,false));card(body,security);
  LinearLayout checklist=col();checklist.addView(tx("LIVE-TRADING GATES — ALL REQUIRED",12,muted,true));checklist.addView(tx("□ Secure bridge and authentication\n□ Confirmed account connection (account type detected automatically)\n□ Verified live quote source and symbol specifications\n□ Paper tests and restart/recovery tests\n□ Risk Guardian, hard limits and kill switch\n□ Explicit user approval after review",13,white,false));card(body,checklist);

 }
}
