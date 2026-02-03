import os
import re
import json
import ssl
import smtplib
from email.message import EmailMessage
from datetime import datetime
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


BASE_URL = "https://katona.jegymester.hu/main"
NO_EVENTS_TEXT = "Sajnáljuk, de az Ön által megadott szűrési feltételek alapján nem találtunk egy eseményt sem."

HU_MONTHS = {
    "január": 1, "február": 2, "március": 3, "április": 4, "május": 5, "június": 6,
    "július": 7, "augusztus": 8, "szeptember": 9, "október": 10, "november": 11, "december": 12
}


def budapest_now():
    return datetime.now(tz=ZoneInfo("Europe/Budapest"))


def build_url(active_page: int) -> str:
    # ugyanazokat a query paramétereket használjuk, mint a weboldal
    # (a searchPhrase üres marad)
    return (
        f"{BASE_URL}?activePage={active_page}"
        f"&osl=events&ot=tickets&searchPhrase="
    )


def page_is_empty(page) -> bool:
    """
    Üres oldal = megjelenik a kék info alert a NO_EVENTS_TEXT-tel.
    """
    try:
        # Megvárjuk, hogy vagy a lista/komponens, vagy az alert előjöjjön
        page.wait_for_selector("sat-productions-and-events-list, div.alert.alert-info", timeout=15000)
    except PlaywrightTimeoutError:
        # ha semmi sem jön, tekintsük üresnek / hibásnak
        return True

    empty_alert = page.locator("div.alert.alert-info", has_text=NO_EVENTS_TEXT)
    if empty_alert.count() > 0:
        return True

    # Biztonsági fallback: ha nincs alert, de semmilyen értelmes tartalom sincs,
    # akkor is lehet üres. (Óvatosan.)
    # Itt csak azt nézzük, hogy van-e legalább valami link/kártya.
    possible_event = page.locator("sat-productions-and-events-list a")
    if possible_event.count() == 0:
        # lehet layout változott, de nem akarunk végtelen loopot -> üresnek vesszük
        return True

    return False


def extract_dates_from_text(text: str):
    """
    Dátum kinyerés teljes oldal textből.
    Több minta:
      1) 2026.01.30 (vagy 2026. 01. 30)
      2) 2026-01-30
      3) 2026. január 30.
      4) 30 január 2026
    """
    dates = []

    # 1) 2026.01.30
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

    # 4) 30 január 2026
    for m in re.finditer(
        r"\b(0?[1-9]|[12]\d|3[01])\s*(január|február|március|április|május|június|július|augusztus|szeptember|október|november|december)\s*(20\d{2})\b",
        text,
        re.IGNORECASE
    ):
        d = int(m.group(1))
        mon_name = m.group(2).lower()
        y = int(m.group(3))
        mo = HU_MONTHS.get(mon_name)
        if mo:
            try:
                dates.append(datetime(y, mo, d).date())
            except ValueError:
                pass

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

    # TO_EMAILS: vesszővel vagy pontosvesszővel elválasztott lista
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


def find_last_nonempty_page(page, max_pages=60) -> int:
    """
    Bináris kereséssel megkeresi az utolsó oldalt, ahol még van esemény.
    Feltételezés: ha egy oldal üres, az utána következők is üresek.
    """
    # Először gyors sanity: page 1 legyen nem üres, különben valami gond van
    page.goto(build_url(1), wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1200)
    if page_is_empty(page):
        return 0

    # találjunk egy upper boundot, ami már üres
    lo = 1
    hi = None
    step = 1
    probe = 1

    while True:
        probe = lo + step
        if probe > max_pages:
            # nem találtunk üreset max_pages-ig -> tekintsük max_pages-nek
            hi = max_pages + 1
            break

        page.goto(build_url(probe), wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1200)

        if page_is_empty(page):
            hi = probe
            break

        lo = probe
        step *= 2  # exponenciális növelés

    # most lo biztos nem üres, hi biztos üres (vagy max_pages+1)
    left = lo
    right = hi  # üres

    # bináris keresés: utolsó nem üres = left
    while left + 1 < right:
        mid = (left + right) // 2
        page.goto(build_url(mid), wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1200)

        if page_is_empty(page):
            right = mid
        else:
            left = mid

    return left


def scrape_latest_date(page, last_page: int):
    """
    Végigmegy 1..last_page, és kinyeri a max dátumot.
    """
    all_dates = []

    for p in range(1, last_page + 1):
        page.goto(build_url(p), wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1200)

        if page_is_empty(page):
            # elvileg last_page-ig nem szabadna üresnek lennie,
            # de ha mégis: ugorjuk át
            continue

        text = page.inner_text("body")
        dates = extract_dates_from_text(text)
        all_dates.extend(dates)

    if not all_dates:
        return None

    return max(all_dates)


def main():
    # --- Playwright ---
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Kicsit "normál" böngészőnek látszódjunk
        page.set_viewport_size({"width": 1280, "height": 900})

        last_page = find_last_nonempty_page(page, max_pages=60)
        if last_page == 0:
            subject = "Katona jegymester – hiba (1. oldal is üres)"
            body = (
                "A script szerint már az 1. oldal is üres.\n"
                "Lehet hálózati hiba, oldalváltozás vagy blokkolás.\n"
            )
            send_email(subject, body)
            browser.close()
            return

        latest = scrape_latest_date(page, last_page)
        browser.close()

    if latest is None:
        subject = "Katona jegymester – hiba (nem találtam dátumot)"
        body = (
            "Nem sikerült dátumokat kinyerni.\n"
            f"Utolsó nem üres oldal (becslés): {last_page}\n"
        )
        send_email(subject, body)
        return

    # --- state ---
    state = load_state("state.json")
    prev_str = state.get("latest_date")
    prev = datetime.strptime(prev_str, "%Y-%m-%d").date() if prev_str else None

    state["latest_date"] = latest.isoformat()
    state["last_page"] = last_page
    state["checked_at_budapest"] = budapest_now().isoformat()
    save_state(state, "state.json")

    # --- email content (mindig küldünk) ---
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
            "Furcsa változás (törlés / szűrés / oldalváltozás lehet).\n\n"
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
