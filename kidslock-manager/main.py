import logging
import threading
import time
import json
import os
import sqlite3
import requests
import subprocess
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import uvicorn
import paho.mqtt.client as mqtt

# --- Init & Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KidsLock")
OPTIONS_PATH = "/data/options.json"
DB_PATH = "/data/kidslock.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('CREATE TABLE IF NOT EXISTS tv_state (tv_name TEXT PRIMARY KEY, remaining_minutes REAL, last_update TEXT)')
    conn.commit()
    conn.close()

init_db()

try:
    if os.path.exists(OPTIONS_PATH):
        with open(OPTIONS_PATH, "r") as f:
            options = json.load(f)
    else: options = {"tvs": [], "mqtt": {}}
except: options = {"tvs": [], "mqtt": {}}

# --- Veilig Pingen ---
def is_online(ip):
    try:
        res = subprocess.run(['ping', '-c', '1', '-W', '1', str(ip)], 
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0
    except: return False

# --- Global State ---
data_lock = threading.RLock()
tv_states = {}
first_run_done = False

for tv in options.get("tvs", []):
    limit = tv.get("daily_limit") if tv.get("daily_limit") is not None else 120
    tv_states[tv["name"]] = {
        "config": tv,
        "online": False,
        "locked": False,
        "remaining_minutes": float(limit),
        "manual_override": False
    }

# --- Monitor ---
def monitor():
    global first_run_done
    time.sleep(10)
    last_tick = time.time()
    while True:
        delta = (time.time() - last_tick) / 60.0
        last_tick = time.time()
        now = datetime.now()
        with data_lock:
            for name, state in tv_states.items():
                state["online"] = is_online(state["config"]["ip"])
                
                # ONBEPERKT LOGICA: Slaat alle blokkades over
                if state["config"].get("no_limit_mode", False):
                    if state["locked"] and not state["manual_override"]:
                        try: requests.post(f"http://{state['config']['ip']}:8080/unlock", timeout=5)
                        except: pass
                        state["locked"] = False
                    continue

                if state["online"] and not state["locked"]:
                    state["remaining_minutes"] = max(0, state["remaining_minutes"] - delta)
                
                bt_str = state["config"].get("bedtime") or "21:00"
                try: bt = datetime.strptime(bt_str, "%H:%M").time()
                except: bt = datetime.strptime("21:00", "%H:%M").time()
                
                is_bt = (now.time() > bt or now.time() < datetime.strptime("04:00", "%H:%M").time())
                
                if first_run_done and not state["manual_override"]:
                    if (state["remaining_minutes"] <= 0 or is_bt) and not state["locked"]:
                        try: requests.post(f"http://{state['config']['ip']}:8080/lock", timeout=5)
                        except: pass
                        state["locked"] = True
        first_run_done = True
        time.sleep(30)

threading.Thread(target=monitor, daemon=True).start()

# --- Web UI ---
app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    tvs_display = []
    with data_lock:
        for name, s in tv_states.items():
            # Status bericht voor de HTML
            status_text = "Actief op netwerk" if s["online"] else "TV staat uit"
            is_unlimited = s["config"].get("no_limit_mode", False)
            if is_unlimited:
                status_text = "ONBEPERKT MODUS ACTIEF"
            
            tvs_display.append({
                "name": name,
                "online": s["online"],
                "remaining": "∞" if is_unlimited else int(s["remaining_minutes"]),
                "limit": s["config"].get("daily_limit") or 120,
                "bedtime": s["config"].get("bedtime") or "21:00",
                "locked": s["locked"],
                "status_msg": status_text,
                "no_limit": is_unlimited
            })
    return templates.TemplateResponse("index.html", {"request": request, "tvs": tvs_display})

@app.post("/toggle_lock/{name}")
async def toggle(name: str):
    return RedirectResponse(url="./", status_code=303)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)