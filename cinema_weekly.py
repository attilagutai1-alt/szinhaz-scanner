"""
Heti mozi összefoglaló – Művész, Puskin, Toldi, Corvin.

Vasárnap futtatva összegyűjti a következő hét (hétfő–vasárnap) vetítéseit.
A mozis hét csütörtök–szerda, ezért 2 mozis hétből kell összeollózni:
  - Aktuális mozis hét → hétfő, kedd, szerda
  - Következő mozis hét → csütörtök, péntek, szombat, vasárnap

Minden mozit a saját oldaláról scrape-elünk, mert az artmozi.hu-n
a vetítési időknél nem látszik melyik mozi melyik időpont.

React szelektorok (azonos mind a 4 oldalon):
  Hétváltó:   div.react-week-filter-number  ("07", "08" stb.)
  Napváltó:   div.react-day-filter-box  (nem disabled)
              .react-day-filter-date   ("feb. 16")
  Film tile:  div.react-film-tile-container
              span.react-film-tile-title-item  (filmcím)
              a.react-film-tile-title[href]    (film link)
  Vetítés:    .react-purchase-container:not(.disabled)
              button.react-purchase-content    ("17:45")
"""

import os
import re
import ssl
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright


CINEMAS = [
    {"name": "Művész", "url": "https://muveszmozi.hu/"},
    {"name": "Puskin", "url": "https://puskinmozi.hu/"},
    {"name": "Toldi",  "url": "https://toldimozi.hu/"},
    {"name": "Corvin", "url": "https://corvinmozi.hu/"},
]

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
    """'feb. 16' -> date(2026, 2, 16)"""
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
    H-Sze: az előző csütörtökhöz tartozó ISO hét
    Cs-V: a hét csütörtökjéhez tartozó ISO hét
    """
    prev_thursday = monday - timedelta(days=4)
    this_thursday = monday + timedelta(days=3)
    return prev_thursday.isocalendar()[1], this_thursday.isocalendar()[1]


def extract_screenings_for_day(page, target_date: date, cinema_name: str) -> list[dict]:
    """
    Az aktuálisan megjelenített nap vetítéseit nyeri ki JS-sel.
    Egyedi mozi oldalon vagyunk → minden vetítés ehhez a mozihoz tartozik.
    """
    data = page.evaluate("""() => {
        const results = [];
        const tiles = document.querySelectorAll('.react-film-tile-container');
        
        tiles.forEach(tile => {
            const titleEl = tile.querySelector('.react-film-tile-title-item');
            if (!titleEl) return;
            const filmTitle = titleEl.textContent.trim();
            
            const linkEl = tile.querySelector('a.react-film-tile-title');
            const filmUrl = linkEl ? linkEl.getAttribute('href') : '';
            
            // Csak a NEM disabled vetítések
            const containers = tile.querySelectorAll('.react-purchase-container:not(.disabled)');
            containers.forEach(container => {
                const btn = container.querySelector('button.react-purchase-content');
                if (!btn) return;
                const time = btn.textContent.trim();
                if (/^\\d{1,2}:\\d{2}$/.test(time)) {
                    results.push({
                        film: filmTitle,
                        time: time,
                        url: filmUrl || '',
                    });
                }
            });
        });
        
        return results;
    }""")

    screenings = []
    for item in data:
        screenings.append({
            "film": item["film"],
            "time": item["time"],
            "url": item.get("url", ""),
            "cinema": cinema_name,
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
                print(f"    Hét {week_str} kiválasztva ✓")
                return True
        print(f"    Hét {week_str} nem található")
        return False
    except Exception as e:
        print(f"    Hétváltó hiba: {e}")
        return False


def click_day_and_scrape(page, target_date: date, cinema_name: str) -> list[dict]:
    """Rákattint a megfelelő napra és kinyeri a vetítéseket."""
    year = target_date.year
    day_name = HU_DAYS_SHORT[target_date.weekday()]

    day_boxes = page.locator("div.react-day-filter-box:not(.disabled)").all()
    for box in day_boxes:
        try:
            date_el = box.locator(".react-day-filter-date")
            date_text = date_el.inner_text(timeout=2000).strip()
            parsed = parse_day_filter_date(date_text, year)
            if parsed == target_date:
                box.click()
                page.wait_for_timeout(2000)
                screenings = extract_screenings_for_day(page, target_date, cinema_name)
                print(f"      {date_text} ({day_name}): {len(screenings)} vetítés")
                return screenings
        except Exception:
            continue

    print(f"      {HU_MONTHS[target_date.month]}. {target_date.day} ({day_name}): nem elérhető")
    return []


def scrape_all() -> tuple[list[dict], date, date]:
    """Mind a 4 mozi következő hetének programját összegyűjti."""
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

        for cinema in CINEMAS:
            name = cinema["name"]
            url = cinema["url"]

            print(f"\n{'='*40}")
            print(f"[{name}] {url}")

            try:
                page.goto(url, wait_until="networkidle", timeout=90000)
                page.wait_for_timeout(5000)

                # Scroll a schedule block-hoz
                try:
                    page.evaluate("document.querySelector('#block-artmozi-homepage-react-block')?.scrollIntoView()")
                    page.wait_for_timeout(2000)
                except Exception:
                    pass

                # --- Mozis hét 1: H, K, Sze ---
                print(f"  Mozis hét {week1:02d} (H–Sze)")
                click_week(page, week1)
                for day_offset in range(3):
                    target = monday + timedelta(days=day_offset)
                    screenings = click_day_and_scrape(page, target, name)
                    all_screenings.extend(screenings)

                # --- Mozis hét 2: Cs, P, Szo, V ---
                print(f"  Mozis hét {week2:02d} (Cs–V)")
                click_week(page, week2)
                for day_offset in range(3, 7):
                    target = monday + timedelta(days=day_offset)
                    screenings = click_day_and_scrape(page, target, name)
                    all_screenings.extend(screenings)

            except Exception as e:
                print(f"  [{name}] HIBA: {e}")

        browser.close()

    print(f"\nÖsszesen {len(all_screenings)} vetítés")
    return all_screenings, monday, sunday


def format_email(all_screenings: list, monday: date, sunday: date) -> tuple[str, str]:
    """Film-centrikus email: film -> mozik -> napok+időpontok."""
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
        for c in CINEMAS:
            lines.append(f"  {c['name']}: {c['url']}")
        return subject, "\n".join(lines)

    # Csoportosítás: film -> {url, cinemas: {cinema -> [(nap, idő)]}}
    films: dict[str, dict] = {}
    for s in all_screenings:
        film = s["film"]
        if film not in films:
            films[film] = {"url": s.get("url", ""), "cinemas": {}}
        cinema = s["cinema"]
        if cinema not in films[film]["cinemas"]:
            films[film]["cinemas"][cinema] = []
        day_short = HU_DAYS_SHORT[s["date"].weekday()]
        films[film]["cinemas"][cinema].append(f"{day_short} {s['time']}")

    for film in sorted(films.keys(), key=str.lower):
        info = films[film]
        
        # Film URL
        film_url = info["url"]
        if film_url and not film_url.startswith("http"):
            film_url = f"https://artmozi.hu{film_url}"

        lines.append(f"🎬 {film}")
        if film_url:
            lines.append(f"   {film_url}")

        for cinema_name in ["Művész", "Puskin", "Toldi", "Corvin"]:
            if cinema_name in info["cinemas"]:
                times = info["cinemas"][cinema_name]
                lines.append(f"   {cinema_name}: {' | '.join(times)}")

        lines.append("")

    lines.append("-" * 55)
    lines.append("Jó mozizást! 🍿")

    return subject, "\n".join(lines)


def send_email(subject: str, body: str):
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    to_emails_raw = os.environ.get("TO_EMAILS")

    if not smtp_user or not smtp_pass or not to_emails_raw:
        print(f"\n[EMAIL] Nincs SMTP, tartalom:")
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
