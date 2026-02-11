import logging, threading, time, sqlite3, requests, socket, json, secrets, os
from datetime import datetime
import paho.mqtt.client as mqtt
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KidsLock")
DB_PATH = "/data/kidslock.db"

MQTT_HOST = os.getenv("MQTT_HOST", "core-mosquitto")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_USER = os.getenv("MQTT_USER", "")
MQTT_PASS = os.getenv("MQTT_PASSWORD", "")

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS tv_configs 
                    (name TEXT PRIMARY KEY, ip TEXT, no_limit INTEGER DEFAULT 0, 
                     elapsed REAL DEFAULT 0, last_reset TEXT)''')
    cursor = conn.execute("PRAGMA table_info(tv_configs)")
    cols = [column[1] for column in cursor.fetchall()]
    days = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']
    for d in days:
        if f"{d}_lim" not in cols:
            conn.execute(f"ALTER TABLE tv_configs ADD COLUMN {d}_lim INTEGER DEFAULT 120")
        if f"{d}_bed" not in cols:
            conn.execute(f"ALTER TABLE tv_configs ADD COLUMN {d}_bed TEXT DEFAULT '20:00'")
    conn.commit()
    conn.close()

init_db()
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
if MQTT_USER: mqtt_client.username_pw_set(MQTT_USER, MQTT_PASS)

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

mqtt_client.on_connect = lambda c, u, f, rc, p=None: publish_discovery() if rc==0 else None
try: 
    mqtt_client.connect_async(MQTT_HOST, MQTT_PORT, 60)
    mqtt_client.loop_start()
except: pass

def monitor_task():
    last_tick = time.time()
    while True:
        try:
            now = datetime.now()
            today = now.strftime("%Y-%m-%d")
            now_time = now.strftime("%H:%M")
            day_prefix = now.strftime("%a").lower()
            delta = (time.time() - last_tick) / 60.0; last_tick = time.time()
            
            conn = sqlite3.connect(DB_PATH)
            tvs = conn.execute(f"SELECT name, ip, no_limit, elapsed, last_reset, {day_prefix}_lim, {day_prefix}_bed FROM tv_configs").fetchall()
            
            for row in tvs:
                name, ip, no_limit, elapsed, last_reset, limit, bedtime = row
                slug = name.lower().replace(" ", "_")
                if last_reset != today:
                    conn.execute("UPDATE tv_configs SET elapsed = 0, last_reset = ? WHERE name = ?", (today, name))
                    elapsed = 0
                try:
                    with socket.create_connection((ip, 8081), timeout=0.4):
                        if no_limit:
                            mqtt_client.publish(f"kidslock/{slug}/remaining", "∞")
                        elif now_time >= bedtime:
                            requests.post(f"http://{ip}:8081/lock", timeout=1)
                            mqtt_client.publish(f"kidslock/{slug}/remaining", "BEDTIME")
                        else:
                            new_elapsed = elapsed + delta
                            conn.execute("UPDATE tv_configs SET elapsed = ? WHERE name = ?", (new_elapsed, name))
                            rem = int(max(0, limit - new_elapsed))
                            mqtt_client.publish(f"kidslock/{slug}/remaining", str(rem))
                            if new_elapsed >= limit: requests.post(f"http://{ip}:8081/lock", timeout=1)
                except: pass
            conn.commit(); conn.close()
        except: pass
        time.sleep(30)

threading.Thread(target=monitor_task, daemon=True).start()

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    day_prefix = datetime.now().strftime("%a").lower()
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(f"SELECT name, ip, elapsed, no_limit, {day_prefix}_lim, {day_prefix}_bed FROM tv_configs").fetchall()
    conn.close()
    tvs = [{"name": r[0], "ip": r[1], "elapsed": round(r[2],1), "no_limit": r[3], "limit": r[4], "bedtime": r[5], "remaining": int(max(0, r[4]-r[2]))} for r in rows]
    return templates.TemplateResponse("index.html", {"request": request, "tvs": tvs})

@app.get("/settings", response_class=HTMLResponse)
async def settings_ui(request: Request):
    conn = sqlite3.connect(DB_PATH); rows = conn.execute("SELECT * FROM tv_configs").fetchall(); conn.close()
    tvs = []
    for r in rows:
        tvs.append({"name":r[0], "ip":r[1], "no_limit":r[2], "mon_lim":r[5], "mon_bed":r[6], "tue_lim":r[7], "tue_bed":r[8], "wed_lim":r[9], "wed_bed":r[10], "thu_lim":r[11], "thu_bed":r[12], "fri_lim":r[13], "fri_bed":r[14], "sat_lim":r[15], "sat_bed":r[16], "sun_lim":r[17], "sun_bed":r[18]})
    return templates.TemplateResponse("settings.html", {"request": request, "tvs": tvs})

@app.post("/api/update_tv")
async def update_tv(request: Request):
    d = await request.form()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE tv_configs SET name=?, no_limit=?, mon_lim=?, mon_bed=?, tue_lim=?, tue_bed=?, wed_lim=?, wed_bed=?, thu_lim=?, thu_bed=?, fri_lim=?, fri_bed=?, sat_lim=?, sat_bed=?, sun_lim=?, sun_bed=? WHERE name=?", (d['new_name'], int(d['no_limit']), d['mon_lim'], d['mon_bed'], d['tue_lim'], d['tue_bed'], d['wed_lim'], d['wed_bed'], d['thu_lim'], d['thu_bed'], d['fri_lim'], d['fri_bed'], d['sat_lim'], d['sat_bed'], d['sun_lim'], d['sun_bed'], d['old_name']))
    publish_discovery(); return {"status": "ok"}

@app.post("/api/delete_tv")
async def delete_tv(name: str = Form(...)):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM tv_configs WHERE name = ?", (name,))
    return {"status": "ok"}

@app.post("/api/tv_action")
async def tv_action(ip: str = Form(...), action: str = Form(...)):
    if action == "reset":
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE tv_configs SET elapsed = 0 WHERE ip = ?", (ip,))
        action = "unlock"
    try:
        requests.post(f"http://{ip}:8081/{action}", timeout=2)
        return {"status": "ok"}
    except: return {"status": "error"}

@app.post("/api/pair_with_device")
async def pair(ip: str=Form(...), code: str=Form(...)):
    try:
        r = requests.post(f"http://{ip}:8081/pair", data={"code": code, "api_key": secrets.token_hex(16)}, timeout=4)
        if r.status_code == 200:
            with sqlite3.connect(DB_PATH) as conn:
                tv_name = f"TV_{ip.split('.')[-1]}"
                conn.execute("INSERT OR REPLACE INTO tv_configs (name, ip, last_reset) VALUES (?, ?, ?)", (tv_name, ip, datetime.now().strftime("%Y-%m-%d")))
            publish_discovery(); return {"status": "success"}
    except: pass
    return JSONResponse({"status": "error"}, status_code=400)

if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port=8000)