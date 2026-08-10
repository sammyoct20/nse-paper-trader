from datetime import datetime
from zoneinfo import ZoneInfo
from engine import PaperEngine

n = datetime.now(ZoneInfo("Asia/Kolkata"))

print("Current IST time:", n)

is_market_open = (
    n.weekday() < 5 and (
        (n.hour == 9 and n.minute >= 15) or 
        10 <= n.hour <= 14 or 
        (n.hour == 15 and n.minute <= 35)
    )
)

print("Market open:", is_market_open)

if is_market_open:
    print("Starting scanner...")
    PaperEngine().run_once()
    print("Scanner finished")
else:
    print("Outside NSE market hours")
