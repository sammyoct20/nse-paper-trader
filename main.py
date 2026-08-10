import time
from engine import PaperEngine

engine = PaperEngine()

while True:
    engine.run_once()
    time.sleep(300)   # runs every 5 minutes
