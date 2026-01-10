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

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KidsLock")

# --- Configuration ---
OPTIONS_PATH = "/data/options.json"
DB_PATH = "/data/kidslock.db"

# Laden van opties uit HA Config
if os.path.exists(OPTIONS_PATH):
    with open(OPTIONS_PATH, "r") as f:
        options = json.load(f)
else:
    options = {"tvs": []}

# --- Database (SQLite) ---
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

def log_event(tv_name, event_type, reason):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO events VALUES (?, ?, ?, ?)", (timestamp, tv_name, event_type, reason))
        conn.commit()
        conn.close()
        logger.info(f"Event: {tv_name} - {event_type} - {reason}")
    except Exception as e:
        logger.error(f"Database error: {e}")

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
        logger.error(f"DB Save Error: {e}")

def load_state(tv_name, daily_limit):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        c.execute("SELECT remaining_minutes, last_update FROM tv_state WHERE tv_name=?", (tv_name,))
        row = c.fetchone()
        conn.close()
        if row:
            saved_minutes, last_update = row
            if last_update == datetime.now().strftime("%Y-%m-%d"):
                return float(saved_minutes)
    except Exception as e:
        logger.error(f"DB Load Error: {e}")
    return float(daily_limit)

# --- Global State Initialization ---
data_lock = threading.RLock()
tv_states = {}
for tv in options.get("tvs", []):
    saved_time = load_state(tv["name"], tv["daily_limit"])
    tv_states[tv["name"]] = {
        "config": tv,
        "online": False,
        "locked": False,
        "remaining_minutes": saved_time,
        "manual_override": False
    }

# --- MQTT Setup ---
mqtt_client = mqtt.Client()

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("Verbonden met MQTT Broker!")
        for tv_name in tv_states:
            publish_discovery(tv_name)
            update_mqtt_state(tv_name)
            # Subscribe op command topic
            slug = tv_name.lower().replace(" ", "_")
            client.subscribe(f"kidslock/{slug}/set")
    else:
        logger.error(f"MQTT Connectie mislukt met code {rc}")

