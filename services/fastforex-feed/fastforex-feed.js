const WebSocket = require("ws");

const apiKey = process.env.FASTFOREX_API_KEY;
if (!apiKey) throw new Error("Set FASTFOREX_API_KEY first");

const ws = new WebSocket("wss://fastforex.wsdx.io");

ws.on("open", () => {
  // Confirm the exact authentication and subscription schema with FastForex
  // for your account/plan before relying on this in production.
  ws.send(JSON.stringify({ type: "auth", api_key: apiKey }));
  ws.send(JSON.stringify({
    type: "subscribe",
    streams: ["fx", "metals"],
    symbols: ["EUR/USD", "XAU/USD"]
  }));
});

ws.on("message", data => console.log(data.toString()));
ws.on("error", err => console.error("WebSocket error:", err.message));
ws.on("close", () => console.log("Connection closed"));
