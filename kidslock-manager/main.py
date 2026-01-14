import logging, threading, time, sqlite3, requests, subprocess, json, os
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

def get_default_schedule():
    days = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]
    return {d: {"limit": 120, "bedtime": "20:00"} for d in days}

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS tv_configs 
                    (name TEXT PRIMARY KEY, ip TEXT, no_limit INTEGER DEFAULT 0, 
                     elapsed REAL DEFAULT 0, last_reset TEXT, schedule TEXT)''')
    conn.commit()
    conn.close()

init_db()
tv_states = {}
data_lock = threading.RLock()

# --- MQTT CLIENT LOGICA ---
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        logger.info("MQTT v1.7.0.8 Verbonden - Discovery Actief")
        with data_lock:
            for name in tv_states:
                slug = name.lower().replace(" ", "_")
                device = {"identifiers": [f"kidslock_{slug}"], "name": f"KidsLock {name}"}
                
                # Discovery: Switch
                client.publish(f"homeassistant/switch/kidslock_{slug}/config", json.dumps({
                    "name": "Vergrendeling", "command_topic": f"kidslock/{slug}/set", 
                    "state_topic": f"kidslock/{slug}/state", "unique_id": f"kidslock_{slug}_switch", 
                    "device": device, "payload_on": "ON", "payload_off": "OFF"
                }), retain=True)
                
                # Discovery: Sensor (met attributen voor tekst-status)
                client.publish(f"homeassistant/sensor/kidslock_{slug}_rem/config", json.dumps({
                    "name": "Tijd Resterend", 
                    "state_topic": f"kidslock/{slug}/remaining", 
                    "json_attributes_topic": f"kidslock/{slug}/attributes",
                    "unit_of_measurement": "min",
                    "unique_id": f"kidslock_{slug}_rem", "device": device, "icon": "mdi:timer-sand"
                }), retain=True)

                # Discovery: Buttons
                client.publish(f"homeassistant/button/kidslock_{slug}_add/config", json.dumps({
                    "name": "Voeg 15m toe", "command_topic": f"kidslock/{slug}/add", 
                    "unique_id": f"kidslock_{slug}_btn_add", "device": device, "icon": "mdi:plus-circle"
                }), retain=True)

                client.publish(f"homeassistant/button/kidslock_{slug}_reset/config", json.dumps({
                    "name": "Reset Dag", "command_topic": f"kidslock/{slug}/reset", 
                    "unique_id": f"kidslock_{slug}_btn_reset", "device": device, "icon": "mdi:refresh"
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
                    client.publish(f"kidslock/{slug}/state", "ON" if is_on else "OFF", retain=True)
                elif action == "add":
                    s["elapsed"] = max(0, s["elapsed"] - 15)
                    with sqlite3.connect(DB_PATH) as c: c.execute("UPDATE tv_configs SET elapsed = ? WHERE name = ?", (s["elapsed"], name))
                elif action == "reset":
                    s["elapsed"] = 0
                    with sqlite3.connect(DB_PATH) as c: c.execute("UPDATE tv_configs SET elapsed = 0 WHERE name = ?", (name,))

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
    days_map = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]
    while True:
        try:
            conn = sqlite3.connect(DB_PATH)
            rows = conn.execute("SELECT name, ip, no_limit, elapsed, last_reset, schedule FROM tv_configs").fetchall()
            conn.close()
            
            now = datetime.now()
            today_date = now.strftime("%Y-%m-%d")
            delta = (time.time() - last_tick) / 60.0
            last_tick = time.time()
            
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
                    s.update({"limit": float(day_cfg['limit']), "bedtime": day_cfg['bedtime'], "no_limit": int(no_limit)})
                    
                    res = subprocess.run(['ping', '-c', '1', '-W', '1', ip], stdout=subprocess.DEVNULL)
                    s["online"] = (res.returncode == 0)
                    s["remaining"] = max(0, s['limit'] - s['elapsed'])
                    is_past_bedtime = now.strftime("%H:%M") >= s['bedtime']
                    
                    should_lock = s["manual_lock"] or (not s["no_limit"] and (s["remaining"] <= 0 or is_past_bedtime))
                    
                    if should_lock != s["locked"]:
                        action = "lock" if should_lock else "unlock"
                        try: 
                            requests.post(f"http://{ip}:8080/{action}", timeout=1)
                            s["locked"] = should_lock
                        except: pass

                    slug = name.lower().replace(" ", "_")
                    mqtt_client.publish(f"kidslock/{slug}/state", "ON" if s["locked"] else "OFF")
                    
                    # --- SMART STATUS & ATTRIBUTES ---
                    if s["no_limit"]:
                        val, display = 120, "Onbeperkt"
                    elif is_past_bedtime:
                        val, display = 0, "Bedtijd"
                    else:
                        val, display = int(s["remaining"]), f"{int(s['remaining'])} min"
                    
                    mqtt_client.publish(f"kidslock/{slug}/remaining", str(val))
                    mqtt_client.publish(f"kidslock/{slug}/attributes", json.dumps({"display_status": display}))

                    if s["online"] and not s["locked"] and not s["no_limit"]:
                        s["elapsed"] += delta
                        with sqlite3.connect(DB_PATH) as c: 
                            c.execute("UPDATE tv_configs SET elapsed = ? WHERE name = ?", (s["elapsed"], name))
                            
        except Exception as e: logger.error(f"Monitor error: {e}")
        time.sleep(30)

threading.Thread(target=monitor, daemon=True).start()

# --- WEB SERVER ---
app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    with data_lock:
        tvs_list = [{"name": n, **s} for n, s in tv_states.items()]
    return templates.TemplateResponse("index.html", {"request": request, "tvs": tvs_list})

@app.get("/settings", response_class=HTMLResponse)
async def settings_ui(request: Request):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT name, ip, no_limit, schedule FROM tv_configs").fetchall()
    conn.close()
    processed_tvs = [{"name": r[0], "ip": r[1], "no_limit": r[2], "schedule": json.loads(r[3]) if r[3] else get_default_schedule()} for r in rows]
    return templates.TemplateResponse("settings.html", {"request": request, "tvs": processed_tvs})

@app.post("/api/save_tv")
async def save_tv(name: str = Form(...), ip: str = Form(...), no_limit: int = Form(...), schedule: str = Form(...)):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR REPLACE INTO tv_configs (name, ip, no_limit, schedule) VALUES (?, ?, ?, ?)", (name, ip, no_limit, schedule))
    return JSONResponse({"status": "ok"})

@app.post("/api/delete_tv/{name}")
async def delete_tv(name: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM tv_configs WHERE name = ?", (name,))
    return JSONResponse({"status": "ok"})

@app.post("/api/{action}/{name}")
async def api_handler(action: str, name: str, minutes: int = Form(None)):
    with data_lock:
        if name in tv_states:
            slug = name.lower().replace(" ", "_")
            if action == "add_time": 
                tv_states[name]["elapsed"] -=