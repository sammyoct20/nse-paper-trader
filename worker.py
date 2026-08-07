from datetime import datetime
from zoneinfo import ZoneInfo
from engine import PaperEngine
n=datetime.now(ZoneInfo("Asia/Kolkata"))
if n.weekday()<5 and ((n.hour==9 and n.minute>=15) or 10<=n.hour<=14 or (n.hour==15 and n.minute<=35)): PaperEngine().run_once()
else: print("Outside NSE market hours")
