import os, json, time, sqlite3, requests, threading, datetime
import paho.mqtt.client as mqtt
from flask import Flask, request, jsonify, redirect

# --- DATABASE ---
DB_PATH = "/data/kidslock.db"
def init_db():
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS devices 
                 (slug TEXT PRIMARY KEY, name TEXT, ip TEXT, 
                  manual_lock BOOLEAN, minutes_used INTEGER, 
                  last_reset TEXT, schedule TEXT)''')
    conn.commit(); conn.close()
init_db()

def get_db_devices(slug=None):
    conn = sqlite3.connect(DB_PATH); conn.row_factory = sqlite3.Row
    c = conn.cursor()
    if slug:
        c.execute("SELECT * FROM devices WHERE slug = ?", (slug,))
        row = c.fetchone(); conn.close()
        return dict(row) if row else None
    c.execute("SELECT * FROM devices")
    rows = c.fetchall(); data = [dict(row) for row in rows]; conn.close()
    return data

# --- MQTT SETUP ---
options = {}
try:
    with open("/data/options.json", "r") as f: options = json.load(f)
except: pass

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if options.get("mqtt_user"):
    mqtt_client.username_pw_set(options["mqtt_user"], options["mqtt_password"])

def update_mqtt(slug, name, manual_lock, minutes_used, schedule_json):
    try:
        schedule = json.loads(schedule_json)
        now = datetime.datetime.now()
        day_cfg = schedule.get(now.strftime("%A"), {"limit": 60, "bedtime": "20:00"})
        limit = int(day_cfg["limit"])
        is_bedtime = now.time() >= datetime.datetime.strptime(day_cfg["bedtime"], "%H:%M").time()
        
        # Onbeperkt logica: als limit 999 is, wordt tijd genegeerd
        is_over_limit = (minutes_used >= limit) if limit < 999 else False
        
        effective_lock = manual_lock or is_bedtime or is_over_limit
        mqtt_client.publish(f"kidslock/{slug}/state", "ON" if effective_lock else "OFF", retain=True)
    except: pass

mqtt_client.connect(options.get("mqtt_host", "core-mosquitto"), options.get("mqtt_port", 1883), 60)
mqtt_client.loop_start()

# --- MONITOR LOOP ---
def monitor():
    while True:
        devices = get_db_devices()
        now = datetime.datetime.now()
        for d in devices:
            # Dagelijkse Reset
            if d['last_reset'] != now.strftime("%Y-%m-%d"):
                conn = sqlite3.connect(DB_PATH); c = conn.cursor()
                c.execute("UPDATE devices SET minutes_used = 0, last_reset = ? WHERE slug = ?", (now.strftime("%Y-%m-%d"), d['slug']))
                conn.commit(); conn.close()

            # TV Status & Time Counting
            try:
                resp = requests.get(f"http://{d['ip']}:8080/status", timeout=1.5).json()
                if not resp.get("locked"):
                    new_m = d['minutes_used'] + 1
                    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
                    c.execute("UPDATE devices SET minutes_used = ? WHERE slug = ?", (new_m, d['slug']))
                    conn.commit(); conn.close()
            except: pass
            update_mqtt(d['slug'], d['name'], bool(d['manual_lock']), d['minutes_used'], d['schedule'])
        time.sleep(60)

threading.Thread(target=monitor, daemon=True).start()

# --- FLASK WEB UI ---
app = Flask(__name__)

STYLE = """
<style>
    body { font-family: sans-serif; background: #f4f7f9; padding: 20px; color: #333; }
    .card { background: white; border-radius: 15px; padding: 20px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); margin-bottom: 20px; text-align: center; max-width: 500px; margin-inline: auto; }
    .timer { font-size: 2.5rem; font-weight: bold; color: #3498db; margin: 10px 0; }
    .btn { padding: 10px; border-radius: 8px; border: none; font-weight: bold; cursor: pointer; text-decoration: none; display: inline-block; width: 100%; margin-bottom: 8px; font-size: 14px; box-sizing: border-box; }
    .btn-green { background: #27ae60; color: white; }
    .btn-red { background: #e74c3c; color: white; }
    .btn-blue { background: #3498db; color: white; }
    .btn-purple { background: #9b59b6; color: white; }
    .btn-outline { background: white; border: 2px solid #3498db; color: #3498db; }
    .btn-warn { background: #f39c12; color: white; }
    input { width: 100%; padding: 8px; margin: 5px 0 15px 0; border: 1px solid #ddd; border-radius: 6px; box-sizing: border-box; }
    label { font-weight: bold; font-size: 13px; display: block; text-align: left; }
    .grid-btns { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
</style>
"""

@app.route('/')
def index():
    devices = get_db_devices()
    html = f"<html><head><meta charset='UTF-8'>{STYLE}</head><body>"
    html += '<div style="display:flex; justify-content:space-between; align-items:center; max-width:500px; margin:auto; margin-bottom:20px;"><h1>🔐 KidsLock</h1><a href="settings" class="btn btn-blue" style="width:auto;">+ Nieuw</a></div>'
    for d in devices:
        sch = json.loads(d['schedule'])
        day_limit = int(sch.get(datetime.datetime.now().strftime("%A"), {}).get("limit", 60))
        is_unlimited = day_limit >= 999
        btn_txt = "ONTGRENDELEN" if d['manual_lock'] else "VERGRENDELEN"
        btn_cls = "btn-red" if d['manual_lock'] else "btn-green"
        
        html += f"""
        <div class="card">
            <h3 style="margin:0;">{d['name']} {"🚀" if is_unlimited else ""}</h3>
            <div class="timer">{d['minutes_used']} <span style="font-size:15px;">/ {'∞' if is_unlimited else day_limit} min</span></div>
            <form action="api/toggle/{d['slug']}" method="POST"><button class="btn {btn_cls}">{btn_txt}</button></form>
            <div class="grid-btns">
                <form action="api/unlimited/{d['slug']}" method="POST"><button class="btn btn-purple">Onbeperkt</button></form>
                <a href="schedule/{d['slug']}" class="btn btn-blue">Planning</a>
            </div>
            <div class="grid-btns">
                <form action="api/add_time/{d['slug']}/15" method="POST"><button class="btn btn-outline">+15 min</button></form>
                <form action="api/reset/{d['slug']}" method="POST"><button class="btn btn-warn">Dag-Reset</button></form>
            </div>
        </div>"""
    return html + "</body></html>"

@app.route('/schedule/<slug>')
def schedule(slug):
    d = get_db_devices(slug); sch = json.loads(d['schedule'])
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    html = f"<html><head><meta charset='UTF-8'>{STYLE}</head><body><div class='card'><h2>Planning: {d['name']}</h2><form action='../api/save_sch/{slug}' method='POST'>"
    for day in days:
        html += f"<div style='border-bottom: 1px solid #eee; padding: 10px 0; text-align: left;'>"
        html += f"<strong>{day}</strong><div class='grid-btns'>"
        html += f"<span>Limiet (999=∞): <input type='number' name='lim_{day}' value='{sch[day]['limit']}'></span>"
        html += f"<span>Bedtijd: <input type='time' name='bed_{day}' value='{sch[day]['bedtime']}'></span></div></div>"
    html += "<button class='btn btn-green' style='margin-top:20px;'>Opslaan</button></form><a href='../' class='btn btn-blue'>Terug</a></div></body></html>"
    return html

@app.route('/api/save_sch/<slug>', methods=['POST'])
def save_sch(slug):
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    new_sch = {day: {"limit": request.form[f"lim_{day}"], "bedtime": request.form[f"bed_{day}"]} for day in days}
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("UPDATE devices SET schedule = ? WHERE slug = ?", (json.dumps(new_sch), slug))
    conn.commit(); conn.close(); return redirect("../", code=302)

@app.route('/api/unlimited/<slug>', methods=['POST'])
def set_unlimited(slug):
    d = get_db_devices(slug); sch = json.loads(d['schedule']); today = datetime.datetime.now().strftime("%A")
    sch[today]["limit"] = 999
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("UPDATE devices SET schedule = ? WHERE slug = ?", (json.dumps(sch), slug))
    conn.commit(); conn.close(); return redirect("./", code=302)

@app.route('/settings')
def settings():
    return f"<html><head><meta charset='UTF-8'>{STYLE}</head><body><div class='card'><h1>Nieuwe TV</h1><form action='api/add' method='POST'><label>Naam:</label><input name='n' required><label>IP Adres:</label><input name='i' required><button class='btn btn-green'>Toevoegen</button></form><a href='./' class='btn btn-blue'>Terug</a></div></body></html>"

@app.route('/api/toggle/<slug>', methods=['POST'])
def toggle(slug):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("UPDATE devices SET manual_lock = NOT manual_lock WHERE slug = ?", (slug,))
    conn.commit(); conn.close(); return redirect("./", code=302)

@app.route('/api/add_time/<slug>/<int:mins>', methods=['POST'])
def add_time(slug, mins):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("UPDATE devices SET minutes_used = MAX(0, minutes_used - ?) WHERE slug = ?", (mins, slug))
    conn.commit(); conn.close(); return redirect("../../", code=302)

@app.route('/api/reset/<slug>', methods=['POST'])
def reset(slug):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("UPDATE devices SET minutes_used = 0 WHERE slug = ?", (slug,))
    conn.commit(); conn.close(); return redirect("./", code=302)

@app.route('/api/add', methods=['POST'])
def add():
    n, i = request.form['n'], request.form['i']; s = n.lower().replace(" ", "_")
    sch = json.dumps({day: {"limit": 60, "bedtime": "20:00"} for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]})
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO devices VALUES (?, ?, ?, 0, 0, '', ?)", (s, n, i, sch))
    conn.commit(); conn.close(); return redirect("../", code=302)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)