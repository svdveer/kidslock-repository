import logging, threading, time, sqlite3, requests, json, os, datetime
import paho.mqtt.client as mqtt
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse
import uvicorn

# --- CONFIG & LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KidsLock")
DB_PATH = "/data/kidslock.db"
OPTIONS_PATH = "/data/options.json"

try:
    with open(OPTIONS_PATH, 'r') as f: options = json.load(f)
except: options = {}

mqtt_conf = options.get("mqtt", {})
MQTT_HOST = options.get("mqtt_host", "core-mosquitto")
MQTT_PORT = options.get("mqtt_port", 1883)

# --- DATABASE & LOGICA ---
def get_default_schedule():
    days = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]
    return {d: {"limit": 120, "bedtime": "20:00"} for d in days}

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS tv_configs 
                        (name TEXT PRIMARY KEY, ip TEXT, no_limit INTEGER DEFAULT 0, 
                         elapsed REAL DEFAULT 0, last_reset TEXT, schedule TEXT)''')
init_db()

tv_states = {}
data_lock = threading.RLock()
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

def on_connect(client, userdata, flags, rc, properties=None):
    logger.info(f"MQTT Verbonden status: {rc}")
    with data_lock:
        for name in tv_states:
            slug = name.lower().replace(" ", "_")
            client.subscribe(f"kidslock/{slug}/set")
            # Discovery logic
            device = {"identifiers": [f"kidslock_{slug}"], "name": f"KidsLock {name}"}
            client.publish(f"homeassistant/switch/kidslock_{slug}/config", json.dumps({
                "name": "Vergrendeling", "command_topic": f"kidslock/{slug}/set", 
                "state_topic": f"kidslock/{slug}/state", "unique_id": f"kidslock_{slug}_sw", 
                "device": device, "payload_on": "ON", "payload_off": "OFF"
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
                client.publish(f"kidslock/{slug}/state", payload, retain=True)
                threading.Thread(target=lambda: requests.get(f"http://{s['ip']}:8080/{'lock' if is_on else 'unlock'}", timeout=1)).start()

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
if options.get("mqtt_user"): mqtt_client.username_pw_set(options["mqtt_user"], options.get("mqtt_password"))

try:
    mqtt_client.connect_async(MQTT_HOST, MQTT_PORT)
    mqtt_client.loop_start()
except: pass

# --- MONITORING ---
def monitor():
    days_map = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]
    while True:
        try:
            with sqlite3.connect(DB_PATH) as conn:
                rows = conn.execute("SELECT name, ip, no_limit, elapsed, last_reset, schedule FROM tv_configs").fetchall()
            
            now = datetime.datetime.now()
            today_date = now.strftime("%Y-%m-%d")
            
            with data_lock:
                for name, ip, no_limit, elapsed, last_reset, sched_json in rows:
                    if last_reset != today_date:
                        elapsed = 0.0
                        with sqlite3.connect(DB_PATH) as c:
                            c.execute("UPDATE tv_configs SET elapsed = 0, last_reset = ? WHERE name = ?", (today_date, name))
                    
                    if name not in tv_states:
                        tv_states[name] = {"ip": ip, "online": False, "locked": False, "manual_lock": False, "elapsed": elapsed}
                    
                    s = tv_states[name]
                    sched = json.loads(sched_json) if sched_json else get_default_schedule()
                    day_cfg = sched.get(days_map[now.weekday()], {"limit": 120, "bedtime": "20:00"})
                    
                    # Status Check
                    try:
                        r = requests.get(f"http://{ip}:8080/status", timeout=1).json()
                        s["online"] = True
                        s["locked"] = r.get("locked", False)
                        if s["online"] and not s["locked"] and not no_limit:
                            s["elapsed"] += 0.5 # Elke 30 sec tick
                            with sqlite3.connect(DB_PATH) as c:
                                c.execute("UPDATE tv_configs SET elapsed = ? WHERE name = ?", (s["elapsed"], name))
                    except: s["online"] = False

                    # Auto-lock logic
                    is_past_bedtime = now.strftime("%H:%M") >= day_cfg['bedtime']
                    should_lock = s["manual_lock"] or (not no_limit and (s["elapsed"] >= float(day_cfg['limit']) or is_past_bedtime))
                    
                    if should_lock != s["locked"]:
                        requests.get(f"http://{ip}:8080/{'lock' if should_lock else 'unlock'}", timeout=1)
                    
                    # MQTT Updates
                    slug = name.lower().replace(" ", "_")
                    mqtt_client.publish(f"kidslock/{slug}/state", "ON" if should_lock else "OFF", retain=True)
                    rem = max(0, float(day_cfg['limit']) - s['elapsed'])
                    mqtt_client.publish(f"kidslock/{slug}/remaining", "∞" if no_limit else f"{int(rem)} min", retain=True)

        except Exception as e: logger.error(f"Monitor error: {e}")
        time.sleep(30)

threading.Thread(target=monitor, daemon=True).start()

# --- WEB UI (FastAPI) ---
app = FastAPI()

HTML_HEAD = """<head><meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1.0'>
<link href='https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css' rel='stylesheet'>
<style>body{background:#f8f9fa;padding:20px;}.card{border-radius:15px;box-shadow:0 4px 8px rgba(0,0,0,0.05);margin-bottom:20px;}</style></head>"""

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    html = f"<html>{HTML_HEAD}<body><div class='container'><h1>🔐 KidsLock Dashboard</h1>"
    with data_lock:
        for name, s in tv_states.items():
            cls = "danger" if s['locked'] else "success"
            html += f"""<div class='card p-3'><h3>{name}</h3>
            <h1 class='text-primary'>{int(s['elapsed'])} min gebruikt</h1>
            <div class='d-flex gap-2'>
                <form action='api/toggle_lock/{name}' method='post' class='flex-grow-1'><button class='btn btn-{cls} w-100'>{'ONTGRENDELEN' if s['locked'] else 'VERGRENDELEN'}</button></form>
                <form action='api/reset/{name}' method='post'><button class='btn btn-warning'>Reset</button></form>
            </div>
            <a href='settings' class='btn btn-link mt-2'>Instellingen & Schema</a></div>"""
    return html + "</div></body></html>"

@app.get("/settings", response_class=HTMLResponse)
async def settings_ui(request: Request):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT name, ip, no_limit, schedule FROM tv_configs").fetchall()
    html = f"<html>{HTML_HEAD}<body><div class='container'><h1>Settings</h1>"
    for r in rows:
        html += f"<div class='card p-3'><h4>{r[0]} ({r[1]})</h4><form action='api/delete_tv/{r[0]}' method='post'><button class='btn btn-sm btn-danger'>Verwijder</button></form></div>"
    html += "<a href='./' class='btn btn-secondary'>Terug</a></div></body></html>"
    return html

@app.post("/api/toggle_lock/{name}")
async def toggle_lock(name: str):
    with data_lock:
        if name in tv_states:
            tv_states[name]["manual_lock"] = not tv_states[name]["manual_lock"]
    return RedirectResponse(url="./", status_code=303)

@app.post("/api/reset/{name}")
async def reset_tv(name: str):
    with sqlite3.connect(DB_PATH) as c: c.execute("UPDATE tv_configs SET elapsed = 0 WHERE name = ?", (name,))
    with data_lock:
        if name in tv_states: tv_states[name]["elapsed"] = 0
    return RedirectResponse(url="./", status_code=303)

@app.post("/api/delete_tv/{name}")
async def delete_tv(name: str):
    with sqlite3.connect(DB_PATH) as conn: conn.execute("DELETE FROM tv_configs WHERE name = ?", (name,))
    return RedirectResponse(url="../settings", status_code=303)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)