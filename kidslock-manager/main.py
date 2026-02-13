import logging, threading, time, sqlite3, requests, socket, json, os
from datetime import datetime
import paho.mqtt.client as mqtt
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import uvicorn

# --- INITIALISATIE ---
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
MQTT_USER = mqtt_conf.get("username")
MQTT_PASS = mqtt_conf.get("password")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS tv_configs 
                    (name TEXT PRIMARY KEY, ip TEXT, no_limit INTEGER DEFAULT 0, 
                     elapsed REAL DEFAULT 0, last_reset TEXT)''')
    cursor = conn.execute("PRAGMA table_info(tv_configs)")
    cols = [column[1] for column in cursor.fetchall()]
    if 'locked' not in cols:
        conn.execute("ALTER TABLE tv_configs ADD COLUMN locked INTEGER DEFAULT 0")
    days = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
    for d in days:
        if f"{d}_lim" not in cols:
            conn.execute(f"ALTER TABLE tv_configs ADD COLUMN {d}_lim INTEGER DEFAULT 120")
        if f"{d}_bed" not in cols:
            conn.execute(f"ALTER TABLE tv_configs ADD COLUMN {d}_bed TEXT DEFAULT '20:00'")
    conn.commit(); conn.close()

init_db()
app = FastAPI(root_path="/api/hassio_ingress/" + os.getenv("HOSTNAME", ""))
if not os.path.exists("static"): os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- MQTT ---
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if MQTT_USER and MQTT_PASS: mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

def on_mqtt_message(client, userdata, msg):
    try:
        topic, payload = msg.topic, msg.payload.decode().lower().strip()
        conn = sqlite3.connect(DB_PATH); tvs = conn.execute("SELECT ip, name, elapsed FROM tv_configs").fetchall(); conn.close()
        for ip, name, elapsed in tvs:
            slug = name.lower().replace(" ", "_")
            if topic == f"kidslock/{slug}/set":
                if payload == "+30":
                    with sqlite3.connect(DB_PATH) as c: c.execute("UPDATE tv_configs SET elapsed = max(0, elapsed - 30) WHERE ip = ?", (ip,))
                elif payload == "reset":
                    with sqlite3.connect(DB_PATH) as c: c.execute("UPDATE tv_configs SET elapsed = 0, locked = 0 WHERE ip = ?", (ip,))
                    requests.post(f"http://{ip}:8081/unlock", timeout=2)
                elif payload in ["lock", "unlock", "on", "off"]:
                    action = "lock" if payload in ["lock", "on"] else "unlock"
                    requests.post(f"http://{ip}:8081/{action}", timeout=2)
                    with sqlite3.connect(DB_PATH) as c: c.execute("UPDATE tv_configs SET locked = ? WHERE ip = ?", (1 if action == "lock" else 0, ip))
                    client.publish(f"kidslock/{slug}/state", "ON" if action == "lock" else "OFF", retain=True)
    except: pass

mqtt_client.on_connect = lambda c, u, f, rc, p=None: publish_discovery() if rc==0 else None
mqtt_client.on_message = on_mqtt_message
try: mqtt_client.connect_async(MQTT_HOST, MQTT_PORT, 60); mqtt_client.loop_start()
except: pass

def publish_discovery():
    try:
        conn = sqlite3.connect(DB_PATH); tvs = conn.execute("SELECT name FROM tv_configs").fetchall(); conn.close()
        for (name,) in tvs:
            slug = name.lower().replace(" ", "_")
            dev = {"identifiers": [f"kidslock_{slug}"], "name": f"KidsLock {name}", "manufacturer": "KidsLock", "model": "v2.6"}
            mqtt_client.publish(f"homeassistant/switch/kidslock_{slug}/config", json.dumps({"device": dev, "name": "Slot", "command_topic": f"kidslock/{slug}/set", "state_topic": f"kidslock/{slug}/state", "unique_id": f"kidslock_{slug}_sw", "payload_on": "lock", "payload_off": "unlock", "icon": "mdi:shield-lock"}), retain=True)
            mqtt_client.publish(f"homeassistant/sensor/kidslock_{slug}/config", json.dumps({"device": dev, "name": "Tijd Resterend", "state_topic": f"kidslock/{slug}/remaining", "unit_of_measurement": "min", "unique_id": f"kidslock_{slug}_rem"}), retain=True)
            mqtt_client.subscribe(f"kidslock/{slug}/set")
    except: pass

# --- MONITOR ---
def monitor_task():
    last_tick = time.time()
    while True:
        try:
            now = datetime.now(); day = now.strftime("%a").lower(); delta = min(1.0, (time.time() - last_tick) / 60.0); last_tick = time.time()
            conn = sqlite3.connect(DB_PATH)
            tvs = conn.execute(f"SELECT name, ip, no_limit, elapsed, last_reset, CAST({day}_lim AS REAL), {day}_bed, locked FROM tv_configs").fetchall()
            for name, ip, no_limit, elapsed, last_reset, limit, bedtime, manual_locked in tvs:
                slug = name.lower().replace(" ", "_")
                if last_reset != now.strftime("%Y-%m-%d"):
                    conn.execute("UPDATE tv_configs SET elapsed = 0, last_reset = ?, locked = 0 WHERE name = ?", (now.strftime("%Y-%m-%d"), name)); elapsed = 0
                try:
                    with socket.create_connection((ip, 8081), timeout=0.4):
                        auto_lock = (now.strftime("%H:%M") >= bedtime) or (not no_limit and (elapsed >= limit))
                        current_lock = manual_locked or auto_lock
                        action = "lock" if current_lock else "unlock"
                        requests.post(f"http://{ip}:8081/{action}", timeout=1)
                        mqtt_client.publish(f"kidslock/{slug}/state", "ON" if current_lock else "OFF")
                        if no_limit: mqtt_client.publish(f"kidslock/{slug}/remaining", "∞")
                        elif now.strftime("%H:%M") >= bedtime: mqtt_client.publish(f"kidslock/{slug}/remaining", "BEDTIME")
                        else:
                            new_el = float(elapsed) + delta if not current_lock else float(elapsed)
                            conn.execute("UPDATE tv_configs SET elapsed = ? WHERE name = ?", (new_el, name))
                            mqtt_client.publish(f"kidslock/{slug}/remaining", str(int(max(0, float(limit) - new_el))))
                except: pass
            conn.commit(); conn.close()
        except: pass
        time.sleep(30)

threading.Thread(target=monitor_task, daemon=True).start()

# --- ROUTES ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    day = datetime.now().strftime("%a").lower()
    conn = sqlite3.connect(DB_PATH); rows = conn.execute(f"SELECT name, ip, elapsed, no_limit, CAST({day}_lim AS REAL), {day}_bed, locked FROM tv_configs").fetchall(); conn.close()
    tvs = [{"name": r[0], "ip": r[1], "elapsed": round(float(r[2]), 1), "no_limit": r[3], "limit": int(float(r[4])), "bedtime": r[5], "remaining": int(max(0, float(r[4])-float(r[2]))), "locked": r[6]} for r in rows]
    return templates.TemplateResponse("index.html", {"request": request, "tvs": tvs})

@app.post("/api/action/{ip}")
async def api_action(ip: str, action: str = Form(...)):
    requests.post(f"http://{ip}:8081/{action}", timeout=2)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE tv_configs SET locked = ? WHERE ip = ?", (1 if action == "lock" else 0, ip))
    return {"status": "ok"}

@app.post("/api/update_tv")
async def update_tv(request: Request):
    d = await request.form()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE tv_configs SET name=?, no_limit=?, mon_lim=?, mon_bed=?, tue_lim=?, tue_bed=?, wed_lim=?, wed_bed=?, thu_lim=?, thu_bed=?, fri_lim=?, fri_bed=?, sat_lim=?, sat_bed=?, sun_lim=?, sun_bed=? WHERE name=?", (d['new_name'], int(d['no_limit']), d['mon_lim'], d['mon_bed'], d['tue_lim'], d['tue_bed'], d['wed_lim'], d['wed_bed'], d['thu_lim'], d['thu_bed'], d['fri_lim'], d['fri_bed'], d['sat_lim'], d['sat_bed'], d['sun_lim'], d['sun_bed'], d['old_name']))
    publish_discovery(); return {"status": "ok"}

if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port=8000)