from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import sqlite3
import requests
import json
import math
import smtplib
import ssl
from email.message import EmailMessage
from datetime import datetime
from threading import Thread
import time

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

DB_PATH = "agent.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY,
            sender_email TEXT,
            sender_password TEXT,
            recipient_email TEXT,
            openrouter_api_key TEXT,
            radius_km INTEGER,
            min_score INTEGER
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            message TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS sent_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link TEXT UNIQUE,
            portal TEXT,
            title TEXT,
            created_at TEXT
        )
    """)

    conn.commit()
    conn.close()

init_db()

def add_log(msg: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO logs (timestamp, message) VALUES (?, ?)", (datetime.utcnow().isoformat(), msg))
    conn.commit()
    conn.close()

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM settings WHERE id = 1")
    row = c.fetchone()
    conn.close()

    data = {
        "sender_email": row[1] if row else "",
        "sender_password": row[2] if row else "",
        "recipient_email": row[3] if row else "",
        "openrouter_api_key": row[4] if row else "",
        "radius_km": row[5] if row else 50,
        "min_score": row[6] if row else 6,
    }

    return templates.TemplateResponse("settings.html", {"request": request, "data": data})

@app.post("/settings")
def save_settings(
    sender_email: str = Form(...),
    sender_password: str = Form(...),
    recipient_email: str = Form(...),
    openrouter_api_key: str = Form(...),
    radius_km: int = Form(...),
    min_score: int = Form(...)
):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("DELETE FROM settings WHERE id = 1")
    c.execute("""
        INSERT INTO settings (id, sender_email, sender_password, recipient_email, openrouter_api_key, radius_km, min_score)
        VALUES (1, ?, ?, ?, ?, ?, ?)
    """, (sender_email, sender_password, recipient_email, openrouter_api_key, radius_km, min_score))

    conn.commit()
    conn.close()

    add_log("Zaktualizowano ustawienia")

    return RedirectResponse("/settings", status_code=303)

@app.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT timestamp, message FROM logs ORDER BY id DESC LIMIT 50")
    rows = c.fetchall()
    conn.close()

    return templates.TemplateResponse("logs.html", {"request": request, "logs": rows})

PORTALS = [
    ("OLX", "https://[Log in to view URL]"),
    ("Gratka", "https://[Log in to view URL]"),
    ("Sprzedajemy", "https://[Log in to view URL]"),
    ("Lento", "https://[Log in to view URL]"),
    ("Listi", "https://[Log in to view URL]"),
    ("TuOgłos", "https://[Log in to view URL]"),
    ("Gumtree", "https://[Log in to view URL]")
]

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html"
}

RUDA_LAT = 50.273
RUDA_LON = 18.856

def fetch_html(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            return r.text
        return None
    except:
        return None

def extract_ai(api_key, portal, html):
    if not html:
        return []

    prompt = f"""
Wyciągnij ogłoszenia budowlane z HTML.
Zwróć JSON:
[
  {{
    "portal": "{portal}",
    "title": "...",
    "description": "...",
    "location": "...",
    "link": "..."
  }}
]
HTML:
{html}
"""

    body = {
        "model": "qwen/qwen-2-7b-instruct",
        "messages": [
            {"role": "system", "content": "Ekstrakcja ogłoszeń budowlanych."},
            {"role": "user", "content": prompt}
        ]
    }

    try:
        r = requests.post(
            "https://[Log in to view URL]",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=30
        )
        content = r.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except:
        return []

def geocode(location):
    try:
        r = requests.get("https://[Log in to view URL]", params={"q": location, "limit": 1}, timeout=10)
        data = r.json()
        if data.get("items"):
            pos = data["items"][0]["position"]
            return pos["lat"], pos["lon"]
        return None
    except:
        return None

def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def score_ai(api_key, offer, distance):
    prompt = f"""
Oceń ogłoszenie budowlane (0-10).
Zwróć JSON:
{{
  "score": 0-10,
  "works": "...",
  "scale": "...",
  "reason": "..."
}}
Ogłoszenie:
{offer}
Odległość: {distance} km
"""

    body = {
        "model": "qwen/qwen-2-7b-instruct",
        "messages": [
            {"role": "system", "content": "Ocena ogłoszeń budowlanych."},
            {"role": "user", "content": prompt}
        ]
    }

    try:
        r = requests.post(
            "https://[Log in to view URL]",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=30
        )
        content = r.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except:
        return {"score": 0}

def is_duplicate(link):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT 1 FROM sent_links WHERE link = ?", (link,))
    exists = c.fetchone() is not None
    conn.close()
    return exists

def save_link(link, portal, title):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT OR IGNORE INTO sent_links (link, portal, title, created_at) VALUES (?, ?, ?, ?)",
        (link, portal, title, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()

def send_email(settings, offer, ai):
    msg = EmailMessage()
    msg["From"] = settings["sender_email"]
    msg["To"] = settings["recipientipient_email"]
    msg["Subject"] = "Nowe zlecenie ≥ 6/10"

    body = f"""
Portal: {offer['portal']}
Tytuł: {offer['title']}
Lokalizacja: {offer['location']}
Link: {offer['link']}

Ocena: {ai['score']}
Prace: {ai.get('works', '')}
Skala: {ai.get('scale', '')}
Powód: {ai.get('reason', '')}

Opis:
{offer['description']}
"""

    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(settings["sender_email"], settings["sender_password"])
        server.send_message(msg)

def run_agent():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM settings WHERE id = 1")
    row = c.fetchone()
    conn.close()

    if not row:
        add_log("Brak ustawień – agent nie działa")
        return

    settings = {
        "sender_email": row[1],
        "sender_password": row[2],
        "recipient_email": row[3],
        "openrouter_api_key": row[4],
        "radius_km": row[5],
        "min_score": row[6],
    }

    add_log("Agent startuje")

    all_offers = []

    for portal, url in PORTALS:
        html = fetch_html(url)
        offers = extract_ai(settings["openrouter_api_key"], portal, html)
        all_offers.extend(offers)

    sent = 0

    for offer in all_offers:
        link = offer.get("link")
        if not link or is_duplicate(link):
            continue

        geo = geocode(offer.get("location", ""))
        if not geo:
            continue

        lat, lon = geo
        distance = haversine(lat, lon, RUDA_LAT, RUDA_LON)

        if distance > settings["radius_km"]:
            continue

        ai = score_ai(settings["openrouter_api_key"], offer, distance)
        score = ai.get("score", 0)

        if score < settings["min_score"]:
            continue

        send_email(settings, offer, ai)
        save_link(link, offer["portal"], offer["title"])
        sent += 1

    add_log(f"Agent zakończył – wysłano {sent} maili")

def scheduler():
    while True:
        run_agent()
        time.sleep(3600)

Thread(target=scheduler, daemon=True).start()
