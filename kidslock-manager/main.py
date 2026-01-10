import logging, threading, time, json, os, requests, subprocess
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KidsLock")
OPTIONS_PATH = "/data/options.json"

def load_options():
    try:
        if os.path.exists(OPTIONS_PATH):
            with open(OPTIONS_PATH, "r") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {"tvs": []}
    except Exception as e:
        logger.error(f"Configuratie kon niet worden gelezen: {e}")
    return {"tvs": []}

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Initialisatie van data
options = load_options()
tv_states = {}
data_lock = threading.RLock()

# Veilig TV's laden, zelfs als de lijst leeg is
tvs_list = options.get("tvs")
if isinstance(tvs_list, list):
    for tv in tvs_list:
        name = tv.get("name", "Onbekende TV")
        ip = tv.get("ip")
        if ip:
            tv_states[name] = {
                "config": tv,
                "online": False,
                "locked": False,
                "remaining_minutes": float(tv.get("daily_limit", 120))
            }

def is_online(ip):
    try:
        # Snelle ping (1 seconde timeout) om vastlopen te voorkomen
        res = subprocess.run(['ping', '-c', '1', '-W', '1', str(ip)], 
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return res.returncode == 0
    except:
        return False

def monitor():
    logger.info("Monitor thread gestart.")
    while True:
        with data_lock:
            for name, state in tv_states.items():
                ip = state["config"].get("ip")
                if ip:
                    state["online"] = is_online(ip)
        time.sleep(30)

# Start monitor alleen als er TV's zijn, maar laat de app hoe dan ook draaien
threading.Thread(target=monitor, daemon=True).start()

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    tvs_display = []
    with data_lock:
        for name, s in tv_states.items():
            tvs_display.append({
                "name": name,
                "online": s["online"],
                "locked": s["locked"],
                "remaining": int(s.get("remaining_minutes", 0))
            })
    return templates.TemplateResponse("index.html", {"request": request, "tvs": tvs_display})

@app.post("/toggle_lock/{name}")
async def toggle(name: str):
    with data_lock:
        if name in tv_states:
            ip = tv_states[name]['config'].get('ip')
            action = "unlock" if tv_states[name]["locked"] else "lock"
            try:
                requests.post(f"http://{ip}:8080/{action}", timeout=2)
                tv_states[name]["locked"] = not tv_states[name]["locked"]
            except:
                logger.warning(f"Kon geen verbinding maken met {name} op {ip}")
    return RedirectResponse(url="./", status_code=303)

@app.post("/add_time/{name}")
async def add_time(name: str, minutes: int = Form(...)):
    with data_lock:
        if name in tv_states:
            tv_states[name]["remaining_minutes"] += minutes
    return RedirectResponse(url="./", status_code=303)

if __name__ == "__main__":
    logger.info("KidsLock Manager start op...")
    uvicorn.run(app, host="0.0.0.0", port=8000)