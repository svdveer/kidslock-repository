import logging, threading, time, json, os, requests, subprocess
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KidsLock")
OPTIONS_PATH = "/data/options.json"

# Veilig laden van opties met privacy-vriendelijke fallbacks
def load_options():
    try:
        if os.path.exists(OPTIONS_PATH):
            with open(OPTIONS_PATH, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Fout bij laden config: {e}")
    return {"tvs": []}

options = load_options()
data_lock = threading.RLock()
tv_states = {}

# Initialiseer alleen TV's die een naam en IP hebben
for tv in options.get("tvs", []):
    name = tv.get("name")
    ip = tv.get("ip")
    
    if name and ip:
        # Fallback naar 120 als de gebruiker niets invult in de UI
        limit = tv.get("daily_limit") if tv.get("daily_limit") is not None else 120
        tv_states[name] = {
            "config": tv,
            "online": False,
            "locked": False,
            "remaining_minutes": float(limit),
            "manual_override": False
        }
    else:
        logger.warning(f"TV configuratie onvolledig: {tv}")

def is_online(ip):
    try:
        res = subprocess.run(['ping', '-c', '1', '-W', '1', str(ip)], stdout=subprocess.DEVNULL)
        return res.returncode == 0
    except: return False

def monitor():
    while True:
        with data_lock:
            for name, state in tv_states.items():
                ip = state["config"].get("ip")
                if ip: state["online"] = is_online(ip)
        time.sleep(30)

threading.Thread(target=monitor, daemon=True).start()

app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    tvs_display = []
    with data_lock:
        for name, s in tv_states.items():
            tvs_display.append({
                "name": name,
                "online": s["online"],
                "locked": s["locked"],
                "remaining": int(s["remaining_minutes"]),
                "limit": s["config"].get("daily_limit") or 120,
                "bedtime": s["config"].get("bedtime") or "21:00",
                "no_limit": s["config"].get("no_limit_mode", False)
            })
    return templates.TemplateResponse("index.html", {"request": request, "tvs": tvs_display})

@app.post("/toggle_lock/{name}")
async def toggle(name: str):
    with data_lock:
        if name in tv_states:
            action = "unlock" if tv_states[name]["locked"] else "lock"
            ip = tv_states[name]['config'].get('ip')
            if ip:
                try:
                    requests.post(f"http://{ip}:8080/{action}", timeout=1.5)
                    tv_states[name]["locked"] = not tv_states[name]["locked"]
                except Exception as e:
                    logger.error(f"Communicatiefout met {name}: {e}")
    return RedirectResponse(url="./", status_code=303)

@app.post("/add_time/{name}")
async def add_time(name: str, minutes: int = Form(...)):
    with data_lock:
        if name in tv_states:
            tv_states[name]["remaining_minutes"] += minutes
    return RedirectResponse(url="./", status_code=303)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)