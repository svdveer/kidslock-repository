import os
import json
import time
import sqlite3
import requests
import threading
import datetime
import paho.mqtt.client as mqtt
from flask import Flask, render_template_string, request, jsonify

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
    devices.clear()
    for row in rows:
        devices[row[0]] = {
            "name": row[1], "ip": row[2], "manual_lock": bool(row[3]),
            "minutes_used": row[4], "last_reset": row[5],
            "schedule": json.loads(row[6]), "online": False, "locked": False
        }
    conn.close()

load_devices()

# --- HULPFUNCTIES ---

def safe_tv_request(slug, action):
    if slug not in devices: return
    try:
        url = f"http://{devices[slug]['ip']}:8080/{action}"
        requests.get(url, timeout=1.5)
    except:
        pass

def update_mqtt_state(slug):
    if slug not in devices: return
    s = devices[slug]
    now = datetime.datetime.now()
    day_name = now.strftime("%A")
    day_cfg = s["schedule"].get(day_name, {"limit": 60, "bedtime": "20:00"})
    
    is_bedtime = now.time() >= datetime.datetime.strptime(day_cfg["bedtime"], "%H:%M").time()
    is_over_limit = s["minutes_used"] >= int(day_cfg["limit"])
    
    effective_lock = s["manual_lock"] or is_bedtime or is_over_limit
    state = "ON" if effective_lock else "OFF"
    
    mqtt_client.publish(f"kidslock/{slug}/state", state, retain=True)
    
    resterend = max(0, int(day_cfg['limit']) - s['minutes_used'])
    info = "Bedtijd" if is_bedtime else ("Slot" if effective_lock else f"{resterend} min")
    mqtt_client.publish(f"kidslock/{slug}/info", info, retain=True)

# --- MQTT SETUP ---
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

def on_connect(client, userdata, flags, rc, properties=None):
    print("INFO: KidsLock MQTT v1.7.0.5 Verbonden")
    for slug in devices:
        client.subscribe(f"kidslock/{slug}/set")
        update_mqtt_state(slug)

def on_message(client, userdata, msg):
    parts = msg.topic.split('/')
    if len(parts) < 2: return
    slug = parts[1]
    payload = msg.payload.decode()
    if slug in devices:
        is_on = (payload == "ON")
        devices[slug]["manual_lock"] = is_on
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("UPDATE devices SET manual_lock = ? WHERE slug = ?", (is_on, slug))
        conn.commit(); conn.close()
        update_mqtt_state(slug)
        threading.Thread(target=safe_tv_request, args=(slug, "lock" if is_on else "unlock")).start()

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
            if s["last_reset"] != today_str:
                s["minutes_used"] = 0
                s["last_reset"] = today_str
                conn = sqlite3.connect(DB_PATH); c = conn.cursor()
                c.execute("UPDATE devices SET minutes_used = 0, last_reset = ? WHERE slug = ?", (today_str, slug))
                conn.commit(); conn.close()

            day_cfg = s["schedule"].get(day_name, {"limit": 60, "bedtime": "20:00"})
            is_bedtime = now.time() >= datetime.datetime.strptime(day_cfg["bedtime"], "%H:%M").time()
            should_lock = s["manual_lock"] or is_bedtime or (s["minutes_used"] >= int(day_cfg["limit"]))

            try:
                resp = requests.get(f"http://{s['ip']}:8080/status", timeout=1.5)
                s["online"] = True
                tv_locked = resp.json().get("locked", False)
                
                if should_lock and not tv_locked:
                    safe_tv_request(slug, "lock")
                elif not should_lock and tv_locked:
                    safe_tv_request(slug, "unlock")
                
                if s["online"] and not tv_locked:
                    s["minutes_used"] += 1
                    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
                    c.execute("UPDATE devices SET minutes_used = ? WHERE slug = ?", (s["minutes_used"], slug))
                    conn.commit(); conn.close()
            except:
                s["online"] = False

            update_mqtt_state(slug)
        time.sleep(60)

threading.Thread(target=monitor, daemon=True).start()

# --- FLASK ---
app = Flask(__name__)

