import logging
import threading
import time
import json
import os
import sqlite3
import requests
import subprocess
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import uvicorn
import paho.mqtt.client as mqtt

# --- Initialisatie & Database ---
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

# Opties laden met veilige fallbacks
try:
    if os.path.exists(OPTIONS_PATH):
        with open(OPTIONS_PATH, "r") as f:
            options = json.load(f)
    else: options = {"tvs": [], "mqtt": {}}
except: options = {"tvs": [], "mqtt": {}}

# --- Functies ---
def is_online(ip):
    try:
        res = subprocess.run(['ping', '-c', '1', '-W', '1', str(ip)], 
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0
    except: return False

data_lock = threading.RLock()
tv_states = {}
first_run_done = False

for tv in options.get("tvs", []):
    limit = tv.get("daily_limit") if tv.get("daily_limit") is not None else 120
    tv_states[tv["name"]] = {
        "config": tv, "online": False, "locked": False,
        "remaining_minutes": float(limit), "manual_override": False
    }

# --- MQTT ---
mqtt_conf = options.get("mqtt", {})
mqtt_client = mqtt.Client()

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        for name in tv_states:
            slug = name.lower().replace(" ", "_")
            client.publish(f"homeassistant/switch/kidslock_{slug}/config", json.dumps({
                "name": f"{name} Lock", "command_topic": f"kidslock/{slug}/set",
                "state_topic": f"kidslock/{slug}/state", "unique_id": f"kidslock_{slug}_switch"
            }), retain=True)
            client.subscribe(f"kidslock/{slug}/set")

mqtt_client.on_connect = on_connect
if mqtt_conf.get("username"):
    mqtt_client.username_pw_set(mqtt_conf["username"], mqtt_conf.get("password"))

try:
    mqtt_client.connect(mqtt_conf.get("host", "core-mosquitto"), mqtt_conf.get("port", 1883))
    mqtt_client.loop_start()
except: pass

# --- Monitor Loop ---
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
                
                # Onbeperkt modus check
                if state["config"].get("no_limit_mode", False):
                    if state["locked"] and not state["manual_override"]:
                        try: requests.post(f"http://{state['config']['ip']}:8080/unlock", timeout=2)
                        except: pass
                        state["locked"] = False
                    continue

                # Tijd aftrek alleen als TV online is
                if state["online"] and not state["locked"]:
                    state["remaining_minutes"] = max(0, state["remaining_minutes"] - delta)
                
                # Bedtijd check
                bt_str = state["config"].get("bedtime") or "21:00"
                try: bt = datetime.strptime(bt_str, "%H:%M").time()
                except: bt = datetime.strptime("21:00", "%H:%M").time()
                is_bt = (now.time() > bt or now.time() < datetime.strptime("04:00", "%H:%M").time())
                
                if first_run_done and not state["manual_override"]:
                    if (state["remaining_minutes"] <= 0 or is_bt) and not state["locked"]:
                        try: requests.post(f"http://{state['config']['ip']}:8080/lock", timeout=2)
                        except: pass
                        state["locked"] = True
        first_run_done = True
        time.sleep(30)

threading.Thread(target=monitor, daemon=True).start()

# --- FastAPI Webserver ---
app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    tvs_display = []
    with data_lock:
        for name, s in tv_states.items():
            is_unlimited = s["config"].get("no_limit_mode", False)
            tvs_display.append({
                "name": name,
                "online": s["online"],
                "remaining": "∞" if is_unlimited else int(s["remaining_minutes"]),
                "limit": s["config"].get("daily_limit") or 120,
                "bedtime": s["config"].get("bedtime") or "21:00",
                "locked": s["locked"],
                "status_msg": "ONBEPERKT" if is_unlimited else ("Online" if s["online"] else "Uit"),
                "no_limit": is_unlimited
            })
    return templates.TemplateResponse("index.html", {"request": request, "tvs": tvs_display})

# DEFINITIEVE FIX VOOR INGRESS REDIRECT
@app.post("/toggle_lock/{name}")
async def toggle(name: str):
    with data_lock:
        if name in tv_states:
            action = "unlock" if tv_states[name]["locked"] else "lock"
            ip = tv_states[name]["config"]["ip"]
            try:
                # Verzend actie naar Android TV
                requests.post(f"http://{ip}:8080/{action}", timeout=2)
                tv_states[name]["locked"] = not tv_states[name]["locked"]
                tv_states[name]["manual_override"] = True
                logger.info(f"Handmatige {action} uitgevoerd voor {name}")
            except Exception as e:
                logger.error(f"Communicatiefout met TV {name}: {e}")

    # Redirect naar ./ is essentieel om binnen het Ingress pad te blijven
    return RedirectResponse(url="./", status_code=303)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)