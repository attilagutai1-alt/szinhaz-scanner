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
# Javított magyar karakterek (UTF-8 helyesen)
NO_EVENTS_TEXT = "Sajnáljuk, de az Ön által megadott szűrési feltételek alapján nem találtunk egy eseményt sem."

HU_MONTHS = {
    "január": 1, "február": 2, "március": 3, "április": 4, "május": 5, "június": 6,
    "július": 7, "augusztus": 8, "szeptember": 9, "október": 10, "november": 11, "december": 12
}


def budapest_now():
    return datetime.now(tz=ZoneInfo("Europe/Budapest"))


def build_url(active_page: int) -> str:
    return (
        f"{BASE_URL}?activePage={active_page}"
        f"&osl=events&ot=tickets&searchPhrase="
    )


def page_is_empty(page) -> bool:
    """
    Üres oldal = megjelenik a kék info alert a NO_EVENTS_TEXT-tel.
    """
    try:
        # Várjuk meg, hogy a tartalom betöltődjön
        # Próbáljunk több selector-t is
        page.wait_for_selector(
            "sat-productions-and-events-list, div.alert.alert-info, div.alert, .event-card, .production-card",
            timeout=20000
        )
    except PlaywrightTimeoutError:
        print("  [DEBUG] Timeout - egyik selector sem jelent meg 20s alatt")
        return True

    # Ellenőrizzük az üres alert-et
    empty_alert = page.locator("div.alert.alert-info:has-text('Sajnáljuk')")
    if empty_alert.count() > 0:
        print(f"  [DEBUG] Üres alert találva")
        return True

    # Alternatív módon: keressen bármilyen alert-et az adott szöveggel
    page_text = page.inner_text("body").lower()
    if "nem találtunk egy eseményt sem" in page_text or "no events found" in page_text:
        print(f"  [DEBUG] 'Nincs esemény' szöveg a body-ban")
        return True

    # Keressünk esemény linkeket vagy kártyákat
    possible_events = page.locator("a[href*='/production/'], a[href*='/event/'], .event-card, .production-card")
    event_count = possible_events.count()
    print(f"  [DEBUG] Talált események/linkek száma: {event_count}")
    
    if event_count == 0:
        # Próbáljuk meg másképp is
        all_links = page.locator("sat-productions-and-events-list a").count()
        print(f"  [DEBUG] Összes link a komponensben: {all_links}")
        return all_links == 0

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
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    to_emails_raw = os.environ.get("TO_EMAILS")

    if not smtp_user or not smtp_pass or not to_emails_raw:
        print("[WARNING] Email credentials not set, printing email instead:")
        print(f"Subject: {subject}")
        print(f"Body:\n{body}")
        return

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
    print("[DEBUG] Ellenőrzöm az 1. oldalt...")
    page.goto(build_url(1), wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(2000)  # Növelve 2 másodpercre
    
    # Készítsünk screenshot-ot debughoz
    try:
        page.screenshot(path="debug_page1.png")
        print("[DEBUG] Screenshot mentve: debug_page1.png")
    except:
        pass
    
    if page_is_empty(page):
        print("[ERROR] Az 1. oldal üresnek tűnik!")
        # Debug info
        print("[DEBUG] Oldal URL:", page.url)
        print("[DEBUG] Oldal title:", page.title())
        body_text = page.inner_text("body")[:500]
        print(f"[DEBUG] Body első 500 karakter:\n{body_text}")
        return 0

    print("[DEBUG] 1. oldal OK, van tartalom")
    
    # Találjunk egy upper boundot, ami már üres
    lo = 1
    hi = None
    step = 1
    probe = 1

    while True:
        probe = lo + step
        if probe > max_pages:
            hi = max_pages + 1
            break

        print(f"[DEBUG] Próbálom oldal {probe}-t...")
        page.goto(build_url(probe), wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2000)

        if page_is_empty(page):
            print(f"[DEBUG] Oldal {probe} üres")
            hi = probe
            break

        print(f"[DEBUG] Oldal {probe} NEM üres")
        lo = probe
        step *= 2

    # Bináris keresés
    left = lo
    right = hi

    print(f"[DEBUG] Bináris keresés {left} és {right} között...")
    
    while left + 1 < right:
        mid = (left + right) // 2
        print(f"[DEBUG] Próbálom oldal {mid}-t...")
        page.goto(build_url(mid), wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2000)

        if page_is_empty(page):
            right = mid
        else:
            left = mid

    print(f"[DEBUG] Utolsó nem üres oldal: {left}")
    return left


def scrape_latest_date(page, last_page: int):
    """
    Végigmegy 1..last_page, és kinyeri a max dátumot.
    """
    all_dates = []

    for p in range(1, last_page + 1):
        print(f"[DEBUG] Scraping oldal {p}/{last_page}...")
        page.goto(build_url(p), wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(1500)

        if page_is_empty(page):
            print(f"  [WARNING] Oldal {p} váratlanul üres, átugrom")
            continue

        text = page.inner_text("body")
        dates = extract_dates_from_text(text)
        if dates:
            print(f"  [DEBUG] Találtam {len(dates)} dátumot: {dates[:3]}..." if len(dates) > 3 else f"  [DEBUG] Találtam {len(dates)} dátumot: {dates}")
        all_dates.extend(dates)

    if not all_dates:
        return None

    return max(all_dates)


def main():
    print(f"[INFO] Script indítása: {budapest_now()}")
    
    # Playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        last_page = find_last_nonempty_page(page, max_pages=60)
        if last_page == 0:
            subject = "Katona jegymester – hiba (1. oldal is üres)"
            body = (
                "A script szerint már az 1. oldal is üres.\n"
                "Lehet hálózati hiba, oldalváltozás vagy blokkolás.\n"
                f"Ellenőrzés ideje: {budapest_now()}\n"
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
            f"Ellenőrzés ideje: {budapest_now()}\n"
        )
        send_email(subject, body)
        return

    # State
    state = load_state("state.json")
    prev_str = state.get("latest_date")
    prev = datetime.strptime(prev_str, "%Y-%m-%d").date() if prev_str else None

    state["latest_date"] = latest.isoformat()
    state["last_page"] = last_page
    state["checked_at_budapest"] = budapest_now().isoformat()
    save_state(state, "state.json")

    # Email content
    if prev is None:
        subject = "Katona jegymester – első futás"
        body = (
            "Első futás (nincs korábbi összehasonlítás).\n\n"
            f"Legutolsó (max) dátum: {latest.isoformat()}\n"
            f"Utolsó nem üres oldal: {last_page}\n"
            f"Ellenőrzés ideje: {budapest_now()}\n"
        )
    elif latest > prev:
        subject = "Katona jegymester – ÚJ dátum került fel"
        body = (
            "Változás!\n\n"
            f"Korábbi max dátum: {prev.isoformat()}\n"
            f"Új max dátum:      {latest.isoformat()}\n"
            f"Utolsó nem üres oldal: {last_page}\n"
            f"Ellenőrzés ideje: {budapest_now()}\n"
        )
    elif latest < prev:
        subject = "Katona jegymester – FIGYELEM: a max dátum csökkent"
        body = (
            "Furcsa változás (törlés / szűrés / oldalváltozás lehet).\n\n"
            f"Korábbi max dátum: {prev.isoformat()}\n"
            f"Mostani max dátum: {latest.isoformat()}\n"
            f"Utolsó nem üres oldal: {last_page}\n"
            f"Ellenőrzés ideje: {budapest_now()}\n"
        )
    else:
        subject = "Katona jegymester – nincs változás"
        body = (
            "Nincs változás.\n\n"
            f"Max dátum továbbra is: {latest.isoformat()}\n"
            f"Utolsó nem üres oldal: {last_page}\n"
            f"Ellenőrzés ideje: {budapest_now()}\n"
        )

    send_email(subject, body)
    print(f"[INFO] Email sent: {subject}")


if __name__ == "__main__":
    main()
