import os
import json
import time
import sqlite3
import requests
import threading
import datetime
import paho.mqtt.client as mqtt
from flask import Flask, render_template, render_template_string, request, jsonify

# --- CONFIGURATIE & DB ---
DB_PATH = "/data/kidslock.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS devices 
                 (slug TEXT PRIMARY KEY, name TEXT, ip TEXT, 
                  manual_lock BOOLEAN, minutes_used INTEGER, 
                  last_reset TEXT, schedule TEXT)''')
    conn.commit()
    conn.close()

init_db()

def get_options():
    try:
        with open("/data/options.json", "r") as f:
            return json.load(f)
    except: return {}

options = get_options()
MQTT_HOST = options.get("mqtt_host", "core-mosquitto")
MQTT_PORT = options.get("mqtt_port", 1883)
MQTT_USER = options.get("mqtt_user")
MQTT_PASS = options.get("mqtt_password")

devices = {}

def load_devices():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM devices")
    rows = c.fetchall()
    for row in rows:
        devices[row[0]] = {
            "name": row[1], "ip": row[2], "manual_lock": bool(row[3]),
            "minutes_used": row[4], "last_reset": row[5],
            "schedule": json.loads(row[6]), "online": False, "locked": False
        }
    conn.close()

load_devices()

# --- MQTT SETUP ---
mqtt_client = mqtt.Client()

def update_mqtt_state(slug):
    """Update HA status op basis van manual_lock OF schema"""
    if slug not in devices: return
    s = devices[slug]
    now = datetime.datetime.now()
    day_name = now.strftime("%A")
    day_cfg = s["schedule"].get(day_name, {"limit": 60, "bedtime": "20:00"})
    
    is_bedtime = now.time() >= datetime.datetime.strptime(day_cfg["bedtime"], "%H:%M").time()
    is_over_limit = s["minutes_used"] >= int(day_cfg["limit"])
    
    # De effectieve lock status voor HA
    effective_lock = s["manual_lock"] or is_bedtime or is_over_limit
    state = "ON" if effective_lock else "OFF"
    
    mqtt_client.publish(f"kidslock/{slug}/state", state, retain=True)
    
    # Info sensor update
    if is_bedtime: info = "Bedtijd"
    elif effective_lock: info = "Slot"
    else: info = f"{max(0, int(day_cfg['limit']) - s['minutes_used'])} min"
    mqtt_client.publish(f"kidslock/{slug}/info", info, retain=True)

def on_connect(client, userdata, flags, rc):
    for slug in devices:
        client.subscribe(f"kidslock/{slug}/set")
        discovery_payload = {
            "name": f"KidsLock {devices[slug]['name']}",
            "state_topic": f"kidslock/{slug}/state",
            "command_topic": f"kidslock/{slug}/set",
            "unique_id": f"kidslock_{slug}_switch",
            "device": {"identifiers": ["kidslock_manager"], "name": "KidsLock Manager"}
        }
        client.publish(f"homeassistant/switch/kidslock_{slug}/config", json.dumps(discovery_payload), retain=True)
        update_mqtt_state(slug)

def on_message(client, userdata, msg):
    parts = msg.topic.split('/')
    if len(parts) < 2: return
    slug = parts[1]
    payload = msg.payload.decode()
    if slug in devices:
        is_on = (payload == "ON")
        devices[slug]["manual_lock"] = is_on
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE devices SET manual_lock = ? WHERE slug = ?", (is_on, slug))
        conn.commit()
        conn.close()
        update_mqtt_state(slug)

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
if MQTT_USER: mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)
mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
mqtt_client.loop_start()

# --- MONITOR LOOP ---
def monitor():
    while True:
        now = datetime.datetime.now()
        today_str = now.strftime("%Y-%m-%d")
        day_name = now.strftime("%A")

        for slug, s in devices.items():
            # 1. Reset check
            if s["last_reset"] != today_str:
                s["minutes_used"] = 0
                s["last_reset"] = today_str
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("UPDATE devices SET minutes_used = 0, last_reset = ? WHERE slug = ?", (today_str, slug))
                conn.commit()
                conn.close()

            # 2. Schema check
            day_cfg = s["schedule"].get(day_name, {"limit": 60, "bedtime": "20:00"})
            is_bedtime = now.time() >= datetime.datetime.strptime(day_cfg["bedtime"], "%H:%M").time()
            should_lock = s["manual_lock"] or is_bedtime or (s["minutes_used"] >= int(day_cfg["limit"]))

            # 3. TV API communicatie
            try:
                resp = requests.get(f"http://{s['ip']}:8080/status", timeout=2)
                s["online"] = True
                s["locked"] = resp.json().get("locked", False)
                
                if should_lock and not s["locked"]:
                    requests.get(f"http://{s['ip']}:8080/lock", timeout=2)
                elif not should_lock and s["locked"]:
                    requests.get(f"http://{s['ip']}:8080/unlock", timeout=2)
                
                if s["online"] and not s["locked"]:
                    s["minutes_used"] += 1
                    conn = sqlite3.connect(DB_PATH)
                    c = conn.cursor()
                    c.execute("UPDATE devices SET minutes_used = ? WHERE slug = ?", (s["minutes_used"], slug))
                    conn.commit()
                    conn.close()
            except:
                s["online"] = False

            update_mqtt_state(slug)
        time.sleep(60)

threading.Thread(target=monitor, daemon=True).start()

# --- FLASK SERVER ---
app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html', devices=devices)

@app.route('/api/toggle_lock/<slug>', methods=['POST'])
def toggle_lock(slug):
    if slug in devices:
        new_state = not devices[slug]["manual_lock"]
        devices[slug]["manual_lock"] = new_state
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE devices SET manual_lock = ? WHERE slug = ?", (new_state, slug))
        conn.commit()
        conn.close()
        update_mqtt_state(slug)
        return jsonify({"success": True, "locked": new_state})
    return jsonify({"error": "Device not found"}), 404

@app.route('/api/add_time/<slug>/<int:mins>', methods=['POST'])
def add_time(slug, mins):
    if slug in devices:
        devices[slug]["minutes_used"] = max(0, devices[slug]["minutes_used"] - mins)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE devices SET minutes_used = ? WHERE slug = ?", (devices[slug]["minutes_used"], slug))
        conn.commit()
        conn.close()
        update_mqtt_state(slug)
        return jsonify({"success": True})
    return jsonify({"error": "Device not found"}), 404

@app.route('/api/reset/<slug>', methods=['POST'])
def reset_device(slug):
    if slug in devices:
        devices[slug]["minutes_used"] = 0
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE devices SET minutes_used = 0 WHERE slug = ?", (slug,))
        conn.commit()
        conn.close()
        update_mqtt_state(slug)
        return jsonify({"success": True})
    return jsonify({"error": "Device not found"}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)