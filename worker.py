from engine import PaperEngine
import datetime


def main():

    print("\n==============================")
    print("🚀 Worker Started")
    print("==============================\n")

    engine = PaperEngine()

    trades = engine.run_once()

    print(f"🕒 Time: {datetime.datetime.now()}")
    print(f"📊 Trades Found: {len(trades)}\n")

    if not trades:
        print("❌ No trades today (market not favorable)\n")
        return

    print("🔥 Trades:\n")

    for t in trades:
        print(f"""
Symbol: {t['symbol']}
Entry: {t['entry']}
SL: {t['sl']}
Target: {t['target']}
RR: {t['rr']}
Score: {t['score']}
---------------------------
""")


if __name__ == "__main__":
    main()
