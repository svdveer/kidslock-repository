import logging, threading, time, sqlite3, requests, subprocess, json, os
from datetime import datetime
import paho.mqtt.client as mqtt
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import uvicorn

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
    # Migratie naar nieuwe tabelstructuur
    conn.execute('''CREATE TABLE IF NOT EXISTS tv_configs 
                    (name TEXT PRIMARY KEY, ip TEXT, no_limit INTEGER DEFAULT 0, 
                     elapsed REAL DEFAULT 0, last_reset TEXT, schedule TEXT)''')
    conn.commit()
    conn.close()

init_db()
tv_states = {}
data_lock = threading.RLock()

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        logger.info("MQTT v1.7.0 Verbonden")
        with data_lock:
            for name in tv_states:
                slug = name.lower().replace(" ", "_")
                device = {"identifiers": [f"kidslock_{slug}"], "name": f"KidsLock {name}"}
                client.publish(f"homeassistant/switch/kidslock_{slug}/config", json.dumps({"name": "Vergrendeling", "command_topic": f"kidslock/{slug}/set", "state_topic": f"kidslock/{slug}/state", "unique_id": f"kidslock_{slug}_switch", "device": device}), retain=True)
                client.publish(f"homeassistant/sensor/kidslock_{slug}_rem/config", json.dumps({"name": "Tijd Resterend", "state_topic": f"kidslock/{slug}/remaining", "unique_id": f"kidslock_{slug}_rem", "device": device, "icon": "mdi:timer-sand"}), retain=True)
                client.subscribe(f"kidslock/{slug}/#")

def on_message(client, userdata, msg):
    parts = msg.topic.split('/')
    if len(parts) < 3: return
    slug, cmd = parts[1], parts[2]
    payload = msg.payload.decode().upper()
    with data_lock:
        for name, s in tv_states.items():
            if name.lower().replace(" ", "_") == slug:
                if cmd == "set":
                    s["manual_lock"] = (payload == "ON")
                    action = "lock" if s["manual_lock"] else "unlock"
                    threading.Thread(target=lambda: requests.post(f"http://{s['ip']}:8080/{action}", timeout=1)).start()

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
if mqtt_conf.get("username"): mqtt_client.username_pw_set(mqtt_conf["username"], mqtt_conf.get("password"))
try:
    mqtt_client.connect_async(MQTT_HOST, MQTT_PORT)
    mqtt_client.loop_start()
except: pass

def monitor():
    last_tick = time.time()
    days_map = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]
    while True:
        try:
            conn = sqlite3.connect(DB_PATH)
            rows = conn.execute("SELECT name, ip, no_limit, elapsed, last_reset, schedule FROM tv_configs").fetchall()
            conn.close()
            
            now = datetime.now()
            today_name = days_map[now.weekday()]
            current_time = now.strftime("%H:%M")
            today_date = now.strftime("%Y-%m-%d")
            delta = (time.time() - last_tick) / 60.0
            last_tick = time.time()
            
            with data_lock:
                for name, ip, no_limit, elapsed, last_reset, sched_json in rows:
                    sched = json.loads(sched_json) if sched_json else get_default_schedule()
                    day_cfg = sched.get(today_name, {"limit": 120, "bedtime": "20:00"})
                    
                    # Dagelijkse Reset
                    if last_reset != today_date:
                        elapsed = 0.0
                        with sqlite3.connect(DB_PATH) as c:
                            c.execute("UPDATE tv_configs SET elapsed = 0, last_reset = ? WHERE name = ?", (today_date, name))
                    
                    if name not in tv_states:
                        tv_states[name] = {"ip": ip, "online": False, "locked": False, "manual_lock": False, "limit": day_cfg['limit']}
                    
                    s = tv_states[name]
                    s.update({"limit": day_cfg['limit'], "bedtime": day_cfg['bedtime'], "no_limit": no_limit, "elapsed": elapsed})
                    
                    res = subprocess.run(['ping', '-c', '1', '-W', '1', ip], stdout=subprocess.DEVNULL)
                    s["online"] = (res.returncode == 0)

                    s["remaining"] = max(0, float(s['limit']) - s['elapsed'])
                    is_past_bedtime = current_time >= s['bedtime']
                    
                    should_lock = s["manual_lock"] or (not no_limit and (s["remaining"] <= 0 or is_past_bedtime))
                    
                    if should_lock != s["locked"] or (s["online"] and should_lock):
                        action = "lock" if should_lock else "unlock"
                        try: requests.post(f"http://{ip}:8080/{action}", timeout=1); s["locked"] = should_lock
                        except: pass

                    if s["online"] and not s["locked"] and not no_limit:
                        s["elapsed"] += delta
                        with sqlite3.connect(DB_PATH) as c:
                            c.execute("UPDATE tv_configs SET elapsed = ? WHERE name = ?", (s["elapsed"], name))
                    
                    slug = name.lower().replace(" ", "_")
                    mqtt_client.publish(f"kidslock/{slug}/state", "ON" if s["locked"] else "OFF")
                    status_val = "Onbeperkt" if no_limit else (f"BEDTIJD" if is_past_bedtime else f"{int(s['remaining'])} min")
                    mqtt_client.publish(f"kidslock/{slug}/remaining", status_val)
        except Exception as e: logger.error(f"Monitor error: {e}")
        time.sleep(30)

threading.Thread(target=monitor, daemon=True).start()

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
    processed_tvs = []
    for r in rows:
        processed_tvs.append({"name": r[0], "ip": r[1], "no_limit": r[2], "schedule": json.loads(r[3]) if r[3] else get_default_schedule()})
    return templates.TemplateResponse("settings.html", {"request": request, "tvs": processed_tvs})

@app.post("/api/save_tv")
async def save_tv(name: str = Form(...), ip: str = Form(...), no_limit: int = Form(...), schedule: str = Form(...)):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR REPLACE INTO tv_configs (name, ip, no_limit, schedule) VALUES (?, ?, ?, ?)", (name, ip, no_limit, schedule))
    return JSONResponse({"status": "ok"})

@app.post("/api/{action}/{name}")
async def api_handler(action: str, name: str, minutes: int = Form(None)):
    with data_lock:
        if name in tv_states:
            if action == "add_time": tv_states[name]["elapsed"] -= minutes
            elif action == "reset":
                with sqlite3.connect(DB_PATH) as c: c.execute("UPDATE tv_configs SET elapsed = 0 WHERE name = ?", (name,))
            elif action == "toggle_lock": tv_states[name]["manual_lock"] = not tv_states[name]["manual_lock"]
    return JSONResponse({"status": "ok"})

@app.post("/api/delete_tv/{name}")
async def delete_tv(name: str):
    with sqlite3.connect(DB_PATH) as conn: conn.execute("DELETE FROM tv_configs WHERE name = ?", (name,))
    return JSONResponse({"status": "ok"})

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)