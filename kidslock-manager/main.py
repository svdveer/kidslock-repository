# FIX voor v1.1.9: Gebruik relatieve paden voor Ingress
@app.post("/toggle_lock/{name}")
async def toggle(name: str, request: Request):
    with data_lock:
        if name in tv_states:
            action = "unlock" if tv_states[name]["locked"] else "lock"
            ip = tv_states[name]["config"]["ip"]
            try:
                # Direct commando naar de TV
                requests.post(f"http://{ip}:8080/{action}", timeout=2)
                tv_states[name]["locked"] = not tv_states[name]["locked"]
                tv_states[name]["manual_override"] = True
                logger.info(f"TV {name} handmatig op {action} gezet")
            except Exception as e:
                logger.error(f"Fout bij communicatie met TV {name}: {e}")

    # Belangrijk: Redirect naar de huidige directory om binnen Ingress te blijven
    return RedirectResponse(url="./", status_code=303)