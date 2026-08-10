import yfinance as yf

class PaperEngine:

    def __init__(self):
        pass

    def scan_market(self):
        print("Scanning market (Yahoo Finance)...")

        # NIFTY 50 sample (you can expand later)
        symbols = [
            "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS",
            "ICICIBANK.NS", "LT.NS", "SBIN.NS", "ITC.NS"
        ]

        signals = []

        for sym in symbols:
            try:
                df = yf.download(sym, period="5d", interval="5m", progress=False)

                if df.empty:
                    continue

                # Simple strategy: price breakout + volume spike
                last = df.iloc[-1]
                prev = df.iloc[-2]

                price = last["Close"]
                prev_price = prev["Close"]

                volume = last["Volume"]
                avg_volume = df["Volume"].mean()

                # Condition (basic but real)
                if price > prev_price and volume > 1.5 * avg_volume:
                    signals.append({
                        "symbol": sym,
                        "price": round(price, 2),
                        "volume": int(volume)
                    })

            except Exception as e:
                print(f"Error fetching {sym}: {e}")

        return signals

    def save(self, signals):
        print(f"Saving {len(signals)} signals...")

    def run_once(self):
        try:
            print("=== ENGINE START ===")

            signals = self.scan_market()
            print(f"Signals found: {len(signals)}")

            if signals:
                print("Sample:", signals[:3])
            else:
                print("No signals found")

            self.save(signals)

            print("=== ENGINE END ===")

        except Exception as e:
            print("FATAL ERROR in run_once:", str(e))
            raise