INDEX_HTML = """
<!DOCTYPE html>
<html lang="nl">
<head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KidsLock Dashboard</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #f8f9fa; padding: 20px; }
        .card { border-radius: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); margin-bottom: 20px; }
        .timer-display { font-size: 2rem; font-weight: bold; color: #007bff; }
    </style>
</head>
<body>
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-4">
            <h1>🔐 KidsLock Manager</h1>
            <a href="/settings" class="btn btn-outline-secondary">Instellingen</a>
        </div>
        <div class="row">
            {% for slug, s in devices.items() %}
            <div class="col-md-6 col-lg-4">
                <div class="card p-3 text-center">
                    <h3>{{ s.name }}</h3>
                    <div class="timer-display mb-3">{{ s.minutes_used }} min</div>
                    <button id="btn-{{ slug }}" onclick="toggleLock('{{ slug }}')" 
                            class="btn {{ 'btn-danger' if s.manual_lock else 'btn-success' }} w-100 mb-2">
                        {{ 'Ontgrendelen' if s.manual_lock else 'Vergrendelen' }}
                    </button>
                    <div class="row g-2">
                        <div class="col-6"><button onclick="addTime('{{ slug }}', 15)" class="btn btn-outline-primary btn-sm w-100">+15m</button></div>
                        <div class="col-6"><button onclick="resetTime('{{ slug }}')" class="btn btn-outline-warning btn-sm w-100">Reset</button></div>
                    </div>
                </div>
            </div>
            {% endfor %}
        </div>
    </div>
    <script>
        function toggleLock(slug) {
            fetch(`/api/toggle_lock/${slug}`, { method: 'POST' })
                .then(res => res.json()).then(data => {
                    const btn = document.getElementById(`btn-${slug}`);
                    btn.innerText = data.locked ? "Ontgrendelen" : "Vergrendelen";
                    btn.className = data.locked ? "btn btn-danger w-100 mb-2" : "btn btn-success w-100 mb-2";
                });
        }
        function addTime(slug, mins) { fetch(`/api/add_time/${slug}/${mins}`, { method: 'POST' }).then(() => location.reload()); }
        function resetTime(slug) { if(confirm("Resetten?")) fetch(`/api/reset/${slug}`, { method: 'POST' }).then(() => location.reload()); }
    </script>
</body>
</html>
"""

SETTINGS_HTML = """
<!DOCTYPE html>
<html><head><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="container p-4">
    <h1>Instellingen</h1>
    <form action="/api/add_device" method="POST" class="card p-3 mb-4">
        <input type="text" name="name" class="form-control mb-2" placeholder="Naam (bijv. Amy)" required>
        <input type="text" name="ip" class="form-control mb-2" placeholder="IP Adres" required>
        <button type="submit" class="btn btn-primary">Toevoegen</button>
    </form>
    <a href="/" class="btn btn-secondary">Terug</a>
</body></html>
"""

@app.route('/')
def index():
    load_devices()
    return render_template_string(INDEX_HTML, devices=devices)

@app.route('/settings')
def settings():
    return render_template_string(SETTINGS_HTML)

@app.route('/api/toggle_lock/<slug>', methods=['POST'])
def toggle_lock(slug):
    if slug in devices:
        new_state = not devices[slug]["manual_lock"]
        devices[slug]["manual_lock"] = new_state
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("UPDATE devices SET manual_lock = ? WHERE slug = ?", (new_state, slug))
        conn.commit(); conn.close()
        update_mqtt_state(slug)
        threading.Thread(target=safe_tv_request, args=(slug, "lock" if new_state else "unlock")).start()
        return jsonify({"success": True, "locked": new_state})
    return jsonify({"error": "Device not found"}), 404

@app.route('/api/add_device', methods=['POST'])
def add_device():
    name = request.form.get('name')
    ip = request.form.get('ip')
    slug = name.lower().replace(" ", "_")
    default_schedule = json.dumps({day: {"limit": 60, "bedtime": "20:00"} for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]})
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO devices VALUES (?, ?, ?, 0, 0, '', ?)", (slug, name, ip, default_schedule))
    conn.commit(); conn.close()
    load_devices()
    return f"<script>window.location.href='/';</script>"

@app.route('/api/add_time/<slug>/<int:mins>', methods=['POST'])
def add_time(slug, mins):
    if slug in devices:
        devices[slug]["minutes_used"] = max(0, devices[slug]["minutes_used"] - mins)
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("UPDATE devices SET minutes_used = ? WHERE slug = ?", (devices[slug]["minutes_used"], slug))
        conn.commit(); conn.close()
        update_mqtt_state(slug)
        return jsonify({"success": True})
    return jsonify({"error": "Not found"}), 404

@app.route('/api/reset/<slug>', methods=['POST'])
def reset_device(slug):
    if slug in devices:
        devices[slug]["minutes_used"] = 0
        conn = sqlite3.connect(DB_PATH); c = conn.cursor()
        c.execute("UPDATE devices SET minutes_used = 0 WHERE slug = ?", (slug,))
        conn.commit(); conn.close()
        update_mqtt_state(slug)
        return jsonify({"success": True})
    return jsonify({"error": "Not found"}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)