from engine import PaperEngine
import datetime
import traceback


def main():

    print("\n==============================")
    print("🚀 Worker Started")
    print("==============================\n")

    print(f"🕒 Time: {datetime.datetime.now()}\n")

    try:
        engine = PaperEngine()

        trades = engine.run_once()

        print(f"📊 Trades Found: {len(trades)}\n")

        if not trades:
            print("❌ No trades (market weak or no setups)\n")
        else:
            print("🔥 New Trades:\n")

            for t in trades:
                print(f"""
Symbol: {t['symbol']}
Entry: {t['entry']}
SL: {t['sl']}
Target: {t['target']}
---------------------------
""")

    except Exception as e:
        print("❌ ERROR OCCURRED\n")
        traceback.print_exc()


if __name__ == "__main__":
    main()
