import logging, threading, time, sqlite3, requests, socket, json, secrets, concurrent.futures, os
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

# --- MQTT CONFIGURATIE ---
MQTT_HOST = os.getenv("MQTT_HOST", "core-mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASSWORD", "")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS tv_configs 
                    (name TEXT PRIMARY KEY, ip TEXT, no_limit INTEGER DEFAULT 0, 
                     elapsed REAL DEFAULT 0, last_reset TEXT,
                     mon_lim INTEGER DEFAULT 120, tue_lim INTEGER DEFAULT 120,
                     wed_lim INTEGER DEFAULT 120, thu_lim INTEGER DEFAULT 120,
                     fri_lim INTEGER DEFAULT 120, sat_lim INTEGER DEFAULT 180,
                     sun_lim INTEGER DEFAULT 180)''')
    cursor = conn.execute("PRAGMA table_info(tv_configs)")
    cols = [column[1] for column in cursor.fetchall()]
    days = ['mon_lim', 'tue_lim', 'wed_lim', 'thu_lim', 'fri_lim', 'sat_lim', 'sun_lim']
    for d in days:
        if d not in cols:
            conn.execute(f"ALTER TABLE tv_configs ADD COLUMN {d} INTEGER DEFAULT 120")
    conn.commit()
    conn.close()

init_db()
app = FastAPI(); templates = Jinja2Templates(directory="templates")

# --- MQTT SETUP ---
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if MQTT_USER: mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

def on_connect(client, userdata, flags, rc, props=None):
    if rc == 0: publish_discovery()
mqtt_client.on_connect = on_connect
try: mqtt_client.connect_async(MQTT_HOST, MQTT_PORT, 60); mqtt_client.loop_start()
except: pass

def publish_discovery():
    try:
        conn = sqlite3.connect(DB_PATH); tvs = conn.execute("SELECT name FROM tv_configs").fetchall(); conn.close()
        for (name,) in tvs:
            slug = name.lower().replace(" ", "_")
            dev = {"identifiers": [f"kidslock_{slug}"], "name": f"KidsLock {name}", "manufacturer": "KidsLock"}
            mqtt_client.publish(f"homeassistant/sensor/kidslock_{slug}/config", json.dumps({
                "name": "Resterende Tijd", "state_topic": f"kidslock/{slug}/remaining",
                "unit_of_measurement": "min", "unique_id": f"kidslock_{slug}_rem", "device": dev
            }), retain=True)
    except: pass

# --- MONITOR TAAK ---
def monitor_task():
    last_tick = time.time()
    while True:
        try:
            now = datetime.now(); today = now.strftime("%Y-%m-%d")
            day_key = now.strftime("%a").lower() + "_lim"
            delta = (time.time() - last_tick) / 60.0; last_tick = time.time()
            conn = sqlite3.connect(DB_PATH)
            tvs = conn.execute("SELECT name, ip, no_limit, elapsed, last_reset, mon_lim, tue_lim, wed_lim, thu_lim, fri_lim, sat_lim, sun_lim FROM tv_configs").fetchall()
            day_idx = {'mon_lim':5, 'tue_lim':6, 'wed_lim':7, 'thu_lim':8, 'fri_lim':9, 'sat_lim':10, 'sun_lim':11}
            for row in tvs:
                name, ip, no_limit, elapsed, last_reset = row[0], row[1], row[2], row[3], row[4]
                current_limit = row[day_idx[day_key]]
                slug = name.lower().replace(" ", "_")
                if last_reset != today:
                    conn.execute("UPDATE tv_configs SET elapsed = 0, last_reset = ? WHERE name = ?", (today, name))
                    elapsed = 0
                online = False
                try:
                    with socket.create_connection((ip, 8081), timeout=0.4): online = True
                except: pass
                if online:
                    if not no_limit:
                        new_elapsed = elapsed + delta
                        conn.execute("UPDATE tv_configs SET elapsed = ? WHERE name = ?", (new_elapsed, name))
                        rem = int(max(0, current_limit - new_elapsed))
                        mqtt_client.publish(f"kidslock/{slug}/remaining", str(rem))
                        if new_elapsed >= current_limit:
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
    day_key = datetime.now().strftime("%a").lower() + "_lim"
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(f"SELECT name, ip, elapsed, no_limit, {day_key} FROM tv_configs").fetchall()
    conn.close()
    tvs = [{"name": r[0], "ip": r[1], "elapsed": round(r[2],1), "no_limit": r[3], "limit": r[4], "remaining": int(max(0, r[4]-r[2]))} for r in rows]
    return templates.TemplateResponse("index.html", {"request": request, "tvs": tvs})

@app.get("/settings", response_class=HTMLResponse)
async def settings_ui(request: Request):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT * FROM tv_configs").fetchall()
    conn.close()
    tvs = []
    for r in rows:
        tvs.append({"name":r[0], "ip":r[1], "no_limit":r[2], "mon":r[5], "tue":r[6], "wed":r[7], "thu":r[8], "fri":r[9], "sat":r[10], "sun":r[11]})
    return templates.TemplateResponse("settings.html", {"request": request, "tvs": tvs})

@app.post("/api/update_tv")
async def update_tv(old_name: str = Form(...), new_name: str = Form(...), no_limit: int = Form(...),
                    mon: int = Form(...), tue: int = Form(...), wed: int = Form(...), 
                    thu: int = Form(...), fri: int = Form(...), sat: int = Form(...), sun: int = Form(...)):
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            conn.execute("""UPDATE tv_configs SET name=?, no_limit=?, 
                            mon_lim=?, tue_lim=?, wed_lim=?, thu_lim=?, fri_lim=?, sat_lim=?, sun_lim=? 
                            WHERE name=?""", (new_name, no_limit, mon, tue, wed, thu, fri, sat, sun, old_name))
            conn.commit()
        publish_discovery()
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Fout bij opslaan: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

@app.post("/api/tv_action")
async def tv_action(ip: str=Form(...), action: str=Form(...)):
    if action == "reset":
        with sqlite3.connect(DB_PATH) as conn: conn.execute("UPDATE tv_configs SET elapsed = 0 WHERE ip = ?", (ip,))
        action = "unlock"
    try: requests.post(f"http://{ip}:8081/{action}", timeout=2)
    except: pass
    return {"status": "ok"}

@app.get("/api/discover")
async def discover(request: Request):
    base_ip = ".".join(request.client.host.split(".")[:3]) + "."
    def check_dev(i):
        target = f"{base_ip}{i}"; 
        try:
            with socket.create_connection((target, 8081), timeout=0.1):
                r = requests.get(f"http://{target}:8081/device_info", timeout=0.2)
                return {"name": r.json()['name'], "ip": target}
        except: return None
    with concurrent.futures.ThreadPoolExecutor(max_workers=100) as ex:
        res = list(ex.map(check_dev, range(1, 255)))
    return [r for r in res if r]

@app.post("/api/pair_with_device")
async def pair(ip: str=Form(...), code: str=Form(...)):
    try:
        r = requests.post(f"http://{ip}:8081/pair", data={"code": code, "api_key": secrets.token_hex(16)}, timeout=4)
        if r.status_code == 200:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("INSERT OR REPLACE INTO tv_configs (name, ip, no_limit, elapsed, last_reset) VALUES (?, ?, 0, 0, ?)", 
                             (f"TV_{ip.split('.')[-1]}", ip, datetime.now().strftime("%Y-%m-%d")))
            publish_discovery(); return {"status": "success"}
    except: pass
    return JSONResponse({"status": "error"}, status_code=400)

@app.post("/api/delete_tv/{name}")
async def delete(name: str):
    with sqlite3.connect(DB_PATH) as conn: conn.execute("DELETE FROM tv_configs WHERE name=?", (name,))
    return {"status": "ok"}

if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port=8000)