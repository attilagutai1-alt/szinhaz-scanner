"""
Örkény István Színház – utolsó elérhető dátum figyelő.

Az https://orkenyszinhaz.hu/jegyvasarlas/kereses/eloadas oldalról
scrape-eli az összes előadás dátumát (a "Továbbiak betöltése" gombot
ismételten megnyomva), majd emailben értesít, ha az utolsó dátum változott.
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


URL = "https://orkenyszinhaz.hu/jegyvasarlas/kereses/eloadas"
STATE_FILE = "orkeny_state.json"


def budapest_now():
    return datetime.now(tz=ZoneInfo("Europe/Budapest"))


def extract_dates_from_text(text: str) -> list[date]:
    """
    Dátumok kinyerése a szövegből.
    Az Örkény oldal formátuma: 2026.02.12. | 19:00
    """
    dates = []

    # Fő formátum: 2026.02.12.
    for m in re.finditer(r"\b(20\d{2})\.(0[1-9]|1[0-2])\.(0[1-9]|[12]\d|3[01])\.", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dates.append(date(y, mo, d))
        except ValueError:
            pass

    # Fallback: 2026-02-12 (URL-ekben is előfordul)
    for m in re.finditer(r"\b(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dates.append(date(y, mo, d))
        except ValueError:
            pass

    return sorted(set(dates))


def load_all_events(page, max_clicks: int = 50) -> str:
    """
    Betölti az összes eseményt a "Továbbiak betöltése" gomb ismételt
    megnyomásával. Visszaadja a teljes oldal szövegét.
    """
    print(f"[INFO] Oldal betöltése: {URL}")
    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)

    # Screenshot debug-hoz
    try:
        page.screenshot(path="debug_orkeny_page.png")
        print("[DEBUG] Screenshot mentve: debug_orkeny_page.png")
    except Exception as e:
        print(f"[WARNING] Screenshot hiba: {e}")

    # Ellenőrizzük, hogy vannak-e események
    body_text = page.inner_text("body")
    initial_dates = extract_dates_from_text(body_text)
    if not initial_dates:
        print("[ERROR] Nem találtam dátumokat az oldalon!")
        return body_text

    print(f"[DEBUG] Kezdeti dátumok száma: {len(initial_dates)}")
    print(f"[DEBUG] Első dátum: {min(initial_dates)}, utolsó: {max(initial_dates)}")

    # "Továbbiak betöltése" gomb kattintgatása
    click_count = 0
    for i in range(max_clicks):
        # Keressük a gombot — többféle szelektor is lehet
        load_more_btn = None
        for selector in [
            "text=Továbbiak betöltése",
            "button:has-text('Továbbiak')",
            ".load-more",
            "[class*='load-more']",
            "a:has-text('Továbbiak')",
            "div:has-text('Továbbiak betöltése') >> visible=true",
        ]:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=2000):
                    load_more_btn = btn
                    break
            except Exception:
                continue

        if load_more_btn is None:
            print(f"[INFO] Nincs több 'Továbbiak betöltése' gomb ({click_count} kattintás után)")
            break

        try:
            load_more_btn.click()
            click_count += 1
            # Várunk, hogy betöltődjön az új tartalom
            page.wait_for_timeout(2000)
            
            if click_count % 5 == 0:
                current_text = page.inner_text("body")
                current_dates = extract_dates_from_text(current_text)
                print(f"[DEBUG] {click_count}. kattintás után: {len(current_dates)} dátum, max: {max(current_dates) if current_dates else 'N/A'}")
        except Exception as e:
            print(f"[DEBUG] Gomb kattintás hiba a {click_count + 1}. próbálkozásnál: {e}")
            break

    print(f"[INFO] Összesen {click_count} 'Továbbiak betöltése' kattintás")

    # Végső screenshot
    try:
        page.screenshot(path="debug_orkeny_final.png")
        print("[DEBUG] Végső screenshot mentve: debug_orkeny_final.png")
    except Exception:
        pass

    return page.inner_text("body")


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
    print(f"[INFO] Örkény scraper indítása: {budapest_now()}")

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

        body_text = load_all_events(page)
        browser.close()

    all_dates = extract_dates_from_text(body_text)

    if not all_dates:
        subject = "Örkény Színház – hiba (nem találtam dátumot)"
        body = (
            "Nem sikerült dátumokat kinyerni az oldalról.\n"
            f"URL: {URL}\n"
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
        subject = "Örkény Színház – első futás"
        body = (
            "Első futás (nincs korábbi összehasonlítás).\n\n"
            f"Legkésőbbi dátum: {latest.isoformat()}\n"
            f"Egyedi dátumok száma: {event_count}\n"
            f"Ellenőrzés ideje: {budapest_now()}\n"
        )
    elif latest > prev:
        subject = "Örkény Színház – ÚJ dátum került fel!"
        body = (
            "Változás!\n\n"
            f"Korábbi max dátum: {prev.isoformat()}\n"
            f"Új max dátum:      {latest.isoformat()}\n"
            f"Egyedi dátumok száma: {event_count}\n"
            f"Ellenőrzés ideje: {budapest_now()}\n"
            f"\nNézd meg: {URL}\n"
        )
    elif latest < prev:
        subject = "Örkény Színház – FIGYELEM: a max dátum csökkent"
        body = (
            "Furcsa változás (törlés / szűrés / oldalváltozás lehet).\n\n"
            f"Korábbi max dátum: {prev.isoformat()}\n"
            f"Mostani max dátum: {latest.isoformat()}\n"
            f"Egyedi dátumok száma: {event_count}\n"
            f"Ellenőrzés ideje: {budapest_now()}\n"
        )
    else:
        subject = "Örkény Színház – nincs változás"
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
