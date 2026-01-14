import os, json, time, sqlite3, requests, threading, datetime
import paho.mqtt.client as mqtt
from flask import Flask, request, jsonify, redirect

# --- DATABASE INITIALISATIE ---
DB_PATH = "/data/kidslock.db"
def init_db():
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS devices 
                 (slug TEXT PRIMARY KEY, name TEXT, ip TEXT, 
                  manual_lock BOOLEAN, minutes_used INTEGER, 
                  last_reset TEXT, schedule TEXT)''')
    conn.commit(); conn.close()

init_db()

def get_db_devices():
    try:
        conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
        c = conn.cursor(); c.execute("SELECT * FROM devices")
        rows = c.fetchall(); data = [dict(row) for row in rows]; conn.close()
        return data
    except: return []

# --- HULPFUNCTIES ---
def safe_tv_request(ip, action):
    try:
        requests.get(f"http://{ip}:8080/{action}", timeout=1.5)
    except: pass

# --- MQTT SETUP ---
options = {}
try:
    with open("/data/options.json", "r") as f: options = json.load(f)
except: pass

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if options.get("mqtt_user"):
    mqtt_client.username_pw_set(options["mqtt_user"], options["mqtt_password"])

def update_mqtt(slug, name, manual_lock, minutes_used, schedule):
    now = datetime.datetime.now()
    day_cfg = schedule.get(now.strftime("%A"), {"limit": 60, "bedtime": "20:00"})
    is_bedtime = now.time() >= datetime.datetime.strptime(day_cfg["bedtime"], "%H:%M").time()
    effective_lock = manual_lock or is_bedtime or (minutes_used >= int(day_cfg["limit"]))
    
    mqtt_client.publish(f"kidslock/{slug}/state", "ON" if effective_lock else "OFF", retain=True)
    
    # Discovery (elke update voor de zekerheid)
    discovery = {"name": f"KidsLock {name}", "state_topic": f"kidslock/{slug}/state", 
                 "command_topic": f"kidslock/{slug}/set", "unique_id": f"kidslock_{slug}"}
    mqtt_client.publish(f"homeassistant/switch/kidslock_{slug}/config", json.dumps(discovery), retain=True)

mqtt_client.connect(options.get("mqtt_host", "core-mosquitto"), options.get("mqtt_port", 1883), 60)
mqtt_client.loop_start()

# --- MONITOR LOOP ---
def monitor():
    while True:
        devices = get_db_devices()
        now = datetime.datetime.now()
        for d in devices:
            # Reset check
            if d['last_reset'] != now.strftime("%Y-%m-%d"):
                conn = sqlite3.connect(DB_PATH); c = conn.cursor()
                c.execute("UPDATE devices SET minutes_used = 0, last_reset = ? WHERE slug = ?", (now.strftime("%Y-%m-%d"), d['slug']))
                conn.commit(); conn.close()
            
            # Status check & Lock/Unlock
            try:
                resp = requests.get(f"http://{d['ip']}:8080/status", timeout=1.5).json()
                if not resp.get("locked"):
                    new_mins = d['minutes_used'] + 1
                    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
                    c.execute("UPDATE devices SET minutes_used = ? WHERE slug = ?", (new_mins, d['slug']))
                    conn.commit(); conn.close()
            except: pass
            
            update_mqtt(d['slug'], d['name'], bool(d['manual_lock']), d['minutes_used'], json.loads(d['schedule']))
        time.sleep(60)

threading.Thread(target=monitor, daemon=True).start()

# --- FLASK WEB UI ---
app = Flask(__name__)

STYLE = """
<style>
    body { font-family: sans-serif; background: #f4f7f6; color: #333; padding: 20px; }
    .card { background: white; border-radius: 12px; padding: 20px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); margin-bottom: 20px; text-align: center; }
    .btn { padding: 12px 20px; border-radius: 8px; border: none; font-weight: bold; cursor: pointer; text-decoration: none; display: inline-block; font-size: 14px; }
    .btn-green { background: #27ae60; color: white; }
    .btn-red { background: #e74c3c; color: white; }
    .btn-blue { background: #3498db; color: white; }
    input { display: block; width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ddd; border-radius: 6px; box-sizing: border-box; }
</style>
"""

@app.route('/')
def index():
    devices = get_db_devices()
    html = f"<html><head><meta charset='UTF-8'>{STYLE}</head><body>"
    html += '<div style="display:flex; justify-content:space-between; align-items:center;"><h1>🔐 KidsLock</h1><a href="settings" class="btn btn-blue">Instellingen</a></div>'
    
    if not devices:
        html += '<div class="card"><h3>Geen TV gevonden</h3><form action="api/add" method="POST"><input name="n" value="Woonkamer TV"><input name="i" value="192.168.1.50"><button class="btn btn-green">Voeg Test TV toe</button></form></div>'
    else:
        for d in devices:
            btn_txt = "ONTGRENDELEN" if d['manual_lock'] else "VERGRENDELEN"
            btn_cls = "btn-red" if d['manual_lock'] else "btn-green"
            html += f'<div class="card"><h3>{d["name"]}</h3><p>{d["ip"]}</p><h1 style="color:#3498db;">{d["minutes_used"]} min</h1>'
            html += f'<form action="api/toggle/{d["slug"]}" method="POST"><button class="btn {btn_cls} w-100">{btn_txt}</button></form></div>'
    
    return html + "</body></html>"

@app.route('/settings')
def settings():
    return f"""<html><head><meta charset='UTF-8'>{STYLE}</head><body>
    <h1>Nieuwe TV</h1>
    <div class="card"><form action="api/add" method="POST">
        Naam: <input name="n" required> IP: <input name="i" required>
        <button class="btn btn-green">Toevoegen</button>
    </form></div>
    <a href="./" class="btn btn-blue">Terug</a></body></html>"""

@app.route('/api/toggle/<slug>', methods=['POST'])
def toggle(slug):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("UPDATE devices SET manual_lock = NOT manual_lock WHERE slug = ?", (slug,))
    conn.commit(); conn.close()
    return redirect("./", code=302)

@app.route('/api/add', methods=['POST'])
def add():
    n, i = request.form['n'], request.form['i']
    s = n.lower().replace(" ", "_")
    sch = json.dumps({day: {"limit": 60, "bedtime": "20:00"} for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]})
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO devices VALUES (?, ?, ?, 0, 0, '', ?)", (s, n, i, sch))
    conn.commit(); conn.close()
    return redirect("../", code=302)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)