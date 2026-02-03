import os
import re
import json
import smtplib
import ssl
from email.message import EmailMessage
from datetime import datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

BASE_URL = "https://katona.jegymester.hu/main"
NO_EVENTS_TEXT = "Sajnáljuk, de az Ön által megadott szűrési feltételek alapján nem találtunk egy eseményt sem."

HU_MONTHS = {
    "január": 1, "február": 2, "március": 3, "április": 4, "május": 5, "június": 6,
    "július": 7, "augusztus": 8, "szeptember": 9, "október": 10, "november": 11, "december": 12
}

def budapest_now():
    return datetime.now(tz=ZoneInfo("Europe/Budapest"))

def should_send_now() -> bool:
    """
    Only send at 08:00 Budapest time.
    Allow manual run override with FORCE_SEND=1.
    """
    if os.environ.get("FORCE_SEND", "").strip() == "1":
        return True
    now = budapest_now()
    return now.hour == 8

def build_url(active_page: int) -> str:
    # Keep it identical to what you use in browser:
    # /main?activePage=1&osl=events&ot=tickets&searchPhrase=
    return f"{BASE_URL}?activePage={active_page}&osl=events&ot=tickets&searchPhrase="

def page_is_empty(rendered_html: str) -> bool:
    # We check rendered DOM (Playwright gives final HTML after JS)
    return NO_EVENTS_TEXT in rendered_html

def extract_dates_from_rendered_html(rendered_html: str):
    """
    Extract event dates from RENDERED HTML (post-JS).
    Returns sorted unique list of date objects.
    """
    soup = BeautifulSoup(rendered_html, "html.parser")
    text = soup.get_text(" ", strip=True)

    dates = []

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
    for m in re.finditer(
        r"\b(20\d{2})\.\s*(január|február|március|április|május|június|július|augusztus|szeptember|október|november|december)\s*(0?[1-9]|[12]\d|3[01])\.?\b",
        text,
        re.IGNORECASE
    ):
        y = int(m.group(1))
        mon_name = m.group(2).lower()
        d = int(m.group(3))
        mo = HU_MONTHS.get(mon_name)
        if mo:
            try:
                dates.append(datetime(y, mo, d).date())
            except ValueError:
                pass

    # de-dup
    return sorted(set(dates))

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

def get_rendered_html_for_page(page_obj, active_page: int) -> str:
    url = build_url(active_page)

    # "networkidle" sometimes never happens on SPAs;
    # so we do a safe approach: domcontentloaded + a short wait.
    page_obj.goto(url, wait_until="domcontentloaded", timeout=60000)
    page_obj.wait_for_timeout(1500)

    # wait a bit for the list or alert to appear
    # if selector exists, great; if not, still proceed
    try:
        page_obj.wait_for_selector("div.alert", timeout=7000)
    except Exception:
        pass

    return page_obj.content()

def is_nonempty_page(page_obj, active_page: int) -> bool:
    html = get_rendered_html_for_page(page_obj, active_page)
    return not page_is_empty(html)

def find_last_nonempty_page(page_obj, start_page=1, max_page_cap=200) -> int:
    """
    Find last page that still has events.
    Strategy: exponential search to find an empty page, then binary search.
    Returns last_nonempty_page (>=1) or 0 if page1 is empty.
    """
    if not is_nonempty_page(page_obj, start_page):
        return 0

    lo = start_page
    hi = start_page

    # exponential climb until we find an empty page or hit cap
    step = 1
    while True:
        next_hi = hi + step
        if next_hi > max_page_cap:
            next_hi = max_page_cap

        if is_nonempty_page(page_obj, next_hi):
            lo = next_hi
            hi = next_hi
            if hi >= max_page_cap:
                return lo
            step *= 2
        else:
            hi = next_hi
            break

    # binary search between (lo nonempty) and (hi empty)
    left = lo
    right = hi
    while left + 1 < right:
        mid = (left + right) // 2
        if is_nonempty_page(page_obj, mid):
            left = mid
        else:
            right = mid

    return left

def find_latest_event_date(page_obj, last_page: int):
    """
    Scan pages 1..last_page and find max date in rendered text.
    """
    all_dates = []
    for p in range(1, last_page + 1):
        html = get_rendered_html_for_page(page_obj, p)
        dates = extract_dates_from_rendered_html(html)
        all_dates.extend(dates)
    if not all_dates:
        return None
    return max(all_dates)

def main():
    if not should_send_now():
        print("Not 08:00 in Budapest, exiting without sending.")
        return

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(locale="hu-HU")
        page = context.new_page()

        last_nonempty = find_last_nonempty_page(page, start_page=1, max_page_cap=200)
        if last_nonempty == 0:
            subject = "Katona jegymester – hiba (már az 1. oldal is üres)"
            body = "Az 1. oldalon sincs esemény (vagy a oldal nem töltött be rendesen)."
            send_email(subject, body)
            browser.close()
            return

        latest = find_latest_event_date(page, last_nonempty)

        browser.close()

    if latest is None:
        subject = "Katona jegymester – hiba (nem találtam dátumot)"
        body = f"Nem sikerült dátumot kinyerni. Utolsó nem üres oldal: {last_nonempty}"
        send_email(subject, body)
        return

    state = load_state("state.json")
    prev_str = state.get("latest_date")
    prev = datetime.strptime(prev_str, "%Y-%m-%d").date() if prev_str else None

    state["latest_date"] = latest.isoformat()
    state["last_page"] = last_nonempty
    state["checked_at_budapest"] = budapest_now().isoformat()
    save_state(state, "state.json")

    if prev is None:
        subject = "Katona jegymester – első futás"
        body = (
            "Első futás (nincs korábbi összehasonlítás).\n\n"
            f"Legutolsó (max) dátum: {latest.isoformat()}\n"
            f"Utolsó nem üres oldal: {last_nonempty}\n"
        )
    elif latest > prev:
        subject = "Katona jegymester – ÚJ dátum került fel"
        body = (
            "Változás!\n\n"
            f"Korábbi max dátum: {prev.isoformat()}\n"
            f"Új max dátum:      {latest.isoformat()}\n"
            f"Utolsó nem üres oldal: {last_nonempty}\n"
        )
    elif latest < prev:
        subject = "Katona jegymester – FIGYELEM: a max dátum csökkent"
        body = (
            "Furcsa változás (lehet törlés / szűrés / oldalváltozás).\n\n"
            f"Korábbi max dátum: {prev.isoformat()}\n"
            f"Mostani max dátum: {latest.isoformat()}\n"
            f"Utolsó nem üres oldal: {last_nonempty}\n"
        )
    else:
        subject = "Katona jegymester – nincs változás"
        body = (
            "Nincs változás.\n\n"
            f"Max dátum továbbra is: {latest.isoformat()}\n"
            f"Utolsó nem üres oldal: {last_nonempty}\n"
        )

    send_email(subject, body)
    print("Email sent.")

if __name__ == "__main__":
    main()
