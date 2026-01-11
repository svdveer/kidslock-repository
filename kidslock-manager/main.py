import logging, threading, time, sqlite3, requests, subprocess, json, os, datetime
import paho.mqtt.client as mqtt
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KidsLock")
DB_PATH = "/data/kidslock.db"
OPTIONS_PATH = "/data/options.json"

# MQTT Config
try:
    with open(OPTIONS_PATH, 'r') as f:
        options = json.load(f)
except:
    options = {}

mqtt_conf = options.get("mqtt", {})
MQTT_HOST = mqtt_conf.get("host", "core-mosquitto")
MQTT_PORT = mqtt_conf.get("port", 1883)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('CREATE TABLE IF NOT EXISTS tv_config (name TEXT PRIMARY KEY, ip TEXT, daily_limit INTEGER, bedtime TEXT, no_limit INTEGER DEFAULT 0, elapsed REAL DEFAULT 0)')
    conn.commit()
    conn.close()

init_db()
tv_states = {}
data_lock = threading.RLock()

# --- MQTT Logic ---
mqtt_client = mqtt.Client()

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("MQTT Verbonden v1.6.2")
        with data_lock:
            for name in tv_states:
                slug = name.lower().replace(" ", "_")
                device = {"identifiers": [f"kidslock_{slug}"], "name": f"KidsLock {name}"}
                # Discovery config
                client.publish(f"homeassistant/switch/kidslock_{slug}/config", json.dumps({"name": "Vergrendeling", "command_topic": f"kidslock/{slug}/set", "state_topic": f"kidslock/{slug}/state", "unique_id": f"kidslock_{slug}_switch", "device": device}), retain=True)
                client.publish(f"homeassistant/sensor/kidslock_{slug}_rem/config", json.dumps({"name": "Tijd Resterend", "state_topic": f"kidslock/{slug}/remaining", "unit_of_measurement": "min", "unique_id": f"kidslock_{slug}_rem", "device": device}), retain=True)
                client.subscribe(f"kidslock/{slug}/#")

def on_message(client, userdata, msg):
    parts = msg.topic.split('/')
    if len(parts) < 3: return
    slug, cmd = parts[1], parts[2]
    payload = msg.payload.decode()
    with data_lock:
        for name, s in tv_states.items():
            if name.lower().replace(" ", "_") == slug:
                if cmd == "set":
                    action = "lock" if payload.upper() == "ON" else "unlock"
                    try: requests.post(f"http://{s['ip']}:8080/{action}", timeout=2); s["locked"] = (payload.upper() == "ON")
                    except: pass
                elif cmd == "add_time":
                    s["remaining"] += float(payload)

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
if mqtt_conf.get("username"): mqtt_client.username_pw_set(mqtt_conf["username"], mqtt_conf.get("password"))
try:
    mqtt_client.connect_async(MQTT_HOST, MQTT_PORT)
    mqtt_client.loop_start()
except: pass

# --- Monitor ---
def monitor():
    last_tick = time.time()
    while True:
        try:
            conn = sqlite3.connect(DB_PATH)
            rows = conn.execute("SELECT name, ip, daily_limit, bedtime, no_limit, elapsed FROM tv_config").fetchall()
            conn.close()
            
            delta = (time.time() - last_tick) / 60.0
            last_tick = time.time()
            
            with data_lock:
                for name, ip, limit, bedtime, no_limit, elapsed in rows:
                    if name not in tv_states:
                        tv_states[name] = {"ip": ip, "limit": limit, "remaining": float(limit) - elapsed, "online": False, "locked": False, "no_limit": no_limit, "elapsed": elapsed}
                    s = tv_states[name]
                    res = subprocess.run(['ping', '-c', '1', '-W', '1', s["ip"]], stdout=subprocess.DEVNULL)
                    s["online"] = (res.returncode == 0)
                    if s["online"] and not s["locked"] and not s["no_limit"]:
                        s["remaining"] = max(0, s["remaining"] - delta)
                        s["elapsed"] += delta
                        with sqlite3.connect(DB_PATH) as c: c.execute("UPDATE tv_config SET elapsed = ? WHERE name = ?", (s["elapsed"], name))
                    
                    slug = name.lower().replace(" ", "_")
                    mqtt_client.publish(f"kidslock/{slug}/state", "ON" if s["locked"] else "OFF")
                    mqtt_client.publish(f"kidslock/{slug}/remaining", str(int(s["remaining"])))
        except: pass
        time.sleep(30)

threading.Thread(target=monitor, daemon=True).start()

# --- FastAPI ---
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
    rows = conn.execute("SELECT name, ip, daily_limit, bedtime, no_limit FROM tv_config").fetchall()
    conn.close()
    return templates.TemplateResponse("settings.html", {"request": request, "tvs": rows})

@app.post("/api/add_tv")
async def add_tv(name: str = Form(...), ip: str = Form(...), limit: int = Form(...), bedtime: str = Form(...), no_limit: int = Form(...)):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR REPLACE INTO tv_config (name, ip, daily_limit, bedtime, no_limit, elapsed) VALUES (?, ?, ?, ?, ?, 0)", (name, ip, limit, bedtime, no_limit))
    return JSONResponse({"status": "ok"})

@app.post("/api/{action}/{name}")
async def api_handler(action: str, name: str, minutes: int = Form(None)):
    with data_lock:
        if name in tv_states:
            s = tv_states[name]
            if action == "toggle_lock":
                act = "unlock" if s["locked"] else "lock"
                try: requests.post(f"http://{s['ip']}:8080/{act}", timeout=2); s["locked"] = not s["locked"]
                except: pass
            elif action == "add_time": s["remaining"] += minutes
    return JSONResponse({"status": "ok"})

@app.post("/api/delete_tv/{name}")
async def delete_tv(name: str):
    with sqlite3.connect(DB_PATH) as conn: conn.execute("DELETE FROM tv_config WHERE name = ?", (name,))
    return JSONResponse({"status": "ok"})

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)