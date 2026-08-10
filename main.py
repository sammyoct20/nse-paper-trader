import threading
from engine import PaperEngine
import os

def run_engine():
    PaperEngine().run_loop()

threading.Thread(target=run_engine, daemon=True).start()

os.system("streamlit run app.py --server.port 10000 --server.address 0.0.0.0")
