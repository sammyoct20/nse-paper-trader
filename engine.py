from datetime import datetime
from zoneinfo import ZoneInfo
import yfinance as yf
import pandas as pd


class PaperEngine:

    def __init__(self):
        self.capital = 100000
        self.risk_per_trade = 0.01  # 1%

    # ---------------- SCANNER ----------------
    def scan_market(self):
        print("Scanning NIFTY 50 (refined strategy)...")

        symbols = [
            "RELIANCE.NS","TCS.NS","INFY.NS","HDFCBANK.NS","ICICIBANK.NS","SBIN.NS",
            "LT.NS","ITC.NS","HINDUNILVR.NS","KOTAKBANK.NS","AXISBANK.NS","BAJFINANCE.NS",
            "BHARTIARTL.NS","ASIANPAINT.NS","MARUTI.NS","HCLTECH.NS","SUNPHARMA.NS",
            "TITAN.NS","ULTRACEMCO.NS","WIPRO.NS","NTPC.NS","POWERGRID.NS","NESTLEIND.NS",
            "ONGC.NS","TECHM.NS","ADANIENT.NS","ADANIPORTS.NS","COALINDIA.NS",
            "TATASTEEL.NS","JSWSTEEL.NS","INDUSINDBK.NS","DRREDDY.NS","CIPLA.NS",
            "APOLLOHOSP.NS","EICHERMOT.NS","GRASIM.NS","HEROMOTOCO.NS","BPCL.NS",
            "BRITANNIA.NS","DIVISLAB.NS","HDFCLIFE.NS","SBILIFE.NS","BAJAJFINSV.NS",
            "SHREECEM.NS","UPL.NS","BAJAJ-AUTO.NS","TATAMOTORS.NS","M&M.NS","HINDALCO.NS"
        ]

        signals = []

        try:
            df = yf.download(
                tickers=symbols,
                period="10d",
                interval="5m",
                group_by='ticker',
                auto_adjust=True,
                threads=True,
                progress=False
            )

            if df is None or df.empty:
                print("No data")
                return []

            for sym in symbols:
                try:
                    if sym not in df.columns:
                        continue

                    data = df[sym].dropna()

                    if len(data) < 50:
                        continue

                    # -------- INDICATORS --------
                    data["EMA20"] = data["Close"].ewm(span=20).mean()
                    data["EMA50"] = data["Close"].ewm(span=50).mean()

                    last = data.iloc[-1]

                    close = float(last["Close"])
                    volume = float(last["Volume"])
                    avg_volume = float(data["Volume"].rolling(20).mean().iloc[-1])

                    ema20 = float(last["EMA20"])
                    ema50 = float(last["EMA50"])

                    # -------- SCORING --------
                    score = 0

                    if close > ema20 > ema50:
                        score += 1

                    recent_high = float(data["High"].rolling(15).max().iloc[-2])
                    if close >= recent_high * 0.995:
                        score += 1

                    if volume > 1.2 * avg_volume:
                        score += 1

                    # -------- STRICT FILTER --------
                    if score < 2:
                        continue

                    # -------- RISK MANAGEMENT --------
                    stop_loss = float(data["Low"].rolling(5).min().iloc[-1])
                    risk_per_share = close - stop_loss

                    # ❌ Reject tiny SL (critical fix)
                    if risk_per_share < close * 0.005:
                        continue

                    risk_amount = self.capital * self.risk_per_trade
                    qty = int(risk_amount / risk_per_share)

                    if qty <= 0:
                        continue

                    target = close + (2 * risk_per_share)

                    # ❌ Reject tiny targets
                    if (target - close) / close < 0.005:
                        continue

                    signals.append({
                        "symbol": sym,
                        "entry": round(close, 2),
                        "stop_loss": round(stop_loss, 2),
                        "target": round(target, 2),
                        "qty": qty,
                        "score": score,
                        "risk_pct": round((risk_per_share / close) * 100, 2)
                    })

                except Exception as inner_e:
                    print(f"Error {sym}: {inner_e}")

        except Exception as e:
            print("Batch error:", e)

        # -------- FINAL FILTERING --------
        signals = sorted(signals, key=lambda x: (x['score'], x['risk_pct']), reverse=True)

        # ❌ Limit trades (very important)
        signals = signals[:5]

        return signals

    # ---------------- SAVE ----------------
    def save(self, signals):
        print(f"Saving {len(signals)} trades...")
        for s in signals:
            print(s)

    # ---------------- RUN ----------------
    def run_once(self):
        try:
            print("=== ENGINE START ===")

            signals = self.scan_market()

            print(f"Signals found: {len(signals)}")

            if signals:
                print("Top signals:", signals)
            else:
                print("No signals")

            self.save(signals)

            print("=== ENGINE END ===")

        except Exception as e:
            print("FATAL ERROR:", str(e))
            raise
