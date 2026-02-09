import logging, threading, time, sqlite3, requests, socket, json, secrets, concurrent.futures
from datetime import datetime
import paho.mqtt.client as mqtt
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import uvicorn

# --- INITIALISATIE ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KidsLock")
DB_PATH = "/data/kidslock.db"

# MQTT Gegevens laden
try:
    with open("/data/mqtt.json") as f: mqtt_info = json.load(f)
except: mqtt_info = {}

MQTT_HOST = mqtt_info.get("host", "core-mosquitto")
MQTT_USER = mqtt_info.get("username", "")
MQTT_PASS = mqtt_info.get("password", "")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    # Maak tabel als deze niet bestaat
    conn.execute('''CREATE TABLE IF NOT EXISTS tv_configs 
                    (name TEXT PRIMARY KEY, ip TEXT, no_limit INTEGER DEFAULT 0, 
                     elapsed REAL DEFAULT 0, last_reset TEXT)''')
    
    # Check of de nieuwe kolommen bestaan, zo niet: toevoegen
    cursor = conn.execute("PRAGMA table_info(tv_configs)")
    columns = [column[1] for column in cursor.fetchall()]
    
    if "daily_limit" not in columns:
        logger.info("Kolom daily_limit toevoegen aan database...")
        conn.execute("ALTER TABLE tv_configs ADD COLUMN daily_limit INTEGER DEFAULT 120")
    
    conn.commit()
    conn.close()

init_db()
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# --- MQTT SETUP ---
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if MQTT_USER: mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

def on_connect(client, userdata, flags, rc, props=None):
    logger.info(f"MQTT Verbonden (code {rc})")
    publish_discovery()

mqtt_client.on_connect = on_connect
try: mqtt_client.connect_async(MQTT_HOST, 1883, 60); mqtt_client.loop_start()
except: logger.error("MQTT Connectie mislukt")

def publish_discovery():
    try:
        conn = sqlite3.connect(DB_PATH)
        tvs = conn.execute("SELECT name FROM tv_configs").fetchall()
        conn.close()
        for (name,) in tvs:
            slug = name.lower().replace(" ", "_")
            device = {"identifiers": [f"kidslock_{slug}"], "name": f"KidsLock {name}", "manufacturer": "KidsLock"}
            mqtt_client.publish(f"homeassistant/sensor/kidslock_{slug}/config", json.dumps({
                "name": "Resterende Tijd", "state_topic": f"kidslock/{slug}/remaining",
                "unit_of_measurement": "min", "unique_id": f"kidslock_{slug}_rem", "device": device
            }), retain=True)
    except: pass

# --- MONITOR LOOP ---
def monitor_task():
    last_tick = time.time()
    while True:
        try:
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            delta = (time.time() - last_tick) / 60.0
            last_tick = time.time()

            conn = sqlite3.connect(DB_PATH)
            tvs = conn.execute("SELECT name, ip, no_limit, elapsed, last_reset, daily_limit FROM tv_configs").fetchall()
            
            for name, ip, no_limit, elapsed, last_reset, daily_limit in tvs:
                slug = name.lower().replace(" ", "_")
                if last_reset != today:
                    conn.execute("UPDATE tv_configs SET elapsed = 0, last_reset = ? WHERE name = ?", (today, name))
                    elapsed = 0
                
                online = False
                try:
                    with socket.create_connection((ip, 8081), timeout=0.5): online = True
                except: pass

                if online:
                    if not no_limit:
                        new_elapsed = elapsed + delta
                        conn.execute("UPDATE tv_configs SET elapsed = ? WHERE name = ?", (new_elapsed, name))
                        rem = int(max(0, daily_limit - new_elapsed))
                        mqtt_client.publish(f"kidslock/{slug}/remaining", str(rem))
                        if new_elapsed >= daily_limit:
                            try: requests.post(f"http://{ip}:8081/lock", timeout=1)
                            except: pass
                    else:
                        mqtt_client.publish(f"kidslock/{slug}/remaining", "∞")
                        try: requests.post(f"http://{ip}:8081/unlock", timeout=1)
                        except: pass
            
            conn.commit(); conn.close()
        except Exception as e: logger.error(f"Monitor error: {e}")
        time.sleep(30)

threading.Thread(target=monitor_task, daemon=True).start()

# --- API ROUTES ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT name, ip, elapsed, no_limit, daily_limit FROM tv_configs").fetchall()
    conn.close()
    tvs = [{"name": r[0], "ip": r[1], "elapsed": round(r[2], 1), "no_limit": r[3], "limit": r[4], "remaining": int(max(0, r[4] - r[2]))} for r in rows]
    return templates.TemplateResponse("index.html", {"request": request, "tvs": tvs})

@app.get("/settings", response_class=HTMLResponse)
async def settings_ui(request: Request):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT name, ip, no_limit, daily_limit FROM tv_configs").fetchall()
    conn.close()
    tvs = [{"name": r[0], "ip": r[1], "no_limit": r[2], "limit": r[3]} for r in rows]
    return templates.TemplateResponse("settings.html", {"request": request, "tvs": tvs})

@app.post("/api/tv_action")
async def tv_action(ip: str = Form(...), action: str = Form(...)):
    if action == "reset":
        with sqlite3.connect(DB_PATH) as conn: conn.execute("UPDATE tv_configs SET elapsed = 0 WHERE ip = ?", (ip,))
        action = "unlock"
    try: requests.post(f"http://{ip}:8081/{action}", timeout=2)
    except: pass
    return {"status": "ok"}

@app.post("/api/update_tv")
async def update_tv(old_name: str = Form(...), new_name: str = Form(...), daily_limit: int = Form(...), no_limit: int = Form(...)):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE tv_configs SET name=?, daily_limit=?, no_limit=? WHERE name=?", (new_name, daily_limit, no_limit, old_name))
    publish_discovery()
    return {"status": "ok"}

@app.get("/api/discover")
async def discover(request: Request):
    try:
        base_ip = ".".join(request.client.host.split(".")[:3]) + "."
        def check_dev(i):
            target = f"{base_ip}{i}"
            try:
                with socket.create_connection((target, 8081), timeout=0.15):
                    r = requests.get(f"http://{target}:8081/device_info", timeout=0.3)
                    if r.status_code == 200: return {"name": r.json()['name'], "ip": target}
            except: return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=80) as ex:
            res = list(ex.map(check_dev, range(1, 255)))
        return [r for r in res if r]
    except: return []

@app.post("/api/pair_with_device")
async def pair(ip: str = Form(...), code: str = Form(...)):
    api_key = secrets.token_hex(16)
    try:
        r = requests.post(f"http://{ip}:8081/pair", data={"code": code, "api_key": api_key}, timeout=4)
        if r.status_code == 200:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("INSERT OR REPLACE INTO tv_configs (name, ip, no_limit, elapsed, last_reset, daily_limit) VALUES (?, ?, ?, 0, ?, 120)", (f"TV_{ip.split('.')[-1]}", ip, 0, datetime.now().strftime("%Y-%m-%d")))
            publish_discovery()
            return {"status": "success"}
    except: pass
    return JSONResponse({"status": "error"}, status_code=400)

@app.post("/api/delete_tv/{name}")
async def delete(name: str):
    with sqlite3.connect(DB_PATH) as conn: conn.execute("DELETE FROM tv_configs WHERE name=?", (name,))
    return {"status": "ok"}

if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port=8000)