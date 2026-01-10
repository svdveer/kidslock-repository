import logging
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KidsLock")

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    logger.info("Dashboard bezocht")
    return "<h1>KidsLock Diagnose v1.4.0</h1><p>De server draait!</p>"

if __name__ == "__main__":
    logger.info("KidsLock start nu op...")
    uvicorn.run(app, host="0.0.0.0", port=8000)