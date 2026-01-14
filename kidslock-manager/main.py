import logging, threading, time, sqlite3, requests, subprocess, json, os, datetime
import paho.mqtt.client as mqtt
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import uvicorn

# --- CONFIG & DB ---
DB_PATH = "/data/kidslock.db"
OPTIONS_PATH = "/data/options.json"

try:
    with open(OPTIONS_PATH, 'r') as f: options = json.load(f)
except: options = {}

MQTT_HOST = options.get("mqtt_host", "core-mosquitto")
MQTT_PORT = options.get("mqtt_port", 1883)

def get_default_schedule():
    days = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]
    return {d: {"limit": 120, "bedtime": "20:00"} for d in days}

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS tv_configs 
                        (name TEXT PRIMARY KEY, ip TEXT, no_limit INTEGER DEFAULT 0, 
                         elapsed REAL DEFAULT 0, last_reset TEXT, schedule TEXT)''')
init_db()

tv_states = {}
data_lock = threading.RLock()
mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

# --- MQTT ---
def on_connect(client, userdata, flags, rc, properties=None):
    with data_lock:
        for name in tv_states:
            slug = name.lower().replace(" ", "_")
            client.subscribe(f"kidslock/{slug}/set")

mqtt_client.on_connect = on_connect
if options.get("mqtt_user"): mqtt_client.username_pw_set(options["mqtt_user"], options.get("mqtt_password"))
try:
    mqtt_client.connect_async(MQTT_HOST, MQTT_PORT)
    mqtt_client.loop_start()
except: pass

# --- MONITOR ---
def monitor():
    days_map = ["Maandag", "Dinsdag", "Woensdag", "Donderdag", "Vrijdag", "Zaterdag", "Zondag"]
    while True:
        try:
            with sqlite3.connect(DB_PATH) as conn:
                rows = conn.execute("SELECT name, ip, no_limit, elapsed, last_reset, schedule FROM tv_configs").fetchall()
            now = datetime.datetime.now()
            today = now.strftime("%Y-%m-%d")
            with data_lock:
                for name, ip, no_limit, elapsed, last_reset, sched_json in rows:
                    if last_reset != today:
                        elapsed = 0.0
                        with sqlite3.connect(DB_PATH) as c:
                            c.execute("UPDATE tv_configs SET elapsed = 0, last_reset = ? WHERE name = ?", (today, name))
                    if name not in tv_states:
                        tv_states[name] = {"ip": ip, "online": False, "locked": False, "manual_lock": False, "elapsed": elapsed}
                    s = tv_states[name]
                    # Update status...
                    slug = name.lower().replace(" ", "_")
                    mqtt_client.publish(f"kidslock/{slug}/state", "ON" if s.get("locked") else "OFF", retain=True)
        except Exception as e: print(f"Monitor error: {e}")
        time.sleep(30)

threading.Thread(target=monitor, daemon=True).start()

# --- WEB UI ---
app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    with data_lock:
        # Hier herstellen we de 'devices' variabele voor index.html
        devices_dict = {n.lower().replace(" ", "_"): {"name": n, **s} for n, s in tv_states.items()}
    return templates.TemplateResponse("index.html", {"request": request, "devices": devices_dict})

@app.get("/settings", response_class=HTMLResponse)
async def settings_ui(request: Request):
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT name, ip, no_limit, schedule FROM tv_configs").fetchall()
    processed_tvs = []
    for r in rows:
        processed_tvs.append({"name": r[0], "ip": r[1], "no_limit": r[2], "schedule": json.loads(r[3]) if r[3] else get_default_schedule()})
    # Hier sturen we 'tvs' door voor settings.html
    return templates.TemplateResponse("settings.html", {"request": request, "tvs": processed_tvs})

@app.post("/api/save_tv")
async def save_tv(name: str = Form(...), ip: str = Form(...), no_limit: int = Form(...), schedule: str = Form(...)):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR REPLACE INTO tv_configs (name, ip, no_limit, schedule) VALUES (?, ?, ?, ?)", (name, ip, no_limit, schedule))
    return RedirectResponse(url="./settings", status_code=303)

@app.post("/api/{action}/{name}")
async def api_handler(action: str, name: str, minutes: int = Form(None)):
    with data_lock:
        if name in tv_states:
            if action == "reset":
                with sqlite3.connect(DB_PATH) as c: c.execute("UPDATE tv_configs SET elapsed = 0 WHERE name = ?", (name,))
                tv_states[name]["elapsed"] = 0
            elif action == "toggle_lock":
                tv_states[name]["manual_lock"] = not tv_states[name]["manual_lock"]
    return RedirectResponse(url="./", status_code=303)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)