package com.elitetradeai.app;
import android.app.*;import android.os.*;import android.graphics.Color;import android.graphics.Typeface;import android.graphics.drawable.GradientDrawable;import android.view.*;import android.widget.*;import java.util.*;import java.text.*;
public class MainActivity extends Activity {
 int bg=Color.rgb(10,16,26),panel=Color.rgb(19,28,42),panel2=Color.rgb(27,39,56),white=Color.rgb(238,244,250),muted=Color.rgb(145,162,181),green=Color.rgb(69,222,177),red=Color.rgb(255,105,120);
 String tab="Market",symbol="XAUUSDm"; double balance=10000; ArrayList<String> positions=new ArrayList<>(); LinearLayout root,body; DecimalFormat df=new DecimalFormat("#,##0.00"); PaperSimulationCoordinator paperCoordinator=new PaperSimulationCoordinator(); ArrayList<String> latestScan=new ArrayList<>(); int scanCount=0;
 public void onCreate(Bundle b){super.onCreate(b);getWindow().setStatusBarColor(bg);getWindow().setNavigationBarColor(bg);render();}
 GradientDrawable shape(int c,int r){GradientDrawable d=new GradientDrawable();d.setColor(c);d.setCornerRadius(r);return d;}
 TextView tx(String s,int z,int c,boolean bold){TextView t=new TextView(this);t.setText(s);t.setTextSize(z);t.setTextColor(c);if(bold)t.setTypeface(null,Typeface.BOLD);t.setGravity(Gravity.CENTER_VERTICAL);return t;}
 LinearLayout col(){LinearLayout l=new LinearLayout(this);l.setOrientation(1);return l;} LinearLayout row(){LinearLayout l=new LinearLayout(this);l.setOrientation(0);l.setGravity(Gravity.CENTER_VERTICAL);return l;}
 void pad(View v,int a,int b,int c,int d){v.setPadding(a,b,c,d);} void gap(LinearLayout l,int h){View v=new View(this);l.addView(v,new LinearLayout.LayoutParams(1,h));}
 void card(LinearLayout p,View v){v.setBackground(shape(panel,22));pad(v,15,14,15,14);LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.bottomMargin=11;p.addView(v,lp);}
 TextView btn(String s,int c){TextView t=tx(s,13,c,true);t.setGravity(17);pad(t,10,12,10,12);t.setBackground(shape(panel2,15));return t;}
 void render(){root=col();root.setBackgroundColor(bg);setContentView(root);LinearLayout head=col();pad(head,17,14,17,8);LinearLayout rr=row();LinearLayout title=col();title.addView(tx("ELITETRADE AI",20,white,true));title.addView(tx("ELITE INTELLIGENCE LAB  •  PAPER MODE",10,green,true));rr.addView(title,new LinearLayout.LayoutParams(0,-2,1));TextView bd=tx("● DEMO",11,green,true);bd.setBackground(shape(Color.rgb(20,56,52),18));pad(bd,10,7,10,7);rr.addView(bd);head.addView(rr);TextView notice=tx("SIMULATED DATA  ·  EXNESS / MT5 NOT CONNECTED",10,muted,true);pad(notice,10,10,10,10);notice.setBackground(shape(panel,12));LinearLayout.LayoutParams n=new LinearLayout.LayoutParams(-1,-2);n.topMargin=10;head.addView(notice,n);root.addView(head);
 ScrollView sc=new ScrollView(this);body=col();pad(body,15,7,15,18);sc.addView(body);root.addView(sc,new LinearLayout.LayoutParams(-1,0,1));LinearLayout nav=row();nav.setBackgroundColor(panel);for(String s:new String[]{"Market","Chart","LAB","Trade","Account"}){TextView b=tx(s,11,tab.equals(s)?green:muted,true);b.setGravity(17);pad(b,1,17,1,17);nav.addView(b,new LinearLayout.LayoutParams(0,-2,1));b.setOnClickListener(v->{tab=s;render();});}root.addView(nav);
 if(tab.equals("Market"))market();else if(tab.equals("Chart"))chart();else if(tab.equals("LAB"))lab();else if(tab.equals("Trade"))trade();else account();}
 void market(){LinearLayout h=col();h.addView(tx("Demo account equity",12,muted,false));h.addView(tx("$"+df.format(balance),30,white,true));h.addView(tx("Virtual balance · not withdrawable",12,muted,false));card(body,h);body.addView(tx("MARKET WATCH",12,muted,true));gap(body,8);
 String[] sy={"XAUUSDm","BTCUSDm","EURUSDm","GBPUSDm","USOILm"};String[] pr={"2350.40","62450.00","1.0842","1.2715","78.35"};for(int i=0;i<sy.length;i++){final String s=sy[i];LinearLayout r=row();LinearLayout l=col();l.addView(tx(s,16,white,true));l.addView(tx("Simulated quote · feed not connected",10,muted,false));r.addView(l,new LinearLayout.LayoutParams(0,-2,1));TextView p=tx(pr[i],15,white,true);p.setGravity(Gravity.RIGHT);r.addView(p);pad(r,0,12,0,12);body.addView(r);View line=new View(this);line.setBackgroundColor(panel2);body.addView(line,new LinearLayout.LayoutParams(-1,1));r.setOnClickListener(v->{symbol=s;tab="Chart";render();});}gap(body,12);body.addView(tx("Prices are illustrative demo values, not executable market quotes.",12,muted,false));}
 void chart(){body.addView(tx(symbol,24,white,true));body.addView(tx("Chart preview · SIMULATED DATA",12,muted,false));gap(body,14);LinearLayout c=col();c.setBackground(shape(panel,18));pad(c,12,15,12,15);c.addView(tx("ILLUSTRATIVE PRICE MOVEMENT",11,muted,true));Random r=new Random(7);for(int i=0;i<18;i++){LinearLayout br=row();br.addView(new View(this),new LinearLayout.LayoutParams(0,1,1));View bar=new View(this);bar.setBackground(shape(i%4==0?red:green,4));LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(8,22+r.nextInt(65));lp.rightMargin=5;br.addView(bar,lp);c.addView(br,new LinearLayout.LayoutParams(-1,70));}body.addView(c);gap(body,12);LinearLayout t=row();for(String s:new String[]{"M1","M5","M15","H1","H4","D1"})t.addView(btn(s,white),new LinearLayout.LayoutParams(0,-2,1));body.addView(t);gap(body,12);LinearLayout i=col();i.addView(tx("INDICATORS",11,muted,true));i.addView(tx("EMA 20 / EMA 50 · RSI 14 · ATR 14",14,white,false));i.addView(tx("Illustrative values only; no live feed.",12,muted,false));card(body,i);}
 void analysis(){LinearLayout a=col();a.addView(tx("RULE-BASED DEMO ANALYSIS",12,muted,true));gap(a,8);a.addView(tx("Status: WAIT",24,green,true));a.addView(tx("Trend: not verified\nRSI: illustrative neutral reading\nVolatility: simulated estimate\nNo live market feed is connected.",14,white,false));card(body,a);LinearLayout b=col();b.addView(tx("WHY WAIT?",12,muted,true));gap(b,6);b.addView(tx("Simulated or conflicting inputs should not trigger real orders.",14,white,false));card(body,b);body.addView(tx("Educational demo only · Not financial advice.",12,muted,false));}
 EditText field(String value){EditText e=new EditText(this);e.setSingleLine(true);e.setText(value);e.setTextColor(white);e.setInputType(8194);e.setBackground(shape(panel2,12));pad(e,10,5,10,5);return e;}
 void input(String label,EditText e){body.addView(tx(label,10,muted,true));body.addView(e,new LinearLayout.LayoutParams(-1,-2));gap(body,7);}
 void trade(){body.addView(tx("PAPER TRADE TICKET",20,white,true));body.addView(tx("Local demo only. No broker orders are sent.",12,muted,false));gap(body,12);EditText entry=field("2350.40"),sl=field("2345.00"),tp=field("2360.00"),lot=field("0.01");input("ENTRY PRICE",entry);input("STOP LOSS",sl);input("TAKE PROFIT",tp);input("LOT SIZE (MAX 0.10)",lot);LinearLayout sides=row();TextView buy=btn("PAPER BUY",green),sell=btn("PAPER SELL",red);sides.addView(buy,new LinearLayout.LayoutParams(0,-2,1));sides.addView(sell,new LinearLayout.LayoutParams(0,-2,1));body.addView(sides);buy.setOnClickListener(v->order("BUY",entry,sl,tp,lot));sell.setOnClickListener(v->order("SELL",entry,sl,tp,lot));gap(body,15);body.addView(tx("OPEN PAPER POSITIONS",12,muted,true));if(positions.isEmpty())body.addView(tx("No open demo positions.",13,muted,false));for(String p:new ArrayList<>(positions)){LinearLayout c=col();c.addView(tx(p,14,white,true));TextView close=btn("Close paper position",white);close.setOnClickListener(v->{positions.remove(p);render();});c.addView(close);card(body,c);}}
 void order(String side,EditText e,EditText s,EditText t,EditText l){try{double en=Double.parseDouble(e.getText().toString()),st=Double.parseDouble(s.getText().toString()),ta=Double.parseDouble(t.getText().toString()),lo=Double.parseDouble(l.getText().toString());if(en<=0||st<=0||ta<=0||lo<=0||lo>.1||(side.equals("BUY")&&(st>=en||ta<=en))||(side.equals("SELL")&&(st<=en||ta>=en))||Math.abs(en-st)*lo*100>balance*.02){Toast.makeText(this,"Invalid values or blocked by 2% demo risk limit.",Toast.LENGTH_LONG).show();return;}String p=side+" "+symbol+" · "+lo+" lots · entry "+en+" · SL "+st+" · TP "+ta;positions.add(p);Toast.makeText(this,"Paper order saved. No real order sent.",Toast.LENGTH_LONG).show();render();}catch(Exception ex){Toast.makeText(this,"Enter valid numeric values.",Toast.LENGTH_LONG).show();}}
 void lab(){
  body.addView(tx("ELITETRADE INTELLIGENCE LAB",20,white,true));
  body.addView(tx("Multi-agent research workspace · PAPER MODE",12,green,true));gap(body,12);
  LinearLayout status=col();status.addView(tx("SYSTEM SAFETY STATUS",12,muted,true));status.addView(tx("Broker execution: OFF\nData source: SIMULATED\nAutonomous live mode: DISABLED\nPaper analysis: READY FOR MANUAL SCANS",14,white,false));card(body,status);
  TextView scan=btn("RUN SIMULATED MARKET SCAN",green);body.addView(scan);
  scan.setOnClickListener(v->{
   try {
    List<PaperSimulationCoordinator.Result> results=paperCoordinator.scan(System.currentTimeMillis());
    latestScan.clear();scanCount++;
    for(PaperSimulationCoordinator.Result result:results){
     latestScan.add(result.symbol+"  |  "+df.format(result.simulatedMid)+"  |  "+result.proposal+"\n"+result.explanation);
    }
    render();
   } catch(Exception ex) {
    Toast.makeText(this,"Scan stopped safely: "+ex.getMessage(),Toast.LENGTH_LONG).show();
   }
  });
  gap(body,10);
  LinearLayout scanCard=col();scanCard.addView(tx("LATEST SCAN · "+scanCount,12,muted,true));
  if(latestScan.isEmpty()) scanCard.addView(tx("No scan has run yet. Tap the scan button to evaluate the simulated watchlist.",13,white,false));
  else for(String line:latestScan){TextView item=tx(line,12,white,false);pad(item,0,8,0,8);scanCard.addView(item);}
  card(body,scanCard);
  String[][] modules={
   {"01 · MARKET RADAR","Scans five illustrative symbols","Simulated watchlist scan implemented; live feed not connected"},
   {"02 · ELITE IQ","Combines trend and momentum opinions","Basic consensus analysis integrated into the scan"},
   {"03 · STRATEGY COMPETITION LAB","Compare rule sets against history","Not implemented yet"},
   {"04 · RISK GUARDIAN","Independent safety veto before a paper order","Safety model exists; not yet connected to a complete order flow"},
   {"05 · EVENT INTELLIGENCE","Economic calendar and event-risk awareness","Not implemented; no calendar feed connected"},
   {"06 · COMMAND CENTRE","Control, journal, analytics and kill switch","Partial UI; persistent journal and kill switch pending"},
   {"07 · DATA INTEGRITY MONITOR","Reject stale or unverified inputs","Scan rejects stale/non-simulated data; full monitoring pending"},
   {"08 · BACKTEST & MEMORY","Review simulated results and decisions","Not implemented yet"}
  };
  for(String[] m:modules){LinearLayout box=col();box.addView(tx(m[0],14,white,true));gap(box,4);box.addView(tx(m[1],12,muted,false));gap(box,4);box.addView(tx(m[2],11,m[2].startsWith("Simulated")||m[2].startsWith("Basic")||m[2].startsWith("Scan")?green:muted,false));card(body,box);}
  body.addView(tx("Every result uses artificial prices. The scan proposes WAIT/BUY/SELL for research only; it does not open positions or send broker orders.",12,muted,false));
 }
 void account(){
  body.addView(tx("BROKER CONNECTION SETUP",20,white,true));
  body.addView(tx("Profile only · no broker login or password is stored here",12,muted,false));gap(body,12);
  LinearLayout profile=col();profile.addView(tx("CURRENT MODE",12,muted,true));profile.addView(tx("SIMULATED PAPER TRADING",16,green,true));profile.addView(tx("Exness / MT5 connection: NOT CONNECTED",13,white,false));card(body,profile);
  EditText broker=field("Exness");EditText server=field("e.g. Exness-MT5Trial (confirm in MT5)");EditText login=field("Account number / label (optional)");
  input("BROKER NAME",broker);input("MT5 SERVER NAME",server);input("ACCOUNT LABEL (OPTIONAL — NOT A PASSWORD)",login);
  TextView save=btn("Save local connection profile",green);body.addView(save);save.setOnClickListener(v->{String b=broker.getText().toString().trim(),sv=server.getText().toString().trim(),id=login.getText().toString().trim();if(b.isEmpty()||sv.isEmpty()){Toast.makeText(this,"Enter broker and server name.",Toast.LENGTH_LONG).show();return;}Toast.makeText(this,"Profile fields checked only. No broker connection has been made or saved securely yet.",Toast.LENGTH_LONG).show();});
  gap(body,12);LinearLayout security=col();security.addView(tx("WHY THERE IS NO PASSWORD FIELD",12,green,true));security.addView(tx("A real autonomous Exness/MT5 bot needs a secure execution bridge and a verified connection. Never paste your trading password, investor password, API secret, or one-time code into this demo app or chat. This profile form does not connect to a broker.",13,white,false));card(body,security);
  LinearLayout checklist=col();checklist.addView(tx("LIVE-TRADING GATES — ALL REQUIRED",12,muted,true));checklist.addView(tx("□ Secure bridge and authentication\n□ Confirmed demo-account connection\n□ Verified live quote source and symbol specifications\n□ Paper tests and restart/recovery tests\n□ Risk Guardian, hard limits and kill switch\n□ Explicit user approval after review",13,white,false));card(body,checklist);
  TextView reset=btn("Reset local demo account to $10,000",white);reset.setOnClickListener(v->{balance=10000;positions.clear();render();});body.addView(reset);
 }
}
