import logging, threading, time, sqlite3, requests, socket, json, secrets, concurrent.futures
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KidsLock")
DB_PATH = "/data/kidslock.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('CREATE TABLE IF NOT EXISTS tv_configs (name TEXT PRIMARY KEY, ip TEXT, no_limit INTEGER DEFAULT 0, elapsed REAL DEFAULT 0, last_reset TEXT, schedule TEXT)')
    conn.commit(); conn.close()

init_db()
app = FastAPI(); templates = Jinja2Templates(directory="templates")

@app.get("/settings", response_class=HTMLResponse)
async def settings_ui(request: Request):
    conn = sqlite3.connect(DB_PATH); rows = conn.execute("SELECT name, ip, no_limit FROM tv_configs").fetchall(); conn.close()
    tvs = [{"name": r[0], "ip": r[1], "no_limit": r[2]} for r in rows]
    return templates.TemplateResponse("settings.html", {"request": request, "tvs": tvs})

# --- SCANNER LOGICA ---
@app.get("/api/discover")
async def discover():
    found = []
    base_ip = "192.168.2." # PAS DIT AAN NAAR JOUW IP REEKS
    
    def check_device(i):
        target = f"{base_ip}{i}"
        try:
            with socket.create_connection((target, 8081), timeout=0.1):
                r = requests.get(f"http://{target}:8081/device_info", timeout=0.3)
                if r.status_code == 200 and "KidsLock_Client" in r.text:
                    data = r.json()
                    return {"name": data['name'], "ip": target}
        except: return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=100) as executor:
        results = list(executor.map(check_device, range(1, 255)))
    return [r for r in results if r]

@app.post("/api/pair_with_device")
async def pair_with_device(ip: str = Form(...), code: str = Form(...)):
    api_key = secrets.token_hex(16)
    try:
        r = requests.post(f"http://{ip}:8081/pair", data={"code": code, "api_key": api_key}, timeout=2)
        if r.status_code == 200:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("INSERT OR REPLACE INTO tv_configs (name, ip, no_limit, elapsed) VALUES (?, ?, ?, 0)", (f"TV_{ip.split('.')[-1]}", ip, 0))
            return {"status": "success"}
    except: pass
    return JSONResponse({"status": "error"}, status_code=400)

@app.post("/api/delete_tv/{name}")
async def delete_tv(name: str):
    with sqlite3.connect(DB_PATH) as conn: conn.execute("DELETE FROM tv_configs WHERE name=?", (name,))
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)