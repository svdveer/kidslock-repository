--- /addons/kidslock_manager/main.py
+++ /addons/kidslock_manager/main.py
@@ -0,0 +1,327 @@
+import json
+import logging
+import os
+import sqlite3
+import threading
+import time
+import subprocess
+from datetime import datetime, timedelta
+
+import requests
+import paho.mqtt.client as mqtt
+from fastapi import FastAPI, Request, Form
+from fastapi.responses import HTMLResponse, RedirectResponse
+from fastapi.templating import Jinja2Templates
+from fastapi.staticfiles import StaticFiles
+import uvicorn
+
+# --- Configuration ---
+OPTIONS_PATH = "/data/options.json"
+DB_PATH = "/data/kidslock.db"
+
+# Setup Logging
+logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
+logger = logging.getLogger("KidsLock")
+
+# Load Options
+try:
+    with open(OPTIONS_PATH, "r") as f:
+        options = json.load(f)
+except FileNotFoundError:
+    logger.error("Options file not found! Using defaults/empty.")
+    options = {"tvs": []}
+
+# Global State
+tv_states = {}
+for tv in options.get("tvs", []):
+    tv_states[tv["name"]] = {
+        "config": tv,
+        "online": False,
+        "locked": False,
+        "remaining_minutes": tv["daily_limit"],
+        "last_seen": None,
+        "manual_override": False # If true, auto-lock logic is paused until manual switch matches logic
+    }
+
+# --- Database ---
+def init_db():
+    conn = sqlite3.connect(DB_PATH)
+    c = conn.cursor()
+    c.execute('''CREATE TABLE IF NOT EXISTS events
+                 (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, tv_name TEXT, event_type TEXT, reason TEXT)''')
+    conn.commit()
+    conn.close()
+
+def log_event(tv_name, event_type, reason):
+    conn = sqlite3.connect(DB_PATH)
+    c = conn.cursor()
+    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
+    c.execute("INSERT INTO events (timestamp, tv_name, event_type, reason) VALUES (?, ?, ?, ?)",
+              (timestamp, tv_name, event_type, reason))
+    conn.commit()
+    conn.close()
+    logger.info(f"Event: {tv_name} - {event_type} ({reason})")
+
+# --- MQTT ---
+mqtt_client = mqtt.Client()
+
+def on_connect(client, userdata, flags, rc):
+    logger.info(f"Connected to MQTT with result code {rc}")
+    # Subscribe to command topics for all TVs
+    for tv_name in tv_states:
+        slug = tv_name.lower().replace(" ", "_")
+        client.subscribe(f"kidslock/{slug}/set")
+        publish_discovery(tv_name)
+
+def on_message(client, userdata, msg):
+    topic = msg.topic
+    payload = msg.payload.decode()
+    logger.info(f"MQTT Message: {topic} {payload}")
+    
+    # Parse topic to find TV
+    for tv_name, state in tv_states.items():
+        slug = tv_name.lower().replace(" ", "_")
+        if topic == f"kidslock/{slug}/set":
+            if payload == "ON":
+                control_tv(tv_name, "lock", "Manual MQTT Lock")
+                state["manual_override"] = True
+            elif payload == "OFF":
+                control_tv(tv_name, "unlock", "Manual MQTT Unlock")
+                state["manual_override"] = True
+
+def publish_discovery(tv_name):
+    slug = tv_name.lower().replace(" ", "_")
+    
+    # Switch Discovery
+    switch_config = {
+        "name": f"KidsLock {tv_name}",
+        "unique_id": f"kidslock_switch_{slug}",
+        "command_topic": f"kidslock/{slug}/set",
+        "state_topic": f"kidslock/{slug}/state",
+        "icon": "mdi:lock"
+    }
+    mqtt_client.publish(f"homeassistant/switch/kidslock_{slug}/config", json.dumps(switch_config), retain=True)
+
+    # Sensor Discovery (Remaining Time)
+    sensor_config = {
+        "name": f"KidsLock {tv_name} Time",
+        "unique_id": f"kidslock_sensor_{slug}",
+        "state_topic": f"kidslock/{slug}/time",
+        "unit_of_measurement": "min",
+        "icon": "mdi:timer-sand"
+    }
+    mqtt_client.publish(f"homeassistant/sensor/kidslock_{slug}_time/config", json.dumps(sensor_config), retain=True)
+
+def update_mqtt_state(tv_name):
+    slug = tv_name.lower().replace(" ", "_")
+    state = tv_states[tv_name]
+    
+    # Publish Switch State
+    mqtt_client.publish(f"kidslock/{slug}/state", "ON" if state["locked"] else "OFF", retain=True)
+    # Publish Time
+    mqtt_client.publish(f"kidslock/{slug}/time", str(state["remaining_minutes"]), retain=True)
+
+# --- TV Control ---
+def control_tv(tv_name, action, reason):
+    state = tv_states[tv_name]
+    ip = state["config"]["ip"]
+    
+    # Only send request if state actually changes or forced
+    if action == "lock" and not state["locked"]:
+        try:
+            requests.post(f"http://{ip}:8080/lock", timeout=2)
+            state["locked"] = True
+            log_event(tv_name, "LOCKED", reason)
+        except Exception as e:
+            logger.error(f"Failed to lock {tv_name}: {e}")
+            
+    elif action == "unlock" and state["locked"]:
+        try:
+            requests.post(f"http://{ip}:8080/unlock", timeout=2)
+            state["locked"] = False
+            log_event(tv_name, "UNLOCKED", reason)
+        except Exception as e:
+            logger.error(f"Failed to unlock {tv_name}: {e}")
+            
+    update_mqtt_state(tv_name)
+
+def ping_tv(ip):
+    try:
+        # Ping with 1 packet, 1 second timeout
+        response = subprocess.call(['ping', '-c', '1', '-W', '1', ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
+        return response == 0
+    except Exception:
+        return False
+
+# --- Background Monitor ---
+def monitor_loop():
+    last_day = datetime.now().day
+    
+    while True:
+        current_now = datetime.now()
+        
+        # Reset daily limits at midnight
+        if current_now.day != last_day:
+            logger.info("New day! Resetting timers.")
+            for tv_name, state in tv_states.items():
+                state["remaining_minutes"] = state["config"]["daily_limit"]
+                state["manual_override"] = False # Reset overrides
+                update_mqtt_state(tv_name)
+            last_day = current_now.day
+
+        for tv_name, state in tv_states.items():
+            ip = state["config"]["ip"]
+            is_online = ping_tv(ip)
+            state["online"] = is_online
+            
+            if is_online:
+                # Decrement time (running every 30s, so deduct 0.5 min)
+                # To keep it integer based, we deduct 1 min every 2 cycles or use float.
+                # Let's use float for internal tracking but display int.
+                if not state["locked"]:
+                    state["remaining_minutes"] = max(0, state["remaining_minutes"] - 0.5)
+            
+            # Check Bedtime
+            bedtime_str = state["config"]["bedtime"]
+            bedtime = datetime.strptime(bedtime_str, "%H:%M").time()
+            now_time = current_now.time()
+            
+            # Logic: Lock if time is up OR it is past bedtime
+            # Simple bedtime logic: if now > bedtime and now < 04:00 (assuming kids sleep before 4am)
+            is_bedtime = False
+            if now_time > bedtime or now_time < datetime.strptime("04:00", "%H:%M").time():
+                is_bedtime = True
+            
+            time_up = state["remaining_minutes"] <= 0
+            
+            if not state["manual_override"]:
+                if (time_up or is_bedtime) and not state["locked"]:
+                    reason = "Bedtime" if is_bedtime else "Time Limit Reached"
+                    control_tv(tv_name, "lock", reason)
+                elif not time_up and not is_bedtime and state["locked"]:
+                    # Auto unlock if conditions clear (e.g. time added)
+                    control_tv(tv_name, "unlock", "Time Added / Morning")
+            
+            update_mqtt_state(tv_name)
+
+        time.sleep(30)
+
+# --- FastAPI / Web Interface ---
+app = FastAPI()
+templates = Jinja2Templates(directory="templates")
+
+@app.get("/", response_class=HTMLResponse)
+async def read_root(request: Request):
+    # Get logs
+    conn = sqlite3.connect(DB_PATH)
+    conn.row_factory = sqlite3.Row
+    c = conn.cursor()
+    c.execute("SELECT * FROM events ORDER BY id DESC LIMIT 50")
+    logs = c.fetchall()
+    conn.close()
+    
+    # Prepare display data
+    display_tvs = []
+    for name, state in tv_states.items():
+        display_tvs.append({
+            "name": name,
+            "online": state["online"],
+            "locked": state["locked"],
+            "remaining": int(state["remaining_minutes"]),
+            "limit": state["config"]["daily_limit"],
+            "bedtime": state["config"]["bedtime"]
+        })
+
+    return templates.TemplateResponse("index.html", {"request": request, "tvs": display_tvs, "logs": logs})
+
+@app.post("/add_time/{tv_name}")
+async def add_time(tv_name: str, minutes: int = Form(...)):
+    if tv_name in tv_states:
+        tv_states[tv_name]["remaining_minutes"] += minutes
+        # If we add time, we might want to clear manual override to allow auto-unlock
+        tv_states[tv_name]["manual_override"] = False
+        # Trigger immediate state check/update would be good, but loop will catch it in <30s
+        # Or force unlock immediately if it was locked due to time
+        if tv_states[tv_name]["locked"]:
+             control_tv(tv_name, "unlock", f"Added {minutes} min")
+        
+        log_event(tv_name, "TIME_ADDED", f"Added {minutes} minutes via Dashboard")
+        update_mqtt_state(tv_name)
+    return RedirectResponse(url="./", status_code=303)
+
+@app.post("/toggle_lock/{tv_name}")
+async def toggle_lock(tv_name: str):
+    if tv_name in tv_states:
+        state = tv_states[tv_name]
+        new_action = "unlock" if state["locked"] else "lock"
+        control_tv(tv_name, new_action, "Dashboard Toggle")
+        state["manual_override"] = True
+    return RedirectResponse(url="./", status_code=303)
+
+# --- Startup ---
+if __name__ == "__main__":
+    init_db()
+    
+    # Start MQTT
+    if options.get("mqtt_host"):
+        mqtt_client.username_pw_set(options["mqtt_user"], options["mqtt_password"])
+        try:
+            mqtt_client.connect(options["mqtt_host"], options["mqtt_port"], 60)
+            mqtt_client.loop_start()
+        except Exception as e:
+            logger.error(f"MQTT Connection failed: {e}")
+
+    # Start Monitor Thread
+    t = threading.Thread(target=monitor_loop, daemon=True)
+    t.start()
+
+    # Start Web Server
+    uvicorn.run(app, host="0.0.0.0", port=8000)
