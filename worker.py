from engine import PaperEngine
from datetime import datetime

print("🚀 Worker Started")
print("Time:", datetime.now())

engine = PaperEngine()
engine.run()

print(f"✅ Done | Capital: ₹{engine.balance:,.2f}")
