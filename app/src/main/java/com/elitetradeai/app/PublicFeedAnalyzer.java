package com.elitetradeai.app;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.text.DecimalFormat;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Signal-only port of the existing EMA/RSI/ATR analysis.
 * Public Biquote feed; not an Exness quote source and never places orders.
 */
public final class PublicFeedAnalyzer {
    private static final String BASE = "https://biquote.io/api/";
    private static final DecimalFormat DF = new DecimalFormat("0.00000");

    private PublicFeedAnalyzer() {}

    public static final class Result {
        public final String symbol, signal, trend15, condition1, detail;
        public final double price, rsi1, atr1, stopLoss, takeProfit;
        public final long time1, time15;

        Result(String symbol, String signal, String trend15, String condition1,
               String detail, double price, double rsi1, double atr1,
               double stopLoss, double takeProfit, long time1, long time15) {
            this.symbol=symbol; this.signal=signal; this.trend15=trend15;
            this.condition1=condition1; this.detail=detail; this.price=price;
            this.rsi1=rsi1; this.atr1=atr1; this.stopLoss=stopLoss;
            this.takeProfit=takeProfit; this.time1=time1; this.time15=time15;
        }

        public String toDisplayText() {
            StringBuilder b = new StringBuilder();
            b.append(symbol).append("  |  ").append(signal).append("\n");
            b.append("Price: ").append(DF.format(price)).append("\n");
            b.append("M15 trend: ").append(trend15).append("  ·  M1: ").append(condition1).append("\n");
            b.append("M1 RSI(14): ").append(DF.format(rsi1)).append("\n");
            b.append(detail).append("\n");
            if ("BUY".equals(signal) || "SELL".equals(signal)) {
                b.append("Illustrative entry: ").append(DF.format(price)).append("\n");
                b.append("Illustrative SL: ").append(DF.format(stopLoss)).append("\n");
                b.append("Illustrative TP: ").append(DF.format(takeProfit)).append("\n");
                b.append("Approx. 2:1 reward/risk before spread, fees and slippage.");
            } else {
                b.append("No entry levels: conditions are not confirmed.");
            }
            return b.toString();
        }
    }

    private static final class Candle {
        final long time;
        final double high, low, close;
        Candle(long time, double high, double low, double close) {
            this.time=time; this.high=high; this.low=low; this.close=close;
        }
    }

    public static Result scan(String symbol) throws Exception {
        List<Candle> m1 = fetch(symbol, "1m");
        List<Candle> m15 = fetch(symbol, "15m");
        long now = System.currentTimeMillis();
        if (m1.size() < 30 || m15.size() < 30) {
            throw new IllegalStateException(symbol + ": not enough candles to calculate indicators.");
        }
        Candle last1 = m1.get(m1.size()-1), last15 = m15.get(m15.size()-1);
        long age1 = now - last1.time;
        long age15 = now - last15.time;
        if (age1 < -60000 || age15 < -60000 || age1 > 180000 || age15 > 1200000) {
            return new Result(symbol, "WAIT", "UNKNOWN", "UNKNOWN",
                    "Stale or future-dated feed candle; wait for fresh data.",
                    last1.close, rsi(m1, 14), atr(m1, 14), 0, 0, last1.time, last15.time);
        }

        double fast15 = ema(m15, 9), slow15 = ema(m15, 26);
        String trend = fast15 > slow15 ? "BUY" : fast15 < slow15 ? "SELL" : "WAIT";
        double fast1 = ema(m1, 9), slow1 = ema(m1, 26), rsi1 = rsi(m1, 14);
        String cond = "WAIT";
        if (fast1 > slow1 && rsi1 >= 50 && rsi1 < 70) cond = "BUY";
        else if (fast1 < slow1 && rsi1 <= 50 && rsi1 > 30) cond = "SELL";

        String signal = trend.equals(cond) && !trend.equals("WAIT") ? trend : "WAIT";
        String detail = signal.equals("WAIT")
                ? "M15 trend and M1 momentum do not agree."
                : "M15 trend and M1 momentum agree; signal only, not a trade instruction.";
        double atr = atr(m1, 14);
        double sl = 0, tp = 0;
        if (signal.equals("BUY")) { sl = last1.close - 1.5 * atr; tp = last1.close + 3.0 * atr; }
        if (signal.equals("SELL")) { sl = last1.close + 1.5 * atr; tp = last1.close - 3.0 * atr; }
        return new Result(symbol, signal, trend, cond, detail, last1.close, rsi1, atr,
                sl, tp, last1.time, last15.time);
    }

    private static List<Candle> fetch(String symbol, String interval) throws Exception {
        String url = BASE + symbol + "/ohlc?interval=" + interval + "&limit=60";
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setRequestMethod("GET");
        c.setConnectTimeout(10000);
        c.setReadTimeout(10000);
        c.setRequestProperty("User-Agent", "EliteTradeAI-Android/1.0");
        try {
            int status = c.getResponseCode();
            if (status != 200) throw new IllegalStateException("Feed HTTP " + status + " for " + symbol + " " + interval);
            StringBuilder body = new StringBuilder();
            try (BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream()))) {
                String line;
                while ((line = r.readLine()) != null) body.append(line);
            }
            JSONObject root = new JSONObject(body.toString());
            JSONArray bars = root.getJSONArray("bars");
            List<Candle> out = new ArrayList<>();
            for (int i=0; i<bars.length(); i++) {
                JSONObject x = bars.getJSONObject(i);
                long time = Instant.parse(x.getString("openTime")).toEpochMilli();
                out.add(new Candle(time, x.getDouble("high"), x.getDouble("low"), x.getDouble("close")));
            }
            Collections.sort(out, (a,b) -> Long.compare(a.time,b.time));
            // Indicators and signals must use completed candles, not the live forming bar.
            long intervalMillis = "1m".equals(interval) ? 60_000L : 15L * 60_000L;
            long now = System.currentTimeMillis();
            out.removeIf(candle -> candle.time + intervalMillis > now);
            return out;
        } finally {
            c.disconnect();
        }
    }

    private static double ema(List<Candle> bars, int period) {
        double k = 2.0 / (period + 1.0);
        int start = Math.max(0, bars.size() - period * 4);
        double value = bars.get(start).close;
        for (int i=start+1; i<bars.size(); i++) value = bars.get(i).close*k + value*(1-k);
        return value;
    }

    private static double rsi(List<Candle> bars, int period) {
        if (bars.size() <= period) return 50;
        double gains=0, losses=0;
        int start=bars.size()-period;
        for (int i=start; i<bars.size(); i++) {
            double d=bars.get(i).close-bars.get(i-1).close;
            if (d>0) gains+=d; else losses-=d;
        }
        if (losses==0) return gains==0 ? 50 : 100;
        return 100.0 - (100.0 / (1.0 + gains/losses));
    }

    private static double atr(List<Candle> bars, int period) {
        if (bars.size() < period+1) return 0;
        double sum=0;
        for (int i=bars.size()-period; i<bars.size(); i++) {
            Candle current=bars.get(i), previous=bars.get(i-1);
            sum += Math.max(current.high-current.low,
                    Math.max(Math.abs(current.high-previous.close), Math.abs(current.low-previous.close)));
        }
        return sum/period;
    }
}
