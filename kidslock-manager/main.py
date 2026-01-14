import logging, threading, time, sqlite3, requests, json, os, datetime
import paho.mqtt.client as mqtt
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
import uvicorn

# --- CONFIG ---
DB_PATH = "/data/kidslock.db"
OPTIONS_PATH = "/data/options.json"

try:
    with open(OPTIONS_PATH, 'r') as f: options = json.load(f)
except: options = {}

MQTT_HOST = options.get("mqtt_host", "core-mosquitto")
MQTT_PORT = options.get("mqtt_port", 1883)

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS tv_configs 
                        (name TEXT PRIMARY KEY, ip TEXT, no_limit INTEGER DEFAULT 0, 
                         elapsed REAL DEFAULT 0, last_reset TEXT, schedule TEXT)''')
init_db()

tv_states = {}
data_lock = threading.RLock()
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

# --- MQTT LOGICA ---
def on_connect(client, userdata, flags, rc, properties=None):
    with data_lock:
        for name in tv_states:
            slug = name.lower().replace(" ", "_")
            client.subscribe(f"kidslock/{slug}/set")
            # Discovery voor HA
            dev = {"identifiers": [f"kidslock_{slug}"], "name": f"KidsLock {name}"}
            client.publish(f"homeassistant/switch/kidslock_{slug}/config", json.dumps({
                "name": "Vergrendeling", "command_topic": f"kidslock/{slug}/set", 
                "state_topic": f"kidslock/{slug}/state", "unique_id": f"kidslock_{slug}_sw", 
                "device": dev, "payload_on": "ON", "payload_off": "OFF"
            }), retain=True)

def on_message(client, userdata, msg):
    parts = msg.topic.split('/')
    if len(parts) < 3: return
    slug, cmd = parts[1], parts[2]
    payload = msg.payload.decode().upper()
    with data_lock:
        for name, s in tv_states.items():
            if name.lower().replace(" ", "_") == slug and cmd == "set":
                is_on = (payload == "ON")
                s["manual_lock"] = is_on
                # Directe actie naar TV
                threading.Thread(target=lambda: requests.get(f"http://{s['ip']}:8080/{'lock' if is_on else 'unlock'}", timeout=1)).start()

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
if options.get("mqtt_user"): mqtt_client.username_pw_set(options["mqtt_user"], options.get("mqtt_password"))

try:
    mqtt_client.connect_async(MQTT_HOST, MQTT_PORT)
    mqtt_client.loop_start()
except: pass

# --- MONITOR ---
def monitor():
    while True:
        try:
            with sqlite3.connect(DB_PATH) as conn:
                rows = conn.execute("SELECT name, ip, no_limit, elapsed, last_reset, schedule FROM tv_configs").fetchall()
            
            now = datetime.datetime.now()
            today = now.strftime("%Y-%m-%d")
            
            with data_lock:
                for name, ip, no_limit, elapsed, last_reset, sched_json in rows:
                    if last_reset != today:
                        elapsed = 0.0
                        with sqlite3.connect(DB_PATH) as c:
                            c.execute("UPDATE tv_configs SET elapsed = 0, last_reset = ? WHERE name = ?", (today, name))
                    
                    if name not in tv_states:
                        tv_states[name] = {"ip": ip, "online": False, "locked": False, "manual_lock": False, "elapsed": elapsed}
                    
                    s = tv_states[name]
                    try:
                        r = requests.get(f"http://{ip}:8080/status", timeout=1).json()
                        s["online"] = True
                        s["locked"] = r.get("locked", False)
                        if s["online"] and not s["locked"] and not no_limit:
                            s["elapsed"] += 0.5
                            with sqlite3.connect(DB_PATH) as c:
                                c.execute("UPDATE tv_configs SET elapsed = ? WHERE name = ?", (s["elapsed"], name))
                    except: s["online"] = False

                    # Regelmatige MQTT Sync
                    slug = name.lower().replace(" ", "_")
                    mqtt_client.publish(f"kidslock/{slug}/state", "ON" if s["locked"] else "OFF", retain=True)
        except Exception as e: print(f"Monitor error: {e}")
        time.sleep(30)

threading.Thread(target=monitor, daemon=True).start()

# --- WEB UI ---
app = FastAPI()

STYLE = """<style>
    body { font-family: 'Segoe UI', sans-serif; background: #f0f2f5; padding: 20px; }
    .card { background: white; border-radius: 12px; padding: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); margin-bottom: 20px; }
    .btn { padding: 12px 24px; border-radius: 8px; border: none; font-weight: bold; cursor: pointer; text-decoration: none; display: inline-block; }
    .btn-primary { background: #007bff; color: white; }
    .btn-danger { background: #dc3545; color: white; }
    .btn-success { background: #28a745; color: white; }
    .timer { font-size: 2.5rem; font-weight: bold; color: #007bff; }
</style>"""

@app.get("/", response_class=HTMLResponse)
async def index():
    html = f"<html><head>{STYLE}</head><body><div style='max-width:600px; margin:auto;'><h1>🔐 KidsLock Dashboard</h1>"
    with data_lock:
        for name, s in tv_states.items():
            cls = "btn-danger" if s['locked'] else "btn-success"
            txt = "ONTGRENDELEN" if s['locked'] else "VERGRENDELEN"
            html += f"""<div class='card text-center'>
                <h2>{name}</h2>
                <div class='timer'>{int(s['elapsed'])} min</div>
                <div style='margin-top:15px;'>
                    <form action='api/toggle/{name}' method='post' style='display:inline;'><button class='btn {cls}'>{txt}</button></form>
                    <form action='api/reset/{name}' method='post' style='display:inline;'><button class='btn btn-primary'>RESET</button></form>
                </div>
            </div>"""
    return html + "</div></body></html>"

@app.post("/api/toggle/{name}")
async def toggle(name: str):
    with data_lock:
        if name in tv_states:
            s = tv_states[name]
            s["manual_lock"] = not s["manual_lock"]
            # REALTIME MQTT PUSH
            slug = name.lower().replace(" ", "_")
            mqtt_client.publish(f"kidslock/{slug}/state", "ON" if s["manual_lock"] else "OFF", retain=True)
            threading.Thread(target=lambda: requests.get(f"http://{s['ip']}:8080/{'lock' if s['manual_lock'] else 'unlock'}", timeout=1)).start()
    return RedirectResponse(url="./", status_code=303)

@app.post("/api/reset/{name}")
async def reset(name: str):
    with sqlite3.connect(DB_PATH) as c:
        c.execute("UPDATE tv_configs SET elapsed = 0 WHERE name = ?", (name,))
    with data_lock:
        if name in tv_states: tv_states[name]["elapsed"] = 0
    return RedirectResponse(url="./", status_code=303)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)