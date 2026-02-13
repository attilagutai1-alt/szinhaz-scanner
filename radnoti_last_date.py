"""
Radnóti Színház – scraper modul.

A radnotiszinhaz.hu/musor/ oldalról havi bontásban (?offset=0,1,2,...)
scrape-eli a dátumokat és az előadásneveket.
"""

import os
import re
import json
from datetime import datetime, date
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from scraper_utils import compare_events


BASE_URL = "https://radnotiszinhaz.hu/musor/"
STATE_FILE = "radnoti_state.json"

WEEKDAYS = r"(?:hétfő|kedd|szerda|csütörtök|péntek|szombat|vasárnap)"


def budapest_now():
    return datetime.now(tz=ZoneInfo("Europe/Budapest"))


def extract_month_info(text: str) -> tuple[int, int] | None:
    m = re.search(r"(20\d{2})\.(0[1-9]|1[0-2])\.\d{2}\.\s*[—–-]\s*(20\d{2})\.(0[1-9]|1[0-2])\.\d{2}\.", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def extract_events_for_month(text: str, year: int, month: int) -> list[tuple[date, str]]:
    """
    A Radnóti havi nézetéből kinyeri a (dátum, előadásnév) párokat.
    Szöveg mintája:
        13.
        csütörtök
        19:00
        Előadás neve
    Egy napon belül több előadás is lehet (pl. 11:00 és 19:00).
    """
    events = []

    # Keresünk napszám + napnév + idő + cím mintákat
    # Megkeressük az összes "NAP.\nNAPNÉV" pozíciót
    day_positions = list(re.finditer(
        r"\b(\d{1,2})\.\s*\n\s*" + WEEKDAYS,
        text, re.IGNORECASE
    ))

    for i, day_match in enumerate(day_positions):
        d = int(day_match.group(1))
        if not (1 <= d <= 31):
            continue

        try:
            event_date = date(year, month, d)
        except ValueError:
            continue

        # A nap blokk vége: a következő napszám pozíciója, vagy a szöveg vége
        block_end = day_positions[i + 1].start() if i + 1 < len(day_positions) else len(text)
        block = text[day_match.start():block_end]

        # Keresünk "HH:MM\nElőadás neve" mintákat a blokkban
        time_matches = list(re.finditer(
            r'(\d{1,2}:\d{2})\s*\n\s*([^\n]+)',
            block
        ))

        if time_matches:
            for tm in time_matches:
                title = tm.group(2).strip()
                # Szűrjük ki a nem-cím sorokat (pl. "Jegy", "Információ", stb.)
                if title and len(title) > 2 and not re.match(r'^[\d.:]+$', title):
                    events.append((event_date, title))
        else:
            # Ha nincs idő minta, próbáljuk az első nem-üres sort a napnév után
            lines = block.split("\n")
            for line in lines[2:]:  # átugorjuk a napszám + napnév sort
                line = line.strip()
                if line and len(line) > 2 and not re.match(r'^[\d.:]+$', line) and not re.match(WEEKDAYS, line, re.IGNORECASE):
                    events.append((event_date, line))
                    break

    return events


def extract_dates_from_range(text: str) -> list[date]:
    """Fallback: teljes dátumok keresése."""
    dates = []
    for m in re.finditer(r"\b(20\d{2})\.(0[1-9]|1[0-2])\.(0[1-9]|[12]\d|3[01])\.", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dates.append(date(y, mo, d))
        except ValueError:
            pass
    return sorted(set(dates))


def scrape_all_months(page, max_months_ahead: int = 12) -> list[tuple[date, str]]:
    all_events = []
    empty_streak = 0

    for offset in range(max_months_ahead):
        url = f"{BASE_URL}?offset={offset}"
        print(f"[RADNÓTI] Betöltés: offset={offset}")

        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
        except PlaywrightTimeoutError:
            print(f"[RADNÓTI] Timeout offset={offset}")
            empty_streak += 1
            if empty_streak >= 2:
                break
            continue

        text = page.inner_text("body")

        if offset == 0:
            try:
                page.screenshot(path="debug_radnoti_page.png")
            except Exception:
                pass

        month_info = extract_month_info(text)
        if month_info:
            year, month = month_info
            print(f"[RADNÓTI] Hónap: {year}.{month:02d}")

            month_events = extract_events_for_month(text, year, month)

            if month_events:
                month_dates = [d for d, _ in month_events]
                print(f"[RADNÓTI] {len(month_events)} előadás, {min(month_dates)} - {max(month_dates)}")
                all_events.extend(month_events)
                empty_streak = 0
            else:
                # Fallback: csak dátumok
                fallback_dates = extract_dates_from_range(text)
                if fallback_dates:
                    for d in fallback_dates:
                        all_events.append((d, "?"))
                    empty_streak = 0
                else:
                    print(f"[RADNÓTI] Nincs esemény")
                    empty_streak += 1
        else:
            fallback_dates = extract_dates_from_range(text)
            if fallback_dates:
                for d in fallback_dates:
                    all_events.append((d, "?"))
                empty_streak = 0
            else:
                empty_streak += 1

        if empty_streak >= 2:
            print(f"[RADNÓTI] 2 üres hónap egymás után, befejezem")
            break

    return all_events


def check() -> dict:
    name = "Radnóti Színház"
    print(f"\n{'='*50}")
    print(f"[RADNÓTI] Scraper indítása: {budapest_now()}")

    result = {"name": name, "latest": None, "prev": None, "status": "error", "detail": ""}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = context.new_page()
            all_events = scrape_all_months(page)
            browser.close()

        if not all_events:
            result["detail"] = "Nem találtam előadást az oldalon."
            return result

        unique_events = sorted(set((d.isoformat(), t) for d, t in all_events))
        latest = max(d for d, _ in all_events)
        event_count = len(unique_events)
        print(f"[RADNÓTI] {event_count} előadás, max: {latest}")

        state = {}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)

        prev_str = state.get("latest_date")
        prev = datetime.strptime(prev_str, "%Y-%m-%d").date() if prev_str else None
        prev_count = state.get("event_count")
        prev_events = state.get("events", [])

        state["latest_date"] = latest.isoformat()
        state["event_count"] = event_count
        state["events"] = [list(e) for e in unique_events]
        state["checked_at_budapest"] = budapest_now().isoformat()
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

        result["latest"] = latest
        result["prev"] = prev
        result["status"], result["detail"] = compare_events(
            latest, event_count, prev, prev_count,
            [list(e) for e in unique_events], prev_events
        )

        print(f"[RADNÓTI] {result['detail']}")
        return result

    except Exception as e:
        result["detail"] = f"Hiba: {e}"
        print(f"[RADNÓTI] {result['detail']}")
        return result


if __name__ == "__main__":
    r = check()
    print(f"\nEredmény: {r}")
