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

# --- Initialisatie ---
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

# Haal opties op met foutafhandeling
try:
    if os.path.exists(OPTIONS_PATH):
        with open(OPTIONS_PATH, "r") as f:
            options = json.load(f)
    else:
        options = {"tvs": [], "mqtt": {}}
except Exception as e:
    logger.error(f"Fout bij laden opties: {e}")
    options = {"tvs": [], "mqtt": {}}

# --- Veilig Pingen ---
def is_online(ip):
    try:
        res = subprocess.run(['ping', '-c', '1', '-W', '1', str(ip)], 
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0
    except: return False

# --- Global State met Fallbacks ---
data_lock = threading.RLock()
tv_states = {}
first_run_done = False

for tv in options.get("tvs", []):
    # Als een waarde None is (leeg in HA), gebruik dan de standaard
    limit = tv.get("daily_limit") if tv.get("daily_limit") is not None else 120
    
    tv_states[tv["name"]] = {
        "config": tv,
        "online": False,
        "locked": False,
        "remaining_minutes": float(limit),
        "manual_override": False
    }

# --- MQTT Setup ---
mqtt_conf = options.get("mqtt", {})
mqtt_client = mqtt.Client()

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("✅ MQTT Verbonden")
        for name in tv_states:
            slug = name.lower().replace(" ", "_")
            client.publish(f"homeassistant/switch/kidslock_{slug}/config", json.dumps({
                "name": f"{name} Lock", "command_topic": f"kidslock/{slug}/set",
                "state_topic": f"kidslock/{slug}/state", "unique_id": f"kidslock_{slug}_switch",
                "device": {"identifiers": [f"kidslock_{slug}"], "name": name}
            }), retain=True)
            client.subscribe(f"kidslock/{slug}/set")

mqtt_client.on_connect = on_connect
if mqtt_conf.get("username"):
    mqtt_client.username_pw_set(mqtt_conf["username"], mqtt_conf.get("password"))

try:
    mqtt_client.connect(mqtt_conf.get("host", "core-mosquitto"), mqtt_conf.get("port", 1883))
    mqtt_client.loop_start()
except Exception as e: logger.error(f"MQTT Fout: {e}")

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
                
                if state["config"].get("no_limit_mode", False):
                    continue

                if state["online"] and not state["locked"]:
                    state["remaining_minutes"] = max(0, state["remaining_minutes"] - delta)
                
                # Fallback voor bedtijd
                bt_str = state["config"].get("bedtime") or "21:00"
                try:
                    bt = datetime.strptime(bt_str, "%H:%M").time()
                except:
                    bt = datetime.strptime("21:00", "%H:%M").time()
                
                is_bt = (now.time() > bt or now.time() < datetime.strptime("04:00", "%H:%M").time())
                
                if first_run_done and not state["manual_override"]:
                    if (state["remaining_minutes"] <= 0 or is_bt) and not state["locked"]:
                        try: requests.post(f"http://{state['config']['ip']}:8080/lock", timeout=5)
                        except: pass
        first_run_done = True
        time.sleep(30)

threading.Thread(target=monitor, daemon=True).start()

# --- Web Interface ---
app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    tvs_list = []
    with data_lock:
        for name, s in tv_states.items():
            tvs_list.append({
                "name": name,
                "online": s["online"],
                "remaining": int(s["remaining_minutes"]),
                "limit": s["config"].get("daily_limit") or 120,
                "bedtime": s["config"].get("bedtime") or "21:00",
                "locked": s["locked"]
            })
    # Stuur de lijst expliciet naar de template
    return templates.TemplateResponse("index.html", {"request": request, "tvs": tvs_list})

@app.post("/toggle_lock/{name}")
async def toggle(name: str):
    return RedirectResponse(url="./", status_code=303)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)