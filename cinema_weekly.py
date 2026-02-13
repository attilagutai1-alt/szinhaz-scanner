"""
Heti mozi összefoglaló – Művész, Puskin, Toldi, Corvin.

Vasárnap futtatva összegyűjti a következő hét (hétfő–vasárnap) vetítéseit.
A mozis hét csütörtök–szerda, ezért 2 mozis hétből kell összeollózni:
  - Aktuális mozis hét → hétfő, kedd, szerda
  - Következő mozis hét → csütörtök, péntek, szombat, vasárnap

Mind a 4 mozi (Művész, Puskin, Toldi, Corvin) ugyanazt az artmozi.hu platformot
használja (Drupal + React schedule block), azonos HTML struktúrával.

React szelektorok:
  Hétváltó:  div.react-week-filter-number  (szöveg: "07", "08" stb.)
  Napváltó:  div.react-day-filter-box  (nem disabled)
             .react-day-filter-title  (napnév / "Ma")
             .react-day-filter-date   ("feb. 16")
  Film cím:  span.react-film-tile-title-item
  Vetítés:   button.react-purchase-content  (szöveg: "17:45")
             class-ban benne: react-cinema-MOZISLUG (pl. react-cinema-toldi-mozi)
"""

import os
import re
import ssl
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright


# Egy oldalt használunk: artmozi.hu mutatja mind a 4 mozit egyben
ARTMOZI_URL = "https://artmozi.hu/"

# A 4 mozi CSS slug-ja a react-cinema-* classban
CINEMAS = {
    "muvesz-mozi":  "Művész",
    "puskin-mozi":  "Puskin",
    "toldi-mozi":   "Toldi",
    "corvin-mozi":  "Corvin",
}

HU_DAYS_SHORT = {0: "H", 1: "K", 2: "Sze", 3: "Cs", 4: "P", 5: "Szo", 6: "V"}
HU_MONTHS = {
    1: "jan", 2: "feb", 3: "már", 4: "ápr", 5: "máj", 6: "jún",
    7: "júl", 8: "aug", 9: "sze", 10: "okt", 11: "nov", 12: "dec",
}
HU_MONTH_PARSE = {
    "jan": 1, "feb": 2, "már": 3, "márc": 3, "ápr": 4, "máj": 5, "jún": 6,
    "júl": 7, "aug": 8, "sze": 9, "szep": 9, "okt": 10, "nov": 11, "dec": 12,
}


def budapest_now():
    return datetime.now(tz=ZoneInfo("Europe/Budapest"))


