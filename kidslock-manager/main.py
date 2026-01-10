import logging, threading, time, sqlite3, requests, subprocess, json, os, datetime
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
    conn.execute('CREATE TABLE IF NOT EXISTS tv_config (name TEXT PRIMARY KEY, ip TEXT, daily_limit INTEGER, bedtime TEXT, no_limit INTEGER DEFAULT 0, elapsed REAL DEFAULT 0)')
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(tv_config)")
    columns = [c[1] for c in cursor.fetchall()]
    if 'elapsed' not in columns:
        conn.execute('ALTER TABLE tv_config ADD COLUMN elapsed REAL DEFAULT 0')
    conn.commit()
    conn.close()

init_db()
tv_states = {}
data_lock = threading.RLock()

# --- MQTT Discovery & Logic ---
mqtt_client = mqtt.Client()

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        logger.info("MQTT Verbonden v1.6.0 - Discovery gestart")
        with data_lock:
            for name in tv_states:
                slug = name.lower().replace(" ", "_")
                device = {"identifiers": [f"kidslock_{slug}"], "name": f"KidsLock {name}", "manufacturer": "KidsLock AI"}
                
                # Entities
                base_configs = [
                    ("switch", "state", "Vergrendeling", "mdi:lock"),
                    ("sensor", "remaining", "Tijd Resterend", "mdi:timer-sand"),
                    ("sensor", "elapsed", "Kijktijd Vandaag", "mdi:television-play")
                ]
                
                for platform, subtopic, label, icon in base_configs:
                    payload = {
                        "name": label, "state_topic": f"kidslock/{slug}/{subtopic}",
                        "unique_id": f"kidslock_{slug}_{subtopic}", "device": device, "icon": icon
                    }
                    if platform == "switch":
                        payload["command_topic"] = f"kidslock/{slug}/set"
                    if platform == "sensor":
                        payload["unit_of_measurement"] = "min"
                    client.publish(f"homeassistant/{platform}/kidslock_{slug}_{subtopic}/config", json.dumps(payload), retain=True)

                # Action Buttons
                for minutes in [15, 30]:
                    client.publish(f"homeassistant/button/kidslock_{slug}_add_{minutes}/config", json.dumps({
                        "name": f"Voeg {minutes}m toe", "command_topic": f"kidslock/{slug}/add_time",
                        "payload_press": str(minutes), "unique_id": f"kidslock_{slug}_add_{minutes}", "device": device, "icon": "mdi:plus-clock"
                    }), retain=True)

                client.publish(f"homeassistant/button/kidslock_{slug}_reset/config", json.dumps({
                    "name": "Reset Daglimiet", "command_topic": f"kidslock/{slug}/reset",
                    "unique_id": f"kidslock_{slug}_reset", "device": device, "icon": "mdi:restore"
                }), retain=True)

                client.subscribe(f"kidslock/{slug}/#")

def on_message(client, userdata, msg):
    parts = msg.topic.split('/')
    if len(parts) < 3: return
    slug = parts[1]
    cmd = parts[2]
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
                elif cmd == "reset":
                    s["remaining"] = float(s["limit"])
                    s["elapsed"] = 0.0
                    try: requests.post(f"http://{s['ip']}:8080/unlock", timeout=2); s["locked"] = False
                    except: pass

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
if mqtt_conf.get("username"): mqtt_client.username_pw_set(mqtt_conf["username"], mqtt_conf.get("password"))
try:
    mqtt_client.connect_async(MQTT_HOST, MQTT_PORT)
    mqtt_client.loop_start()
except: pass

def monitor():
    last_date = datetime.date.today()
    last_tick = time.time()
    while True:
        try:
            conn = sqlite3.connect(DB_PATH); cursor = conn.cursor()
            cursor.execute("SELECT name, ip, daily_limit, bedtime, no_limit, elapsed FROM tv_config")
            rows = cursor.fetchall(); conn.close()
            
            with data_lock:
                if datetime.date.today() != last_date:
                    for n in tv_states:
                        tv_states[n]["remaining"] = float(tv_states[n]["limit"])
                        tv_states[n]["elapsed"] = 0.0
                    last_date = datetime.date.today()
                
                delta = (time.time() - last_tick) / 60.0
                last_tick = time.time()
                
                for name, ip, limit, bedtime, no_limit, db_elapsed in rows:
                    if name not in tv_states:
                        tv_states[name] = {"ip": ip, "limit": limit, "remaining": float(limit), "online": False, "locked": False, "no_limit": no_limit, "elapsed": db_elapsed}
                    
                    s = tv_states[name]
                    s.update({"ip": ip, "limit": limit, "no_limit": no_limit})
                    res = subprocess.run(['ping', '-c', '1', '-W', '1', s["ip"]], stdout=subprocess.DEVNULL)
                    s["online"] = (res.returncode == 0)

                    if s["online"] and not s["locked"] and s["no_limit"] == 0:
                        s["remaining"] = max(0, s["remaining"] - delta)
                        s["elapsed"] += delta
                    
                    if s["remaining"] <= 0 and not s["locked"] and s["no_limit"] == 0:
                        try: requests.post(f"http://{s['ip']}:8080/lock", timeout=2); s["locked"] = True
                        except: pass
                    
                    slug = name.lower().replace(" ", "_")
                    mqtt_client.publish(f"kidslock/{slug}/state", "ON" if s["locked"] else "OFF", retain=True)
                    mqtt_client.publish(f"kidslock/{slug}/remaining", str(int(s["remaining"])), retain=True)
                    mqtt_client.publish(f"kidslock/{slug}/elapsed", str(int(s["elapsed"])), retain=True)
                    
                    with sqlite3.connect(DB_PATH) as conn:
                        conn.execute("UPDATE tv_config SET elapsed = ? WHERE name = ?", (s["elapsed"], name))
        except: pass
        time.sleep(30)

threading.Thread(target=monitor, daemon=True).start()

app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    tvs_list = [{"name": n, **s} for n, s in tv_states.items()]
    return templates.TemplateResponse("index.html", {"request": request, "tvs": tvs_list})

@app.post("/api/{action}/{name}")
async def api_handler(action: str, name: str, minutes: int = Form(None)):
    with data_lock:
        if name in tv_states:
            s = tv_states[name]
            if action == "toggle_lock":
                act = "unlock" if s["locked"] else "lock"
                try: requests.post(f"http://{s['ip']}:8080/{act}", timeout=2); s["locked"] = not s["locked"]
                except: pass
            elif action == "add_time":
                s["remaining"] += minutes
            elif action == "reset":
                s["remaining"] = float(s["limit"]); s["elapsed"] = 0.0
                try: requests.post(f"http://{s['ip']}:8080/unlock", timeout=2); s["locked"] = False
                except: pass
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)