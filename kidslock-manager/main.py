import logging
import threading
import time
import json
import os
import sqlite3
import requests
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import uvicorn
import paho.mqtt.client as mqtt

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KidsLock")

# --- Configuration & Paths ---
OPTIONS_PATH = "/data/options.json"
DB_PATH = "/data/kidslock.db"

if os.path.exists(OPTIONS_PATH):
    with open(OPTIONS_PATH, "r") as f:
        options = json.load(f)
else:
    options = {"tvs": [], "mqtt": {}}

# --- Database ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS events
                 (timestamp TEXT, tv_name TEXT, event_type TEXT, reason TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS tv_state
                 (tv_name TEXT PRIMARY KEY, remaining_minutes REAL, last_update TEXT)''')
    conn.commit()
    conn.close()

init_db()

def save_state(tv_name, minutes):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        today = datetime.now().strftime("%Y-%m-%d")
        c.execute("INSERT OR REPLACE INTO tv_state (tv_name, remaining_minutes, last_update) VALUES (?, ?, ?)", 
                  (tv_name, minutes, today))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DB Opslagfout: {e}")

def load_state(tv_name, daily_limit):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        c.execute("SELECT remaining_minutes, last_update FROM tv_state WHERE tv_name=?", (tv_name,))
        row = c.fetchone()
        conn.close()
        if row and row[1] == datetime.now().strftime("%Y-%m-%d"):
            return float(row[0])
    except Exception: pass
    return float(daily_limit)

# --- Global State ---
data_lock = threading.RLock()
tv_states = {}
first_run_done = False 

for tv in options.get("tvs", []):
    limit = tv.get("daily_limit", 120)
    saved_time = load_state(tv["name"], limit)
    tv_states[tv["name"]] = {
        "config": tv,
        "online": False,
        "locked": False,
        "remaining_minutes": saved_time,
        "manual_override": False
    }

# --- MQTT Setup ---
mqtt_conf = options.get("mqtt", {})
mqtt_client = mqtt.Client()

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("✅ MQTT Verbonden!")
        for tv_name in tv_states:
            publish_discovery(tv_name)
            update_mqtt_state(tv_name)
            slug = tv_name.lower().replace(" ", "_")
            client.subscribe(f"kidslock/{slug}/set")
    else:
        logger.error(f"❌ MQTT Fout {rc}")

mqtt_client.on_connect = on_connect

if mqtt_conf.get("username"):
    mqtt_client.username_pw_set(mqtt_conf["username"], mqtt_conf.get("password"))

try:
    mqtt_client.connect(mqtt_conf.get("host", "core-mosquitto"), mqtt_conf.get("port", 1883), 60)
    mqtt_client.loop_start()
except Exception as e:
    logger.error(f"MQTT Fout: {e}")

def publish_discovery(tv_name):
    slug = tv_name.lower().replace(" ", "_")
    device = {"identifiers": [f"kidslock_{slug}"], "name": tv_name, "manufacturer": "KidsLock"}
    mqtt_client.publish(f"homeassistant/switch/kidslock_{slug}/config", json.dumps({
        "name": f"{tv_name} Lock", "command_topic": f"kidslock/{slug}/set",
        "state_topic": f"kidslock/{slug}/state", "unique_id": f"kidslock_{slug}_switch",
        "device": device, "icon": "mdi:lock"
    }), retain=True)
    mqtt_client.publish(f"homeassistant/sensor/kidslock_{slug}_time/config", json.dumps({
        "name": f"{tv_name} Tijd over", "state_topic": f"kidslock/{slug}/time",
        "unit_of_measurement": "min", "unique_id": f"kidslock_{slug}_time",
        "device": device, "icon": "mdi:timer-sand"
    }), retain=True)

def update_mqtt_state(tv_name):
    slug = tv_name.lower().replace(" ", "_")
    state = tv_states[tv_name]
    mqtt_client.publish(f"kidslock/{slug}/state", "ON" if state["locked"] else "OFF", retain=True)
    mqtt_client.publish(f"kidslock/{slug}/time", int(state["remaining_minutes"]), retain=True)

# --- Control ---
def control_tv(tv_name, action, reason):
    state = tv_states[tv_name]
    ip = state["config"]["ip"]
    if (action == "lock" and state["locked"]) or (action == "unlock" and not state["locked"]): return

    def req():
        try:
            requests.post(f"http://{ip}:8080/{action}", timeout=5)
            logger.info(f"📺 {tv_name} -> {action} ({reason})")
        except: logger.error(f"⚠️ {tv_name} onbereikbaar")

    threading.Thread(target=req, daemon=True).start()
    state["locked"] = (action == "lock")
    update_mqtt_state(tv_name)

# --- Monitor ---
def monitor_loop():
    global first_run_done
    last_day = datetime.now().day
    last_tick = time.time()
    
    # Wacht 10 sec zodat Supervisor/MQTT stabiel zijn
    time.sleep(10)
    
    while True:
        delta = (time.time() - last_tick) / 60.0
        last_tick = time.time()
        now = datetime.now()
        
        # Dagelijkse Reset
        if now.day != last_day:
            with data_lock:
                for n, s in tv_states.items():
                    s["remaining_minutes"] = s["config"].get("daily_limit", 120)
                    s["manual_override"] = False
                    update_mqtt_state(n)
            last_day = now.day

        with data_lock:
            for name, state in tv_states.items():
                is_online = (os.system(f"ping -c 1 -W 1 {state['config']['ip']} > /dev/null 2>&1") == 0)
                state["online"] = is_online
                
                # --- NO LIMIT MODE CHECK ---
                if state["config"].get("no_limit_mode", False):
                    # Als onbeperkt aan staat, forceren we een unlock (tenzij handmatig overschreven)
                    if state["locked"] and not state["manual_override"]:
                        control_tv(name, "unlock", "Onbeperkte modus actief")
                    update_mqtt_state(name)
                    continue # Sla tijd-aftrek en bedtijd over voor deze TV

                # --- NORMALE LOGICA ---
                if is_online and not state["locked"]:
                    state["remaining_minutes"] = max(0, state["remaining_minutes"] - delta)
                    save_state(name, state["remaining_minutes"])

                b_str = str(state["config"].get("bedtime", "20:00"))
                try: bt = datetime.strptime(b_str if ":" in b_str else "20:00", "%H:%M").time()
                except: bt = datetime.strptime("20:00", "%H:%M").time()

                is_bt = (now.time() > bt or now.time() < datetime.strptime("04:00", "%H:%M").time())
                time_up = state["remaining_minutes"] <= 0
                
                if not state["manual_override"] and first_run_done:
                    if (time_up or is_bt) and not state["locked"]:
                        control_tv(name, "lock", "Tijd op/Bedtijd")
                    elif not time_up and not is_bt and state["locked"]:
                        control_tv(name, "unlock", "Vrijgave")
        
        first_run_done = True
        time.sleep(30)

threading.Thread(target=monitor_loop, daemon=True).start()

# --- FastAPI ---
app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    tvs = []
    with data_lock:
        for n, s in tv_states.items():
            tvs.append({
                "name": n, "online": s["online"], "locked": s["locked"],
                "remaining": int(s["remaining_minutes"]), 
                "limit": s["config"].get("daily_limit", 120),
                "bedtime": s["config"].get("bedtime", "20:00"),
                "no_limit": s["config"].get("no_limit_mode", False)
            })
    return templates.TemplateResponse("index.html", {"request": request, "tvs": tvs})

@app.post("/add_time/{tv_name}")
async def add_time(tv_name: str, minutes: int = Form(...)):
    with data_lock:
        if tv_name in tv_states:
            tv_states[tv_name]["remaining_minutes"] += minutes
            tv_states[tv_name]["manual_override"] = False
            if tv_states[tv_name]["locked"]: control_tv(tv_name, "unlock", "Extra tijd")
    return RedirectResponse(url="./", status_code=303)

@app.post("/toggle_lock/{tv_name}")
async def toggle_lock(tv_name: str):
    with data_lock:
        if tv_name in tv_states:
            action = "unlock" if tv_states[tv_name]["locked"] else "lock"
            control_tv(tv_name, action, "Dashboard")
            tv_states[tv_name]["manual_override"] = True
    return RedirectResponse(url="./", status_code=303)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)