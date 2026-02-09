import logging, threading, time, sqlite3, requests, subprocess, json, os, secrets
from datetime import datetime
import paho.mqtt.client as mqtt
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import uvicorn

# --- INITIALISATIE & LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KidsLock")
DB_PATH = "/data/kidslock.db"
OPTIONS_PATH = "/data/options.json"

try:
    with open(OPTIONS_PATH, 'r') as f: options = json.load(f)
except: options = {}

mqtt_conf = options.get("mqtt", {})
MQTT_HOST = mqtt_conf.get("host", "core-mosquitto")
MQTT_PORT = mqtt_conf.get("port", 1883)

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS tv_configs 
                    (name TEXT PRIMARY KEY, ip TEXT, no_limit INTEGER DEFAULT 0, 
                     elapsed REAL DEFAULT 0, last_reset TEXT, schedule TEXT)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS paired_devices 
                    (device_id TEXT PRIMARY KEY, name TEXT, pairing_code TEXT, 
                     api_key TEXT, last_seen TEXT, status TEXT, current_app TEXT)''')
    conn.commit()
    conn.close()

init_db()
tv_states = {}
data_lock = threading.RLock()

# --- MQTT CLIENT LOGICA ---
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        logger.info("MQTT v2.0.0.3 Verbonden")
        with data_lock:
            for name in tv_states:
                slug = name.lower().replace(" ", "_")
                device = {"identifiers": [f"kidslock_{slug}"], "name": f"KidsLock {name}"}
                
                # Discovery: Switch & Sensor
                client.publish(f"homeassistant/switch/kidslock_{slug}/config", json.dumps({
                    "name": "Vergrendeling", "command_topic": f"kidslock/{slug}/set", 
                    "state_topic": f"kidslock/{slug}/state", "unique_id": f"kidslock_{slug}_switch", "device": device
                }), retain=True)
                client.publish(f"homeassistant/sensor/kidslock_{slug}_rem/config", json.dumps({
                    "name": "Tijd Resterend", "state_topic": f"kidslock/{slug}/remaining", 
                    "json_attributes_topic": f"kidslock/{slug}/attributes", "unit_of_measurement": "min",
                    "unique_id": f"kidslock_{slug}_rem", "device": device
                }), retain=True)
                client.subscribe(f"kidslock/{slug}/#")

def on_message(client, userdata, msg):
    parts = msg.topic.split('/')
    if len(parts) < 3: return
    slug, action = parts[1], parts[2]
    with data_lock:
        for name, s in tv_states.items():
            if name.lower().replace(" ", "_") == slug:
                if action == "set":
                    is_on = (msg.payload.decode().upper() == "ON")
                    s["manual_lock"] = is_on
                    threading.Thread(target=lambda: requests.post(f"http://{s['ip']}:8080/{'lock' if is_on else 'unlock'}", timeout=2)).start()
                elif action == "reset":
                    with sqlite3.connect(DB_PATH) as c:
                        c.execute("UPDATE tv_configs SET elapsed = 0 WHERE name = ?", (name,))

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
if mqtt_conf.get("username"): mqtt_client.username_pw_set(mqtt_conf["username"], mqtt_conf.get("password"))

try:
    mqtt_client.connect_async(MQTT_HOST, MQTT_PORT)
    mqtt_client.loop_start()
except: pass

# --- MONITOR LOOP ---
def monitor():
    last_tick = time.time()
    initialized = False
    days_map = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]
    while True:
        try:
            conn = sqlite3.connect(DB_PATH)
            rows = conn.execute("SELECT name, ip, no_limit, elapsed, last_reset, schedule FROM tv_configs").fetchall()
            conn.close()
            
            now = datetime.now()
            today_date = now.strftime("%Y-%m-%d")
            delta = 0.0 if not initialized else min(1.0, (time.time() - last_tick) / 60.0)
            last_tick = time.time()
            initialized = True
            
            with data_lock:
                for name, ip, no_limit, elapsed_db, last_reset, sched_json in rows:
                    if last_reset != today_date:
                        elapsed_db = 0.0
                        with sqlite3.connect(DB_PATH) as c: c.execute("UPDATE tv_configs SET elapsed = 0, last_reset = ? WHERE name = ?", (today_date, name))
                    
                    if name not in tv_states:
                        tv_states[name] = {"ip": ip, "online": False, "locked": False, "manual_lock": False, "elapsed": elapsed_db}
                    
                    s = tv_states[name]
                    s["elapsed"] = elapsed_db
                    sched = json.loads(sched_json) if sched_json else {"Maandag": {"limit": 120, "bedtime": "20:00"}}
                    day_cfg = sched.get(days_map[now.weekday()], {"limit": 120, "bedtime": "20:00"})
                    
                    # Check status via Ping (fallback)
                    res = subprocess.run(['ping', '-c', '1', '-W', '1', ip], stdout=subprocess.DEVNULL)
                    s["online"] = (res.returncode == 0)
                    s["remaining"] = max(0, float(day_cfg.get('limit', 120)) - s['elapsed'])
                    
                    is_past_bedtime = now.strftime("%H:%M") >= day_cfg.get('bedtime', "20:00")
                    should_lock = s["manual_lock"] or is_past_bedtime or (not no_limit and s["remaining"] <= 0)
                    
                    if should_lock != s["locked"]:
                        action = "lock" if should_lock else "unlock"
                        try: requests.post(f"http://{ip}:8080/{action}", timeout=1); s["locked"] = should_lock
                        except: pass

                    slug = name.lower().replace(" ", "_")
                    display = "Bedtijd" if is_past_bedtime else ("Onbeperkt" if no_limit else f"{int(s['remaining'])} min")
                    mqtt_client.publish(f"kidslock/{slug}/remaining", str(120 if no_limit else int(s["remaining"])))
                    mqtt_client.publish(f"kidslock/{slug}/attributes", json.dumps({"display_status": display}))
                    mqtt_client.publish(f"kidslock/{slug}/state", "ON" if s["locked"] else "OFF")

                    if s["online"] and not s["locked"] and not no_limit:
                        with sqlite3.connect(DB_PATH) as c: c.execute("UPDATE tv_configs SET elapsed = ? WHERE name = ?", (s["elapsed"] + delta, name))
                            
        except Exception as e: logger.error(f"Monitor error: {e}")
        time.sleep(30)

threading.Thread(target=monitor, daemon=True).start()

# --- WEB SERVER & API ---
app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    with data_lock: tvs_list = [{"name": n, **s} for n, s in tv_states.items()]
    return templates.TemplateResponse("index.html", {"request": request, "tvs": tvs_list})

@app.get("/settings", response_class=HTMLResponse)
async def settings_ui(request: Request):
    conn = sqlite3.connect(DB_PATH); rows = conn.execute("SELECT name, ip, no_limit, schedule FROM tv_configs").fetchall(); conn.close()
    processed_tvs = [{"name": r[0], "ip": r[1], "no_limit": r[2], "schedule": json.loads(r[3]) if r[3] else {}} for r in rows]
    return templates.TemplateResponse("settings.html", {"request": request, "tvs": processed_tvs})

@app.get("/api/get_general_pairing_code")
async def get_general_code():
    code = "".join(secrets.choice("0123456789") for i in range(6))
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR REPLACE INTO paired_devices (device_id, name, pairing_code, status) VALUES (?, ?, ?, ?)", ("GLOBAL_PAIR", "Pending", code, "pairing"))
    return JSONResponse({"code": code})

@app.post("/api/pair")
async def pair_device(request: Request, device_id: str = Form(...), code: str = Form(...), device_name: str = Form(...)):
    client_ip = request.client.host
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT pairing_code FROM paired_devices WHERE device_id = 'GLOBAL_PAIR' AND pairing_code = ?", (code,)).fetchone()
        if row:
            api_key = secrets.token_hex(16)
            conn.execute("INSERT OR REPLACE INTO paired_devices (device_id, name, api_key, status, last_seen) VALUES (?, ?, ?, ?, ?)", (device_id, device_name, api_key, "active", datetime.now().isoformat()))
            conn.execute("INSERT OR IGNORE INTO tv_configs (name, ip, no_limit, elapsed, last_reset, schedule) VALUES (?, ?, ?, ?, ?, ?)", (device_name, client_ip, 0, 0, datetime.now().strftime("%Y-%m-%d"), "{}"))
            return JSONResponse({"status": "paired", "api_key": api_key})
    return JSONResponse({"status": "error", "message": "Code ongeldig"}, status_code=400)

@app.post("/api/heartbeat")
async def heartbeat(device_id: str = Form(...), api_key: str = Form(...), current_app: str = Form("unknown")):
    with sqlite3.connect(DB_PATH) as conn:
        device = conn.execute("SELECT name FROM paired_devices WHERE device_id = ? AND api_key = ?", (device_id, api_key)).fetchone()
        if device:
            conn.execute("UPDATE paired_devices SET last_seen = ?, current_app = ? WHERE device_id = ?", (datetime.now().isoformat(), current_app, device_id))
            return JSONResponse({"status": "ok"})
    return JSONResponse({"status": "unauthorized"}, status_code=401)

@app.post("/api/save_tv")
async def save_tv(name: str = Form(...), ip: str = Form(...), no_limit: int = Form(...), schedule: str = Form(...)):
    with sqlite3.connect(DB_PATH) as conn: conn.execute("INSERT OR REPLACE INTO tv_configs (name, ip, no_limit, schedule) VALUES (?, ?, ?, ?)", (name, ip, no_limit, schedule))
    return JSONResponse({"status": "ok"})

@app.post("/api/delete_tv/{name}")
async def delete_tv(name: str):
    with sqlite3.connect(DB_PATH) as conn: conn.execute("DELETE FROM tv_configs WHERE name = ?", (name,))
    return JSONResponse({"status": "ok"})

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)