def get_target_week() -> tuple[date, date]:
    """Következő hét hétfő–vasárnap."""
    today = budapest_now().date()
    days_ahead = (7 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    monday = today + timedelta(days=days_ahead)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def parse_day_filter_date(date_text: str, year: int) -> date | None:
    """
    "feb. 16" -> date(2026, 2, 16)
    """
    m = re.match(r'([a-záéíóöőúüű]+)\.?\s+(\d{1,2})', date_text.strip().lower())
    if not m:
        return None
    month_str = m.group(1)
    day = int(m.group(2))
    month = HU_MONTH_PARSE.get(month_str)
    if not month:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def get_week_numbers_for_target(monday: date) -> tuple[int, int]:
    """
    Mozis hét = csütörtök-szerda.
    Hétfő-Szerda: az aktuális mozis hét száma
    Csütörtök-Vasárnap: a következő mozis hét száma

    A hétszám az ISO hét, ami a react-week-filter-number-ben jelenik meg.
    """
    # H-Sze napok az előző csütörtökhöz tartozó ISO héten vannak
    prev_thursday = monday - timedelta(days=4)
    # Cs-V napok a hét csütörtökjéhez tartoznak
    this_thursday = monday + timedelta(days=3)

    return prev_thursday.isocalendar()[1], this_thursday.isocalendar()[1]


def extract_screenings_for_day(page, target_date: date) -> list[dict]:
    """
    Az aktuálisan megjelenített nap vetítéseit nyeri ki.
    Visszaad: [{"film": str, "time": str, "cinema_slug": str, "date": date}, ...]
    """
    screenings = []

    html = page.content()

    # DEBUG: HTML méret és kulcs-szelektorok keresése
    print(f"      [DEBUG] HTML méret: {len(html)} karakter")
    
    title_count = len(re.findall(r'react-film-tile-title-item', html))
    btn_count = len(re.findall(r'react-purchase-content', html))
    cinema_count = len(re.findall(r'react-cinema-', html))
    print(f"      [DEBUG] title-item: {title_count}, purchase-content: {btn_count}, react-cinema-: {cinema_count}")
    
    # Ha nincs találat, keressünk más mintákat
    if title_count == 0:
        # Keressünk bármilyen "film" vagy "title" classot
        film_classes = re.findall(r'class="[^"]*(?:film|title|movie)[^"]*"', html, re.IGNORECASE)
        print(f"      [DEBUG] Film/title/movie classes: {film_classes[:10]}")
    
    if btn_count == 0:
        # Keressünk bármilyen időpontot (HH:MM)
        times_in_html = re.findall(r'>(\d{1,2}:\d{2})<', html)
        print(f"      [DEBUG] Időpontok a HTML-ben: {times_in_html[:10]}")
    
    # Mentsünk el egy HTML mintát az első napnál
    if target_date.weekday() == 0:  # hétfő
        sample_file = f"debug_cinema_html_sample.txt"
        # A react block környékét mentjük
        react_idx = html.find('block-artmozi-homepage-react-block')
        if react_idx >= 0:
            sample = html[react_idx:react_idx+5000]
        else:
            # Az oldal közepéből mentünk egy darabot
            mid = len(html) // 2
            sample = html[mid:mid+5000]
        with open(sample_file, "w", encoding="utf-8") as f:
            f.write(sample)
        print(f"      [DEBUG] HTML minta mentve: {sample_file}")

    # Film címek pozíciói
    title_pattern = re.finditer(
        r'<span[^>]*class="react-film-tile-title-item"[^>]*>([^<]+)</span>',
        html
    )
    titles_with_pos = [(m.start(), m.group(1).strip()) for m in title_pattern]

    # Vetítés gombok pozíciói
    button_pattern = re.finditer(
        r'<button[^>]*class="react-purchase-content[^"]*react-cinema-([a-z-]+)"[^>]*>(\d{1,2}:\d{2})</button>',
        html
    )
    buttons_with_pos = [(m.start(), m.group(1), m.group(2)) for m in button_pattern]

    # Minden gombot a legközelebbi (előtte lévő) filmcímhez rendelünk
    for btn_pos, cinema_slug, time_str in buttons_with_pos:
        film_title = "?"
        for title_pos, title in reversed(titles_with_pos):
            if title_pos < btn_pos:
                film_title = title
                break

        if cinema_slug in CINEMAS:
            screenings.append({
                "film": film_title,
                "time": time_str,
                "cinema_slug": cinema_slug,
                "cinema": CINEMAS[cinema_slug],
                "date": target_date,
            })

    return screenings


def click_week(page, week_num: int) -> bool:
    """Rákattint a megfelelő hétszámra."""
    week_str = f"{week_num:02d}"
    try:
        week_buttons = page.locator("div.react-week-filter-number").all()
        for btn in week_buttons:
            if btn.inner_text(timeout=2000).strip() == week_str:
                btn.click()
                page.wait_for_timeout(3000)
                print(f"  Hét {week_str} kiválasztva ✓")
                return True
        print(f"  Hét {week_str} gomb nem található az oldalon")
        return False
    except Exception as e:
        print(f"  Hétváltó hiba: {e}")
        return False


def click_day_and_scrape(page, target_date: date) -> list[dict]:
    """
    Rákattint a megfelelő napra a napváltóban és kinyeri a vetítéseket.
    """
    target_str = f"{HU_MONTHS[target_date.month]}. {target_date.day}"
    year = target_date.year

    day_boxes = page.locator("div.react-day-filter-box:not(.disabled)").all()
    for box in day_boxes:
        try:
            date_el = box.locator(".react-day-filter-date")
            date_text = date_el.inner_text(timeout=2000).strip()

            parsed = parse_day_filter_date(date_text, year)
            if parsed == target_date:
                box.click()
                page.wait_for_timeout(2000)
                screenings = extract_screenings_for_day(page, target_date)
                day_name = HU_DAYS_SHORT[target_date.weekday()]
                print(f"    {date_text} ({day_name}): {len(screenings)} vetítés")
                return screenings
        except Exception:
            continue

    print(f"    {target_str} nap nem található / nem kattintható")
    return []


def scrape_all() -> tuple[list[dict], date, date]:
    """
    Mind a 4 mozi következő hetének programját összegyűjti.
    Az artmozi.hu-t használjuk – egy oldalon mind a 4 mozi vetítése látszik.
    """
    monday, sunday = get_target_week()
    week1, week2 = get_week_numbers_for_target(monday)

    print(f"Célhét: {monday} (hétfő) – {sunday} (vasárnap)")
    print(f"Mozis hetek: {week1:02d} (H-Sze) és {week2:02d} (Cs-V)")

    all_screenings = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = context.new_page()
        page.set_default_timeout(60000)

        print(f"\nOldal betöltése: {ARTMOZI_URL}")
        page.goto(ARTMOZI_URL, wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(5000)

        # Scrolloljunk a schedule blockhoz
        try:
            page.evaluate("document.querySelector('#block-artmozi-homepage-react-block')?.scrollIntoView()")
            page.wait_for_timeout(2000)
        except Exception:
            pass

        # --- 1. mozis hét: H, K, Sze ---
        print(f"\n--- Mozis hét {week1:02d} (hétfő–szerda) ---")
        click_week(page, week1)

        for day_offset in range(3):  # H=0, K=1, Sze=2
            target = monday + timedelta(days=day_offset)
            screenings = click_day_and_scrape(page, target)
            all_screenings.extend(screenings)

        # --- 2. mozis hét: Cs, P, Szo, V ---
        print(f"\n--- Mozis hét {week2:02d} (csütörtök–vasárnap) ---")
        click_week(page, week2)

        for day_offset in range(3, 7):  # Cs=3, P=4, Szo=5, V=6
            target = monday + timedelta(days=day_offset)
            screenings = click_day_and_scrape(page, target)
            all_screenings.extend(screenings)

        browser.close()

    print(f"\nÖsszesen {len(all_screenings)} vetítés gyűjtve")
    return all_screenings, monday, sunday


def format_email(all_screenings: list, monday: date, sunday: date) -> tuple[str, str]:
    """Formázza az emailt film-centrikusan, mozikat alatta felsorolva."""
    mon_str = f"{HU_MONTHS[monday.month]}. {monday.day}."
    sun_str = f"{HU_MONTHS[sunday.month]}. {sunday.day}."

    subject = f"🎬 Mozihét: {mon_str} – {sun_str}"

    lines = [
        f"Mozihét: {monday.strftime('%Y.%m.%d.')} (hétfő) – {sunday.strftime('%Y.%m.%d.')} (vasárnap)",
        "=" * 55,
        "",
    ]

    if not all_screenings:
        lines.append("Nem sikerült vetítéseket találni ezen a héten.")
        lines.append("")
        lines.append("Ellenőrizd manuálisan:")
        lines.append(f"  https://artmozi.hu/")
        return subject, "\n".join(lines)

    # Csoportosítás: film -> cinema -> [(nap_short, idő), ...]
    films: dict[str, dict[str, list[str]]] = {}
    for s in all_screenings:
        film = s["film"]
        cinema = s["cinema"]
        day_short = HU_DAYS_SHORT[s["date"].weekday()]
        time_str = s["time"]

        if film not in films:
            films[film] = {}
        if cinema not in films[film]:
            films[film][cinema] = []
        films[film][cinema].append(f"{day_short} {time_str}")

    # Rendezés filmcím szerint
    for film in sorted(films.keys(), key=str.lower):
        # Film slug a linkhez (ékezetek eltávolítása)
        slug = film.lower()
        for hun, asc in [("á","a"),("é","e"),("í","i"),("ó","o"),("ö","o"),("ő","o"),("ú","u"),("ü","u"),("ű","u")]:
            slug = slug.replace(hun, asc)
        slug = re.sub(r'[^\w\s-]', '', slug)
        slug = re.sub(r'[\s]+', '-', slug).strip('-')

        lines.append(f"🎬 {film}")
        lines.append(f"   https://artmozi.hu/filmek/{slug}")

        for cinema in ["Művész", "Puskin", "Toldi", "Corvin"]:
            if cinema in films[film]:
                times = films[film][cinema]
                # Csoportosítás napok szerint
                lines.append(f"   {cinema}: {' | '.join(times)}")

        lines.append("")

    lines.append("-" * 55)
    lines.append("Jó mozizást! 🍿")

    return subject, "\n".join(lines)


def send_email(subject: str, body: str):
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    to_emails_raw = os.environ.get("TO_EMAILS")

    if not smtp_user or not smtp_pass or not to_emails_raw:
        print(f"\n[EMAIL] Nincs SMTP beállítva, email tartalom:")
        print(f"  Tárgy: {subject}")
        print(f"\n{body}")
        return

    to_emails = [e.strip() for e in re.split(r"[;,]", to_emails_raw) if e.strip()]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(to_emails)
    msg.set_content(body)

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)

    print(f"\n[EMAIL] Elküldve: {subject}")


def main():
    now = budapest_now()
    print(f"{'#'*60}")
    print(f"  HETI MOZI ÖSSZEFOGLALÓ – {now.strftime('%Y.%m.%d. %H:%M')}")
    print(f"{'#'*60}")

    all_screenings, monday, sunday = scrape_all()
    subject, body = format_email(all_screenings, monday, sunday)
    send_email(subject, body)


if __name__ == "__main__":
    main()
