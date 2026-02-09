import logging, threading, time, sqlite3, requests, socket, json, secrets, concurrent.futures
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import uvicorn

# --- INITIALISATIE ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KidsLock")
DB_PATH = "/data/kidslock.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('CREATE TABLE IF NOT EXISTS tv_configs (name TEXT PRIMARY KEY, ip TEXT, no_limit INTEGER DEFAULT 0, elapsed REAL DEFAULT 0, last_reset TEXT, schedule TEXT)')
    conn.commit()
    conn.close()

init_db()
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# --- WEB UI ROUTES ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT name, ip, elapsed, no_limit FROM tv_configs").fetchall()
    conn.close()
    tvs = [{"name": r[0], "ip": r[1], "elapsed": round(r[2], 1), "no_limit": r[3]} for r in rows]
    return templates.TemplateResponse("index.html", {"request": request, "tvs": tvs})

@app.get("/settings", response_class=HTMLResponse)
async def settings_ui(request: Request):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT name, ip, no_limit FROM tv_configs").fetchall()
    conn.close()
    tvs = [{"name": r[0], "ip": r[1], "no_limit": r[2]} for r in rows]
    return templates.TemplateResponse("settings.html", {"request": request, "tvs": tvs})

# --- SCANNER & PAIRING API ---
@app.get("/api/discover")
async def discover(request: Request):
    found = []
    # Probeert de IP-range van je netwerk te raden (bijv. 192.168.2.)
    try:
        base_ip = ".".join(request.client.host.split(".")[:3]) + "."
    except:
        base_ip = "192.168.2." 
    
    def check_device(i):
        target = f"{base_ip}{i}"
        try:
            # We kijken of poort 8081 (KidsLock) open staat
            with socket.create_connection((target, 8081), timeout=0.2):
                r = requests.get(f"http://{target}:8081/device_info", timeout=0.4)
                if r.status_code == 200:
                    data = r.json()
                    data['ip'] = target
                    return data
        except:
            return None

    # Scant 254 IP-adressen tegelijkertijd binnen ~2 seconden
    with concurrent.futures.ThreadPoolExecutor(max_workers=100) as executor:
        results = list(executor.map(check_device, range(1, 255)))
    
    return [r for r in results if r]

@app.post("/api/pair_with_device")
async def pair_with_device(ip: str = Form(...), code: str = Form(...)):
    api_key = secrets.token_hex(16)
    try:
        # Stuur de pairing-opdracht NAAR de TV
        r = requests.post(f"http://{ip}:8081/pair", data={"code": code, "api_key": api_key}, timeout=3)
        if r.status_code == 200:
            with sqlite3.connect(DB_PATH) as conn:
                device_name = f"Android_TV_{ip.split('.')[-1]}"
                conn.execute("INSERT OR REPLACE INTO tv_configs (name, ip, no_limit, elapsed, last_reset) VALUES (?, ?, ?, 0, ?)", 
                             (device_name, ip, 0, datetime.now().strftime("%Y-%m-%d")))
            return {"status": "success", "name": device_name}
    except Exception as e:
        logger.error(f"Pairing error: {e}")
    
    return JSONResponse({"status": "error", "message": "Code onjuist of TV onbereikbaar"}, status_code=400)

@app.post("/api/delete_tv/{name}")
async def delete_tv(name: str):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM tv_configs WHERE name=?", (name,))
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)