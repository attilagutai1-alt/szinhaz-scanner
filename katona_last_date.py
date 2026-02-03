import os
import re
import json
import smtplib
import ssl
from email.message import EmailMessage
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


BASE_URL = "https://katona.jegymester.hu/main"
NO_EVENTS_TEXT = "Sajnáljuk, de az Ön által megadott szűrési feltételek alapján nem találtunk egy eseményt sem."


HU_MONTHS = {
    "január": 1, "február": 2, "március": 3, "április": 4, "május": 5, "június": 6,
    "július": 7, "augusztus": 8, "szeptember": 9, "október": 10, "november": 11, "december": 12
}


def budapest_now():
    return datetime.now(tz=ZoneInfo("Europe/Budapest"))


def fetch_page(active_page: int) -> str:
    params = {
        "activePage": str(active_page),
        "osl": "events",
        "ot": "tickets",
        "searchPhrase": ""
    }
    r = requests.get(BASE_URL, params=params, timeout=30)
    r.raise_for_status()
    return r.text


def extract_dates_from_html(html: str):
    """
    Extract event dates from HTML.
    We try multiple patterns to be robust:
      1) YYYY.MM.DD
      2) YYYY-MM-DD
      3) YYYY. <hu month> DD.  (e.g. 2026. január 30.)
      4) DD <hu month> YYYY    (rare, but just in case)
    Returns a list of datetime.date
    """
    dates = []

    # quick stop condition
    if NO_EVENTS_TEXT in html:
        return dates

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    # 1) 2026.01.30 or 2026. 01. 30
    for m in re.finditer(r"\b(20\d{2})\.\s*(0?[1-9]|1[0-2])\.\s*(0?[1-9]|[12]\d|3[01])\b", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dates.append(datetime(y, mo, d).date())
        except ValueError:
            pass

    # 2) 2026-01-30
    for m in re.finditer(r"\b(20\d{2})-(0?[1-9]|1[0-2])-(0?[1-9]|[12]\d|3[01])\b", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dates.append(datetime(y, mo, d).date())
        except ValueError:
            pass

    # 3) 2026. január 30.
    for m in re.finditer(r"\b(20\d{2})\.\s*(január|február|március|április|május|június|július|augusztus|szeptember|október|november|december)\s*(0?[1-9]|[12]\d|3[01])\.?\b", text, re.IGNORECASE):
        y = int(m.group(1))
        mon_name = m.group(2).lower()
        d = int(m.group(3))
        mo = HU_MONTHS.get(mon_name)
        if mo:
            try:
                dates.append(datetime(y, mo, d).date())
            except ValueError:
                pass

    # 4) 30 január 2026
    for m in re.finditer(r"\b(0?[1-9]|[12]\d|3[01])\s*(január|február|március|április|május|június|július|augusztus|szeptember|október|november|december)\s*(20\d{2})\b", text, re.IGNORECASE):
        d = int(m.group(1))
        mon_name = m.group(2).lower()
        y = int(m.group(3))
        mo = HU_MONTHS.get(mon_name)
        if mo:
            try:
                dates.append(datetime(y, mo, d).date())
            except ValueError:
                pass

    # de-dup
    dates = list(sorted(set(dates)))
    return dates


def find_latest_event_date(max_pages=50):
    """
    Walk pages until we hit the 'no events' message OR we stop finding dates.
    Returns (latest_date, last_page_checked)
    """
    all_dates = []
    last_nonempty_page = 0

    for page in range(1, max_pages + 1):
        html = fetch_page(page)

        if NO_EVENTS_TEXT in html:
            break

        page_dates = extract_dates_from_html(html)
        if page_dates:
            all_dates.extend(page_dates)
            last_nonempty_page = page
        else:
            # if a page returns no dates, still could be layout change — stop to avoid looping forever
            break

    if not all_dates:
        return None, last_nonempty_page

    return max(all_dates), last_nonempty_page


def load_state(path="state.json"):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state, path="state.json"):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def send_email(subject: str, body: str):
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]
    to_emails_raw = os.environ["TO_EMAILS"]

    # allow commas or semicolons; spaces are ok
    to_emails = [e.strip() for e in re.split(r"[;,]", to_emails_raw) if e.strip()]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(to_emails)
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)


def main():
    
    latest, last_page = find_latest_event_date(max_pages=50)
    if latest is None:
        subject = "Katona jegymester – hiba (nem találtam dátumot)"
        body = (
            "Nem sikerült dátumokat kinyerni az oldalról.\n"
            "Lehet, hogy megváltozott az oldal szerkezete.\n"
            f"Utolsó ellenőrzött oldal: {last_page}\n"
        )
        send_email(subject, body)
        return

    state = load_state("state.json")
    prev_str = state.get("latest_date")
    prev = datetime.strptime(prev_str, "%Y-%m-%d").date() if prev_str else None

    state["latest_date"] = latest.isoformat()
    state["last_page"] = last_page
    state["checked_at_budapest"] = budapest_now().isoformat()
    save_state(state, "state.json")

    if prev is None:
        subject = "Katona jegymester – első futás"
        body = (
            "Első futás (nincs korábbi összehasonlítás).\n\n"
            f"Legutolsó (max) dátum: {latest.isoformat()}\n"
            f"Utolsó nem üres oldal: {last_page}\n"
        )
    elif latest > prev:
        subject = "Katona jegymester – ÚJ dátum került fel"
        body = (
            "Változás!\n\n"
            f"Korábbi max dátum: {prev.isoformat()}\n"
            f"Új max dátum:      {latest.isoformat()}\n"
            f"Utolsó nem üres oldal: {last_page}\n"
        )
    elif latest < prev:
        subject = "Katona jegymester – FIGYELEM: a max dátum csökkent"
        body = (
            "Furcsa változás (lehet törlés / szűrés / oldalváltozás).\n\n"
            f"Korábbi max dátum: {prev.isoformat()}\n"
            f"Mostani max dátum: {latest.isoformat()}\n"
            f"Utolsó nem üres oldal: {last_page}\n"
        )
    else:
        subject = "Katona jegymester – nincs változás"
        body = (
            "Nincs változás.\n\n"
            f"Max dátum továbbra is: {latest.isoformat()}\n"
            f"Utolsó nem üres oldal: {last_page}\n"
        )

    send_email(subject, body)
    print("Email sent.")


if __name__ == "__main__":
    main()

