import os, json, time, sqlite3, requests, threading, datetime
import paho.mqtt.client as mqtt
from flask import Flask, render_template_string, request, jsonify

# --- DATABASE INITIALISATIE ---
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

# --- DATA LADEN ---
devices = {}
def load_devices():
    global devices
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row # Zorgt voor makkelijke toegang
        c = conn.cursor()
        c.execute("SELECT * FROM devices")
        rows = c.fetchall()
        
        new_devices = {}
        for row in rows:
            new_devices[row['slug']] = {
                "name": row['name'],
                "ip": row['ip'],
                "manual_lock": bool(row['manual_lock']),
                "minutes_used": row['minutes_used'],
                "last_reset": row['last_reset'],
                "schedule": json.loads(row['schedule']),
                "online": False
            }
        devices = new_devices
        conn.close()
        print(f"INFO: {len(devices)} apparaten geladen uit DB.")
    except Exception as e:
        print(f"ERROR: Laden database mislukt: {e}")

load_devices()

# --- MQTT SETUP ---
options = {}
try:
    with open("/data/options.json", "r") as f:
        options = json.load(f)
except:
    pass

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if options.get("mqtt_user"):
    mqtt_client.username_pw_set(options["mqtt_user"], options["mqtt_password"])

def update_mqtt_state(slug):
    if slug not in devices: return
    s = devices[slug]
    mqtt_client.publish(f"kidslock/{slug}/state", "ON" if s["manual_lock"] else "OFF", retain=True)

try:
    mqtt_client.connect(options.get("mqtt_host", "core-mosquitto"), options.get("mqtt_port", 1883), 60)
    mqtt_client.loop_start()
except:
    print("ERROR: MQTT Verbinding mislukt.")

# --- FLASK DASHBOARD ---
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="nl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KidsLock Manager</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #f0f2f5; padding-top: 50px; }
        .card-kids { border: none; border-radius: 20px; box-shadow: 0 10px 20px rgba(0,0,0,0.05); transition: 0.3s; }
        .card-kids:hover { transform: translateY(-5px); }
        .timer { font-size: 2.5rem; font-weight: 800; color: #0d6efd; }
    </style>
</head>
<body>
    <div class="container">
        <div class="d-flex justify-content-between align-items-center mb-5">
            <h1 class="fw-bold">🔐 KidsLock Dashboard</h1>
            <a href="/settings" class="btn btn-dark rounded-pill px-4">Instellingen</a>
        </div>

        {% if not devices %}
        <div class="alert alert-warning p-5 text-center shadow-sm" style="border-radius: 20px;">
            <h2 class="alert-heading">Geen apparaten gevonden!</h2>
            <p>Er staan momenteel geen TV's in de database.</p>
            <hr>
            <form action="/api/add" method="POST" class="d-inline">
                <input type="hidden" name="n" value="Test TV">
                <input type="hidden" name="i" value="192.168.1.100">
                <button type="submit" class="btn btn-warning fw-bold">Klik hier om een Test TV toe te voegen</button>
            </form>
        </div>
        {% else %}
        <div class="row">
            {% for slug, s in devices.items() %}
            <div class="col-md-4 mb-4">
                <div class="card card-kids p-4">
                    <h3 class="mb-0">{{ s.name }}</h3>
                    <small class="text-muted">{{ s.ip }}</small>
                    <div class="text-center my-4">
                        <div class="timer">{{ s.minutes_used }} <small style="font-size: 1rem;">min</small></div>
                    </div>
                    <button onclick="toggle('{{ slug }}')" 
                            id="btn-{{ slug }}"
                            class="btn {{ 'btn-danger' if s.manual_lock else 'btn-success' }} btn-lg w-100 fw-bold rounded-pill">
                        {{ 'ONTGRENDELEN' if s.manual_lock else 'VERGRENDELEN' }}
                    </button>
                </div>
            </div>
            {% endfor %}
        </div>
        {% endif %}
    </div>

    <script>
        function toggle(slug) {
            const btn = document.getElementById('btn-' + slug);
            btn.disabled = true;
            fetch('/api/toggle/' + slug, { method: 'POST' })
                .then(res => res.json())
                .then(data => {
                    if(data.success) {
                        location.reload();
                    }
                });
        }
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    load_devices()
    return render_template_string(HTML_TEMPLATE, devices=devices)

@app.route('/settings')
def settings():
    return """
    <div style="font-family: sans-serif; padding: 50px; text-align: center;">
        <h1>Instellingen</h1>
        <form action="/api/add" method="POST" style="display: inline-block; text-align: left; background: #eee; padding: 20px; border-radius: 10px;">
            Naam: <br><input name="n" style="width: 100%; margin-bottom: 10px;" required><br>
            IP Adres: <br><input name="i" style="width: 100%; margin-bottom: 10px;" required><br>
            <button type="submit" style="width: 100%; background: blue; color: white; border: none; padding: 10px; border-radius: 5px;">Voeg TV Toe</button>
        </form>
        <br><br><a href="/">Terug naar Dashboard</a>
    </div>
    """

@app.route('/api/toggle/<slug>', methods=['POST'])
def toggle(slug):
    if slug in devices:
        new_state = not devices[slug]["manual_lock"]
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE devices SET manual_lock = ? WHERE slug = ?", (new_state, slug))
        conn.commit()
        conn.close()
        load_devices()
        update_mqtt_state(slug)
        return jsonify(success=True)
    return jsonify(success=False), 404

@app.route('/api/add', methods=['POST'])
def add():
    n, i = request.form['n'], request.form['i']
    s = n.lower().replace(" ", "_")
    sch = json.dumps({d:{"limit":60,"bedtime":"20:00"} for d in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]})
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO devices VALUES (?,?,?,0,0,'',?)", (s,n,i,sch))
    conn.commit(); conn.close()
    load_devices()
    return '<script>window.location.href="/";</script>'

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)