def on_message(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload.decode()
    
    with data_lock:
        for tv_name, state in tv_states.items():
            slug = tv_name.lower().replace(" ", "_")
            if topic == f"kidslock/{slug}/set":
                if payload == "ON":
                    control_tv(tv_name, "lock", "MQTT Remote")
                    state["manual_override"] = True
                elif payload == "OFF":
                    control_tv(tv_name, "unlock", "MQTT Remote")
                    state["manual_override"] = True

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

# Gebruik de interne HA MQTT broker
try:
    mqtt_client.connect("core-mosquitto", 1883, 60)
    mqtt_client.loop_start()
except Exception as e:
    logger.error(f"MQTT Initialisatie fout: {e}")

def publish_discovery(tv_name):
    slug = tv_name.lower().replace(" ", "_")
    
    # Discovery Switch
    switch_config = {
        "name": f"{tv_name} Lock",
        "command_topic": f"kidslock/{slug}/set",
        "state_topic": f"kidslock/{slug}/state",
        "unique_id": f"kidslock_{slug}_switch",
        "device": {"identifiers": [f"kidslock_{slug}"], "name": tv_name, "manufacturer": "KidsLock"},
        "icon": "mdi:lock"
    }
    mqtt_client.publish(f"homeassistant/switch/kidslock_{slug}/config", json.dumps(switch_config), retain=True)

    # Discovery Sensor (Tijd over)
    sensor_config = {
        "name": f"{tv_name} Tijd over",
        "state_topic": f"kidslock/{slug}/time",
        "unit_of_measurement": "min",
        "unique_id": f"kidslock_{slug}_time",
        "device": {"identifiers": [f"kidslock_{slug}"]},
        "icon": "mdi:timer-sand"
    }
    mqtt_client.publish(f"homeassistant/sensor/kidslock_{slug}_time/config", json.dumps(sensor_config), retain=True)

def update_mqtt_state(tv_name):
    slug = tv_name.lower().replace(" ", "_")
    state = tv_states[tv_name]
    mqtt_client.publish(f"kidslock/{slug}/state", "ON" if state["locked"] else "OFF", retain=True)
    mqtt_client.publish(f"kidslock/{slug}/time", int(state["remaining_minutes"]), retain=True)

# --- TV Control Logic ---
def ping_tv(ip):
    # -c 1 (1 packet), -W 1 (1 sec timeout)
    response = os.system(f"ping -c 1 -W 1 {ip} > /dev/null 2>&1")
    return response == 0

def control_tv(tv_name, action, reason):
    state = tv_states[tv_name]
    ip = state["config"]["ip"]
    
    if action == "lock" and state["locked"]: return
    if action == "unlock" and not state["locked"]: return

    def send_request():
        try:
            url = f"http://{ip}:8080/{action}"
            requests.post(url, timeout=5)
            logger.info(f"TV {tv_name} succesvol ge-{action}ed")
        except Exception as e:
            logger.error(f"HTTP Fout bij {tv_name}: {e}")

    threading.Thread(target=send_request, daemon=True).start()
    state["locked"] = (action == "lock")
    log_event(tv_name, action.upper(), reason)
    update_mqtt_state(tv_name)

# --- Background Monitor ---
def monitor_loop():
    last_day = datetime.now().day
    last_tick = time.time()
    
    while True:
        current_time = time.time()
        delta_minutes = (current_time - last_tick) / 60.0
        last_tick = current_time
        current_now = datetime.now()
        
        # Reset om middernacht
        if current_now.day != last_day:
            with data_lock:
                for tv_name, state in tv_states.items():
                    state["remaining_minutes"] = state["config"]["daily_limit"]
                    state["manual_override"] = False
                    update_mqtt_state(tv_name)
            last_day = current_now.day

        # Check alle TV's
        with data_lock:
            for tv_name, state in tv_states.items():
                is_online = ping_tv(state["config"]["ip"])
                state["online"] = is_online
                
                # Tijd aftrekken indien TV aan is en NO LIMIT uit staat
                if is_online and not state["locked"]:
                    if not state["config"].get("no_limit_mode", False):
                        state["remaining_minutes"] = max(0, state["remaining_minutes"] - delta_minutes)
                        save_state(tv_name, state["remaining_minutes"])
                
                # Check Bedtijd
                bedtime_str = state["config"].get("bedtime", "20:00")
                bedtime = datetime.strptime(bedtime_str, "%H:%M").time()
                now_time = current_now.time()
                
                is_bedtime = (now_time > bedtime or now_time < datetime.strptime("04:00", "%H:%M").time())
                time_up = state["remaining_minutes"] <= 0
                
                if not state["manual_override"]:
                    if (time_up or is_bedtime) and not state["locked"]:
                        control_tv(tv_name, "lock", "Bedtijd" if is_bedtime else "Limiet bereikt")
                    elif not time_up and not is_bedtime and state["locked"]:
                        control_tv(tv_name, "unlock", "Nieuwe tijd/Ochtend")
                
                update_mqtt_state(tv_name)

        time.sleep(30)

# Start Monitor Thread
threading.Thread(target=monitor_loop, daemon=True).start()

# --- FastAPI / Ingress ---
app = FastAPI()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    display_tvs = []
    with data_lock:
        for name, state in tv_states.items():
            display_tvs.append({
                "name": name,
                "online": state["online"],
                "locked": state["locked"],
                "remaining": int(state["remaining_minutes"]),
                "limit": state["config"]["daily_limit"],
                "bedtime": state["config"].get("bedtime", "N/B"),
                "no_limit": state["config"].get("no_limit_mode", False)
            })
    return templates.TemplateResponse("index.html", {"request": request, "tvs": display_tvs})

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)