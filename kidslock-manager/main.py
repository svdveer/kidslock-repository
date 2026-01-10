import logging, threading, time, sqlite3, requests, subprocess, json, os
import paho.mqtt.client as mqtt
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KidsLock")
DB_PATH = "/data/kidslock.db"
OPTIONS_PATH = "/data/options.json"

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
    conn.execute('CREATE TABLE IF NOT EXISTS tv_config (name TEXT PRIMARY KEY, ip TEXT, daily_limit INTEGER, bedtime TEXT)')
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(tv_config)")
    if 'no_limit' not in [c[1] for c in cursor.fetchall()]:
        conn.execute('ALTER TABLE tv_config ADD COLUMN no_limit INTEGER DEFAULT 0')
    conn.execute('CREATE TABLE IF NOT EXISTS tv_state (tv_name TEXT PRIMARY KEY, remaining REAL)')
    conn.commit()
    conn.close()

init_db()
tv_states = {}
data_lock = threading.RLock()

# --- MQTT ---
mqtt_client = mqtt.Client()
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        with data_lock:
            for name in tv_states:
                slug = name.lower().replace(" ", "_")
                discovery = {"name": f"{name} Lock", "command_topic": f"kidslock/{slug}/set", "state_topic": f"kidslock/{slug}/state", "unique_id": f"kidslock_{slug}", "device": {"identifiers": ["kidslock_mgr"], "name": "KidsLock Manager"}}
                client.publish(f"homeassistant/switch/kidslock_{slug}/config", json.dumps(discovery), retain=True)
                client.subscribe(f"kidslock/{slug}/set")

def on_message(client, userdata, msg):
    payload = msg.payload.decode().upper()
    with data_lock:
        for name, s in tv_states.items():
            slug = name.lower().replace(" ", "_")
            if msg.topic == f"kidslock/{slug}/set":
                action = "lock" if payload == "ON" else "unlock"
                try:
                    requests.post(f"http://{s['ip']}:8080/{action}", timeout=1.5)
                    s["locked"] = (payload == "ON")
                    client.publish(f"kidslock/{slug}/state", payload, retain=True)
                except: pass

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
if mqtt_conf.get("username"):
    mqtt_client.username_pw_set(mqtt_conf["username"], mqtt_conf.get("password"))
try:
    mqtt_client.connect_async(MQTT_HOST, MQTT_PORT)
    mqtt_client.loop_start()
except: pass

def monitor():
    last_tick = time.time()
    while True:
        try:
            conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
            cursor.execute("SELECT name, ip, daily_limit, bedtime, no_limit FROM tv_config")
            rows = cursor.fetchall(); conn.close()
            with data_lock:
                current_names = [r[0] for r in rows]
                for n in list(tv_states.keys()):
                    if n not in current_names: del tv_states[n]
                delta = (time.time() - last_tick) / 60.0
                last_tick = time.time()
                for name, ip, limit, bedtime, no_limit in rows:
                    if name not in tv_states:
                        tv_states[name] = {"ip": ip, "limit": limit, "remaining": float(limit), "online": False, "locked": False, "no_limit": no_limit}
                    s = tv_states[name]
                    s.update({"ip": ip, "limit": limit, "no_limit": no_limit})
                    res = subprocess.run(['ping', '-c', '1', '-W', '1', s["ip"]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    s["online"] = (res.returncode == 0)
                    if s["no_limit"] == 1:
                        if s["locked"]: requests.post(f"http://{s['ip']}:8080/unlock", timeout=1.5); s["locked"] = False
                    elif s["online"] and not s["locked"]:
                        s["remaining"] = max(0, s["remaining"] - delta)
                    if s["remaining"] <= 0 and not s["locked"] and s["no_limit"] == 0:
                        try: requests.post(f"http://{s['ip']}:8080/lock", timeout=1.5); s["locked"] = True
                        except: pass
                    slug = name.lower().replace(" ", "_")
                    mqtt_client.publish(f"kidslock/{slug}/state", "ON" if s["locked"] else "OFF", retain=True)
        except: pass
        time.sleep(30)

threading.Thread(target=monitor, daemon=True).start()

app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    tvs_list = [{"name": n, **s} for n, s in tv_states.items()]
    return templates.TemplateResponse("index.html", {"request": request, "tvs": tvs_list})

@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request):
    conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
    cursor.execute("SELECT * FROM tv_config"); tvs = cursor.fetchall(); conn.close()
    return templates.TemplateResponse("settings.html", {"request": request, "tvs": tvs})

# --- API ---
@app.post("/api/add_tv")
async def add_tv(name:str=Form(...), ip:str=Form(...), limit:int=Form(...), bedtime:str=Form(...), no_limit:int=Form(0)):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("INSERT OR REPLACE INTO tv_config VALUES (?,?,?,?,?)", (name, ip, limit, bedtime, no_limit))
    conn.commit(); conn.close(); return {"status": "ok"}

@app.post("/api/delete_tv/{name}")
async def delete_tv(name: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("DELETE FROM tv_config WHERE name = ?", (name,))
    conn.commit(); conn.close(); return {"status": "ok"}

@app.post("/api/toggle_lock/{name}")
async def toggle_api(name: str):
    with data_lock:
        if name in tv_states:
            s = tv_states[name]
            action = "unlock" if s["locked"] else "lock"
            try:
                requests.post(f"http://{s['ip']}:8080/{action}", timeout=1.5)
                s["locked"] = not s["locked"]
                slug = name.lower().replace(" ", "_")
                mqtt_client.publish(f"kidslock/{slug}/state", "ON" if s["locked"] else "OFF", retain=True)
            except: pass
    return {"status": "ok"}

@app.post("/api/add_time/{name}")
async def add_time_api(name: str, minutes: int = Form(...)):
    with data_lock:
        if name in tv_states: tv_states[name]["remaining"] += minutes
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)