from engine import PaperEngine
import datetime
import traceback


def main():

    print("🚀 Worker Started")
    print("Time:", datetime.datetime.now())

    try:
        engine = PaperEngine()
        trades = engine.run_once()

        print("Trades Found:", len(trades))

    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    main()
