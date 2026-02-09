import logging, threading, time, sqlite3, requests, socket, json, secrets, concurrent.futures
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import uvicorn

# --- CONFIGURATIE ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KidsLock")
DB_PATH = "/data/kidslock.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''CREATE TABLE IF NOT EXISTS tv_configs 
                    (name TEXT PRIMARY KEY, ip TEXT, no_limit INTEGER DEFAULT 0, 
                     elapsed REAL DEFAULT 0, last_reset TEXT, daily_limit INTEGER DEFAULT 120)''')
    conn.commit()
    conn.close()

init_db()
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# --- MONITOR LOOP (De 'Politieagent') ---
def monitor_task():
    logger.info("KidsLock Monitor gestart...")
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
                # 1. Dagelijkse Reset
                if last_reset != today:
                    conn.execute("UPDATE tv_configs SET elapsed = 0, last_reset = ? WHERE name = ?", (today, name))
                    elapsed = 0
                
                # 2. Check of TV online is (poort 8081)
                online = False
                try:
                    with socket.create_connection((ip, 8081), timeout=0.5):
                        online = True
                except: pass

                # 3. Tijd optellen en Lock sturen
                if online:
                    if not no_limit:
                        new_elapsed = elapsed + delta
                        conn.execute("UPDATE tv_configs SET elapsed = ? WHERE name = ?", (new_elapsed, name))
                        
                        # Als tijd op is -> Stuur LOCK naar de App
                        if new_elapsed >= daily_limit:
                            try: requests.post(f"http://{ip}:8081/lock", timeout=1)
                            except: pass
                    else:
                        # Als Unlimited aan staat -> Zorg dat hij UNLOCKED blijft
                        try: requests.post(f"http://{ip}:8081/unlock", timeout=1)
                        except: pass
            
            conn.commit()
            conn.close()
        except Exception as e: 
            logger.error(f"Monitor error: {e}")
        time.sleep(30)

threading.Thread(target=monitor_task, daemon=True).start()

# --- ROUTES ---
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT name, ip, elapsed, no_limit, daily_limit FROM tv_configs").fetchall()
    conn.close()
    tvs_data = []
    for r in rows:
        tvs_data.append({
            "name": r[0], "ip": r[1], "elapsed": round(r[2], 1),
            "no_limit": r[3], "limit": r[4],
            "remaining": int(max(0, r[4] - r[2]))
        })
    return templates.TemplateResponse("index.html", {"request": request, "tvs": tvs_data})

@app.get("/settings", response_class=HTMLResponse)
async def settings_ui(request: Request):
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT name, ip, no_limit, daily_limit FROM tv_configs").fetchall()
    conn.close()
    tvs = [{"name": r[0], "ip": r[1], "no_limit": r[2], "limit": r[3]} for r in rows]
    return templates.TemplateResponse("settings.html", {"request": request, "tvs": tvs})

# --- API ACTIES ---
@app.post("/api/tv_action")
async def tv_action(ip: str = Form(...), action: str = Form(...)):
    try:
        if action == "reset":
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("UPDATE tv_configs SET elapsed = 0 WHERE ip = ?", (ip,))
            requests.post(f"http://{ip}:8081/unlock", timeout=2)
        else:
            requests.post(f"http://{ip}:8081/{action}", timeout=2)
        return {"status": "ok"}
    except: 
        return JSONResponse({"status": "error"}, status_code=400)

@app.post("/api/update_tv")
async def update_tv(old_name: str = Form(...), new_name: str = Form(...), daily_limit: int = Form(...), no_limit: int = Form(...)):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE tv_configs SET name=?, daily_limit=?, no_limit=? WHERE name=?", 
                     (new_name, daily_limit, no_limit, old_name))
    return {"status": "ok"}

@app.get("/api/discover")
async def discover(request: Request):
    try:
        base_ip = ".".join(request.client.host.split(".")[:3]) + "."
    except:
        base_ip = "192.168.2."
    
    def check_device(i):
        target = f"{base_ip}{i}"
        try:
            with socket.create_connection((target, 8081), timeout=0.15):
                r = requests.get(f"http://{target}:8081/device_info", timeout=0.3)
                if r.status_code == 200: return {"name": r.json()['name'], "ip": target}
        except: return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=80) as executor:
        results = list(executor.map(check_device, range(1, 255)))
    return [r for r in results if r]

@app.post("/api/pair_with_device")
async def pair_with_device(ip: str = Form(...), code: str = Form(...)):
    api_key = secrets.token_hex(16)
    try:
        r = requests.post(f"http://{ip}:8081/pair", data={"code": code, "api_key": api_key}, timeout=4)
        if r.status_code == 200:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("INSERT OR REPLACE INTO tv_configs (name, ip, no_limit, elapsed, last_reset) VALUES (?, ?, ?, 0, ?)", 
                             (f"TV_{ip.split('.')[-1]}", ip, 0, datetime.now().strftime("%Y-%m-%d")))
            return {"status": "success"}
    except: pass
    return JSONResponse({"status": "error"}, status_code=400)

@app.post("/api/delete_tv/{name}")
async def delete_tv(name: str):
    with sqlite3.connect(DB_PATH) as conn: 
        conn.execute("DELETE FROM tv_configs WHERE name=?", (name,))
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)