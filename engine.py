from datetime import datetime
from zoneinfo import ZoneInfo

class PaperEngine:

    def __init__(self):
        # No external client for now
        pass

    def scan_market(self):
        """
        Dummy scanner (replace later with real logic)
        """
        print("Scanning market...")

        # fake signals
        signals = [
            {"symbol": "RELIANCE", "price": 2900},
            {"symbol": "TCS", "price": 3500}
        ]

        return signals

    def save(self, signals):
        """
        Dummy save (replace with DB later)
        """
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
