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
    conn.commit()
    conn.close()

init_db()

def log_event(tv_name, event_type, reason):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO events VALUES (?, ?, ?, ?)", (timestamp, tv_name, event_type, reason))
        conn.commit()
        conn.close()
        logger.info(f"Event: {tv_name} - {event_type} - {reason}")
    except Exception as e:
        logger.error(f"Database error: {e}")

def get_logs():
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT * FROM events ORDER BY timestamp DESC LIMIT 50")
        rows = c.fetchall()
        conn.close()
        return [{"timestamp": r[0], "tv_name": r[1], "event_type": r[2], "reason": r[3]} for r in rows]
    except Exception:
        return []

# Global State
data_lock = threading.RLock()
tv_states = {}
for tv in options.get("tvs", []):
    tv_states[tv["name"]] = {
        "config": tv,
        "online": False,
        "locked": False,
        "remaining_minutes": tv["daily_limit"],
        "manual_override": False
    }

# --- MQTT Setup ---
mqtt_client = mqtt.Client()

def on_connect(client, userdata, flags, rc):
    logger.info(f"Connected to MQTT with result code {rc}")
    for tv_name in tv_states:
        slug = tv_name.lower().replace(" ", "_")
        client.subscribe(f"kidslock/{slug}/set")
        publish_discovery(tv_name)

def on_message(client, userdata, msg):
    topic = msg.topic
    payload = msg.payload.decode()
    logger.info(f"MQTT Message: {topic} {payload}")
    
    with data_lock:
        for tv_name, state in tv_states.items():
            slug = tv_name.lower().replace(" ", "_")
            if topic == f"kidslock/{slug}/set":
                if payload == "ON": # Lock
                    control_tv(tv_name, "lock", "Manual MQTT Lock")
                    state["manual_override"] = True
                elif payload == "OFF": # Unlock
                    control_tv(tv_name, "unlock", "Manual MQTT Unlock")
                    state["manual_override"] = True

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

# Connect to HA internal broker
mqtt_host = os.getenv("MQTT_HOST", "core-mosquitto")
mqtt_port = int(os.getenv("MQTT_PORT", 1883))
mqtt_user = os.getenv("MQTT_USERNAME", "")
mqtt_pass = os.getenv("MQTT_PASSWORD", "")

if mqtt_user:
    mqtt_client.username_pw_set(mqtt_user, mqtt_pass)

try:
    mqtt_client.connect(mqtt_host, mqtt_port, 60)
    mqtt_client.loop_start()
except Exception as e:
    logger.error(f"MQTT Connection failed: {e}")

