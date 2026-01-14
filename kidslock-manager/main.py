import os, json, time, sqlite3, requests, threading, datetime
import paho.mqtt.client as mqtt
from flask import Flask, request, jsonify

# --- DATABASE ---
DB_PATH = "/data/kidslock.db"
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS devices 
                 (slug TEXT PRIMARY KEY, name TEXT, ip TEXT, 
                  manual_lock BOOLEAN, minutes_used INTEGER, 
                  last_reset TEXT, schedule TEXT)''')
    conn.commit(); conn.close()
init_db()

def get_db_devices():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM devices")
    rows = c.fetchall()
    data = [dict(row) for row in rows]
    conn.close()
    return data

# --- FLASK ---
app = Flask(__name__)

@app.route('/')
def index():
    devices = get_db_devices()
    
    # RELATIEVE LINKS: We halen de / voor 'settings' en 'api' weg
    html = """
    <!DOCTYPE html><html><head>
    <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <title>KidsLock</title>
    </head><body class="container p-4 bg-light">
    <div class="d-flex justify-content-between mb-4">
        <h1>🔐 KidsLock Dashboard</h1>
        <a href="settings" class="btn btn-dark">Instellingen</a>
    </div>
    <div class="row">
    """
    
    if not devices:
        html += """
        <div class="col-12 text-center p-5 border bg-white rounded shadow-sm">
            <h2 class="text-warning">Geen apparaten gevonden!</h2>
            <p>De database is momenteel leeg.</p>
            <form action="api/add" method="POST">
                <input type="hidden" name="n" value="Woonkamer TV">
                <input type="hidden" name="i" value="192.168.2.79">
                <button type="submit" class="btn btn-warning fw-bold px-4">Klik hier om een Test TV toe te voegen</button>
            </form>
        </div>
        """
    else:
        for d in devices:
            btn_class = "btn-danger" if d['manual_lock'] else "btn-success"
            btn_text = "ONTGRENDELEN" if d['manual_lock'] else "VERGRENDELEN"
            html += f"""
            <div class="col-md-4 mb-3">
                <div class="card p-3 shadow-sm border-0" style="border-radius:15px;">
                    <h4 class="mb-1">{d['name']}</h4>
                    <p class="text-muted small">{d['ip']}</p>
                    <h2 class="text-center text-primary my-3">{d['minutes_used']} min</h2>
                    <button onclick="toggle('{d['slug']}')" class="btn {btn_class} w-100 fw-bold">{btn_text}</button>
                </div>
            </div>
            """

    html += """
    </div>
    <script>
    function toggle(slug) {
        // GEEN / voor api: dit houdt het verzoek binnen de ingress tunnel
        fetch('api/toggle/' + slug, { method: 'POST' }).then(() => location.reload());
    }
    </script>
    </body></html>
    """
    return html

@app.route('/settings')
def settings():
    # Ook hier: actie is 'api/add' (relatief), niet '/api/add'
    return """
    <body class="container p-4">
        <h1>Instellingen</h1>
        <form action="api/add" method="POST" class="card p-4 border-0 shadow-sm">
            <div class="mb-3"><label>Naam:</label><input name="n" class="form-control" required></div>
            <div class="mb-3"><label>IP Adres:</label><input name="i" class="form-control" required></div>
            <button class="btn btn-primary w-100">Apparaat Opslaan</button>
        </form>
        <br><a href="./" class="btn btn-link">← Terug naar Dashboard</a>
    </body>
    """

@app.route('/api/toggle/<slug>', methods=['POST'])
def toggle(slug):
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("UPDATE devices SET manual_lock = NOT manual_lock WHERE slug = ?", (slug,))
    conn.commit(); conn.close()
    return jsonify(success=True)

@app.route('/api/add', methods=['POST'])
def add():
    n, i = request.form['n'], request.form['i']
    s = n.lower().replace(" ", "_")
    sch = json.dumps({d:{"limit":60,"bedtime":"20:00"} for d in ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]})
    conn = sqlite3.connect(DB_PATH); c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO devices VALUES (?,?,?,0,0,'',?)", (s,n,i,sch))
    conn.commit(); conn.close()
    # De redirect moet ook relatief via JS om 404 te voorkomen
    return '<script>window.location.href="./";</script>'

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)