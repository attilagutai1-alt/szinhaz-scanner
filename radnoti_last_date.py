"""
Radnóti Színház – utolsó elérhető dátum figyelő.

A https://radnotiszinhaz.hu/musor/ oldalról scrape-eli az előadások
dátumait havi bontásban (?offset=0,1,2,...), egészen addig, amíg
üres hónapot nem talál. Emailben értesít, ha a legkésőbbi dátum változott.
"""

import os
import re
import json
import ssl
import smtplib
from email.message import EmailMessage
from datetime import datetime, date
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


BASE_URL = "https://radnotiszinhaz.hu/musor/"
STATE_FILE = "radnoti_state.json"


def budapest_now():
    return datetime.now(tz=ZoneInfo("Europe/Budapest"))


def extract_month_info(text: str) -> tuple[int, int] | None:
    """
    Kiolvassa az oldal tetejéről az évet és hónapot.
    Formátum: "2026.02.01. — 2026.02.28."
    """
    m = re.search(r"(20\d{2})\.(0[1-9]|1[0-2])\.\d{2}\.\s*[—–-]\s*(20\d{2})\.(0[1-9]|1[0-2])\.\d{2}\.", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def extract_dates_for_month(text: str, year: int, month: int) -> list[date]:
    """
    Az adott hónapban kikeresi a napszámokat az eseménylistából.
    Az oldal struktúrája: "12.\ncsütörtök\n19:00\nElőadás neve"
    """
    dates = []

    # A napszámok a szövegben "N." formátumban jelennek meg (1-31)
    # Keresünk minden napszámot, ami napnév előtt áll
    day_pattern = re.finditer(
        r"\b(\d{1,2})\.\s*\n\s*(?:hétfő|kedd|szerda|csütörtök|péntek|szombat|vasárnap)",
        text,
        re.IGNORECASE
    )

    seen_days = set()
    for match in day_pattern:
        d = int(match.group(1))
        if 1 <= d <= 31 and d not in seen_days:
            seen_days.add(d)
            try:
                dates.append(date(year, month, d))
            except ValueError:
                pass

    return sorted(dates)


def extract_dates_from_range(text: str) -> list[date]:
    """
    Fallback: keresünk teljes dátumokat (2026.02.12. formátum)
    """
    dates = []
    for m in re.finditer(r"\b(20\d{2})\.(0[1-9]|1[0-2])\.(0[1-9]|[12]\d|3[01])\.", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dates.append(date(y, mo, d))
        except ValueError:
            pass
    return sorted(set(dates))


def scrape_all_months(page, max_months_ahead: int = 12) -> list[date]:
    """
    Végigmegy a hónapokon (offset=0, 1, 2, ...) amíg van esemény.
    """
    all_dates = []
    empty_streak = 0

    for offset in range(max_months_ahead):
        url = f"{BASE_URL}?offset={offset}"
        print(f"[DEBUG] Betöltés: offset={offset} ({url})")

        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
        except PlaywrightTimeoutError:
            print(f"[WARNING] Timeout offset={offset}, továbblépek")
            empty_streak += 1
            if empty_streak >= 2:
                break
            continue

        text = page.inner_text("body")

        # Screenshot az első oldalról debug-hoz
        if offset == 0:
            try:
                page.screenshot(path="debug_radnoti_page.png")
                print("[DEBUG] Screenshot mentve: debug_radnoti_page.png")
            except Exception:
                pass

        # Hónap info kinyerése
        month_info = extract_month_info(text)
        if month_info:
            year, month = month_info
            print(f"[DEBUG] Hónap: {year}.{month:02d}")

            # Napszámok kinyerése a struktúrából
            month_dates = extract_dates_for_month(text, year, month)

            # Fallback: teljes dátumok keresése
            if not month_dates:
                month_dates = extract_dates_from_range(text)

            if month_dates:
                print(f"[DEBUG] Dátumok: {len(month_dates)} db, {min(month_dates)} - {max(month_dates)}")
                all_dates.extend(month_dates)
                empty_streak = 0
            else:
                print(f"[DEBUG] Nincs esemény ebben a hónapban")
                empty_streak += 1
        else:
            print(f"[DEBUG] Nem sikerült hónap infót kinyerni")
            # Próbáljuk fallback-kel
            fallback_dates = extract_dates_from_range(text)
            if fallback_dates:
                all_dates.extend(fallback_dates)
                empty_streak = 0
            else:
                empty_streak += 1

        if empty_streak >= 2:
            print(f"[INFO] 2 egymást követő üres hónap, befejezem")
            break

    return sorted(set(all_dates))


def load_state(path: str = STATE_FILE) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict, path: str = STATE_FILE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def send_email(subject: str, body: str):
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    to_emails_raw = os.environ.get("TO_EMAILS")

    if not smtp_user or not smtp_pass or not to_emails_raw:
        print("[WARNING] Email credentials not set, printing email instead:")
        print(f"  Subject: {subject}")
        print(f"  Body:\n{body}")
        return

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
    print(f"[INFO] Radnóti scraper indítása: {budapest_now()}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()

        all_dates = scrape_all_months(page)
        browser.close()

    if not all_dates:
        subject = "Radnóti Színház – hiba (nem találtam dátumot)"
        body = (
            "Nem sikerült dátumokat kinyerni az oldalról.\n"
            f"URL: {BASE_URL}\n"
            f"Ellenőrzés ideje: {budapest_now()}\n"
        )
        send_email(subject, body)
        print(f"[ERROR] {subject}")
        return

    latest = max(all_dates)
    event_count = len(all_dates)
    print(f"[INFO] Összesen {event_count} egyedi dátum, legkésőbbi: {latest.isoformat()}")

    # State kezelés
    state = load_state()
    prev_str = state.get("latest_date")
    prev = datetime.strptime(prev_str, "%Y-%m-%d").date() if prev_str else None

    state["latest_date"] = latest.isoformat()
    state["event_count"] = event_count
    state["checked_at_budapest"] = budapest_now().isoformat()
    save_state(state)

    # Email
    if prev is None:
        subject = "Radnóti Színház – első futás"
        body = (
            "Első futás (nincs korábbi összehasonlítás).\n\n"
            f"Legkésőbbi dátum: {latest.isoformat()}\n"
            f"Egyedi dátumok száma: {event_count}\n"
            f"Ellenőrzés ideje: {budapest_now()}\n"
        )
    elif latest > prev:
        subject = "Radnóti Színház – ÚJ dátum került fel!"
        body = (
            "Változás!\n\n"
            f"Korábbi max dátum: {prev.isoformat()}\n"
            f"Új max dátum:      {latest.isoformat()}\n"
            f"Egyedi dátumok száma: {event_count}\n"
            f"Ellenőrzés ideje: {budapest_now()}\n"
            f"\nNézd meg: {BASE_URL}\n"
        )
    elif latest < prev:
        subject = "Radnóti Színház – FIGYELEM: a max dátum csökkent"
        body = (
            "Furcsa változás (törlés / szűrés / oldalváltozás lehet).\n\n"
            f"Korábbi max dátum: {prev.isoformat()}\n"
            f"Mostani max dátum: {latest.isoformat()}\n"
            f"Egyedi dátumok száma: {event_count}\n"
            f"Ellenőrzés ideje: {budapest_now()}\n"
        )
    else:
        subject = "Radnóti Színház – nincs változás"
        body = (
            "Nincs változás.\n\n"
            f"Max dátum továbbra is: {latest.isoformat()}\n"
            f"Egyedi dátumok száma: {event_count}\n"
            f"Ellenőrzés ideje: {budapest_now()}\n"
        )

    send_email(subject, body)
    print(f"[INFO] Email sent: {subject}")


if __name__ == "__main__":
    main()