def publish_discovery(tv_name):
    slug = tv_name.lower().replace(" ", "_")
    
    # Switch (Lock)
    switch_config = {
        "name": f"{tv_name} KidsLock",
        "command_topic": f"kidslock/{slug}/set",
        "state_topic": f"kidslock/{slug}/state",
        "unique_id": f"kidslock_{slug}_switch",
        "device": {"identifiers": [f"kidslock_{slug}"], "name": tv_name, "manufacturer": "KidsLock"},
        "icon": "mdi:lock"
    }
    mqtt_client.publish(f"homeassistant/switch/kidslock_{slug}/config", json.dumps(switch_config), retain=True)

    # Sensor (Time Remaining)
    sensor_config = {
        "name": f"{tv_name} Remaining Time",
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
    
    # Switch State
    mqtt_client.publish(f"kidslock/{slug}/state", "ON" if state["locked"] else "OFF", retain=True)
    # Time State
    mqtt_client.publish(f"kidslock/{slug}/time", int(state["remaining_minutes"]), retain=True)

# --- TV Control Logic ---
def ping_tv(ip):
    response = os.system(f"ping -c 1 -W 1 {ip} > /dev/null 2>&1")
    return response == 0

def control_tv(tv_name, action, reason):
    state = tv_states[tv_name]
    ip = state["config"]["ip"]
    
    # Avoid redundant calls
    if action == "lock" and state["locked"]: return
    if action == "unlock" and not state["locked"]: return

    try:
        url = f"http://{ip}:8080/{action}"
        # Run in thread to avoid blocking
        threading.Thread(target=lambda: requests.post(url, timeout=5)).start()
        
        state["locked"] = (action == "lock")
        log_event(tv_name, action.upper(), reason)
        update_mqtt_state(tv_name)
        logger.info(f"TV {tv_name} {action}ed. Reason: {reason}")
    except Exception as e:
        logger.error(f"Failed to {action} {tv_name}: {e}")

# --- Background Monitor ---
def monitor_loop():
    last_day = datetime.now().day
    last_tick = time.time()
    
    while True:
        current_time = time.time()
        delta_minutes = (current_time - last_tick) / 60.0
        last_tick = current_time

        current_now = datetime.now()
        
        # Reset daily limits at midnight
        if current_now.day != last_day:
            logger.info("New day! Resetting timers.")
            with data_lock:
                for tv_name, state in tv_states.items():
                    state["remaining_minutes"] = state["config"]["daily_limit"]
                    state["manual_override"] = False # Reset overrides
                    update_mqtt_state(tv_name)
            last_day = current_now.day

        # Check TVs
        with data_lock:
            tv_names = list(tv_states.keys())

        for tv_name in tv_names:
            with data_lock:
                ip = tv_states[tv_name]["config"]["ip"]
            
            is_online = ping_tv(ip)
            
            with data_lock:
                state = tv_states[tv_name]
                state["online"] = is_online
                
                if is_online and not state["locked"]:
                    state["remaining_minutes"] = max(0, state["remaining_minutes"] - delta_minutes)
                
                # Check Bedtime
                bedtime_str = state["config"]["bedtime"]
                bedtime = datetime.strptime(bedtime_str, "%H:%M").time()
                now_time = current_now.time()
                
                is_bedtime = False
                if now_time > bedtime or now_time < datetime.strptime("04:00", "%H:%M").time():
                    is_bedtime = True
                
                time_up = state["remaining_minutes"] <= 0
                
                if not state["manual_override"]:
                    if (time_up or is_bedtime) and not state["locked"]:
                        reason = "Bedtime" if is_bedtime else "Time Limit Reached"
                        control_tv(tv_name, "lock", reason)
                    elif not time_up and not is_bedtime and state["locked"]:
                        control_tv(tv_name, "unlock", "Time Added / Morning")
                
                update_mqtt_state(tv_name)

        time.sleep(30)

# Start Monitor
threading.Thread(target=monitor_loop, daemon=True).start()

# --- FastAPI / Web Interface ---
app = FastAPI()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    
    # Prepare display data
    display_tvs = []
    with data_lock:
        for name, state in tv_states.items():
            display_tvs.append({
                "name": name,
                "online": state["online"],
                "locked": state["locked"],
                "remaining": int(state["remaining_minutes"]),
                "limit": state["config"]["daily_limit"],
                "bedtime": state["config"]["bedtime"]
            })

    logs = get_logs()
    return templates.TemplateResponse("index.html", {"request": request, "tvs": display_tvs, "logs": logs})

@app.post("/add_time/{tv_name}")
async def add_time(tv_name: str, minutes: int = Form(...)):
    with data_lock:
        if tv_name in tv_states:
            tv_states[tv_name]["remaining_minutes"] += minutes
            # If we add time, we might want to clear manual override to allow auto-unlock
            tv_states[tv_name]["manual_override"] = False
            
            # Force unlock immediately if it was locked due to time
            if tv_states[tv_name]["locked"]:
                 control_tv(tv_name, "unlock", f"Added {minutes} min")
            
            log_event(tv_name, "TIME_ADDED", f"Added {minutes} minutes via Dashboard")
            update_mqtt_state(tv_name)
    # Redirect back two levels because we are at /add_time/{name}
    return RedirectResponse(url="../../", status_code=303)

@app.post("/toggle_lock/{tv_name}")
async def toggle_lock(tv_name: str):
    with data_lock:
        if tv_name in tv_states:
            state = tv_states[tv_name]
            new_action = "unlock" if state["locked"] else "lock"
            control_tv(tv_name, new_action, "Dashboard Toggle")
            state["manual_override"] = True
    return RedirectResponse(url="../../", status_code=303)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
