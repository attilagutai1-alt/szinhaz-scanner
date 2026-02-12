"""
Radnóti Színház – scraper modul.

A radnotiszinhaz.hu/musor/ oldalról havi bontásban (?offset=0,1,2,...)
scrape-eli a dátumokat, amíg üres hónapot nem talál.
"""

import os
import re
import json
from datetime import datetime, date
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


BASE_URL = "https://radnotiszinhaz.hu/musor/"
STATE_FILE = "radnoti_state.json"


def budapest_now():
    return datetime.now(tz=ZoneInfo("Europe/Budapest"))


def extract_month_info(text: str) -> tuple[int, int] | None:
    m = re.search(r"(20\d{2})\.(0[1-9]|1[0-2])\.\d{2}\.\s*[—–-]\s*(20\d{2})\.(0[1-9]|1[0-2])\.\d{2}\.", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def extract_dates_for_month(text: str, year: int, month: int) -> list[date]:
    dates = []
    day_pattern = re.finditer(
        r"\b(\d{1,2})\.\s*\n\s*(?:hétfő|kedd|szerda|csütörtök|péntek|szombat|vasárnap)",
        text, re.IGNORECASE
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
    dates = []
    for m in re.finditer(r"\b(20\d{2})\.(0[1-9]|1[0-2])\.(0[1-9]|[12]\d|3[01])\.", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dates.append(date(y, mo, d))
        except ValueError:
            pass
    return sorted(set(dates))


def scrape_all_months(page, max_months_ahead: int = 12) -> list[date]:
    all_dates = []
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

            month_dates = extract_dates_for_month(text, year, month)
            if not month_dates:
                month_dates = extract_dates_from_range(text)

            if month_dates:
                print(f"[RADNÓTI] {len(month_dates)} dátum: {min(month_dates)} - {max(month_dates)}")
                all_dates.extend(month_dates)
                empty_streak = 0
            else:
                print(f"[RADNÓTI] Nincs esemény")
                empty_streak += 1
        else:
            fallback_dates = extract_dates_from_range(text)
            if fallback_dates:
                all_dates.extend(fallback_dates)
                empty_streak = 0
            else:
                empty_streak += 1

        if empty_streak >= 2:
            print(f"[RADNÓTI] 2 üres hónap egymás után, befejezem")
            break

    return sorted(set(all_dates))


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
            all_dates = scrape_all_months(page)
            browser.close()

        if not all_dates:
            result["detail"] = "Nem találtam dátumot az oldalon."
            return result

        latest = max(all_dates)
        event_count = len(all_dates)
        print(f"[RADNÓTI] {event_count} egyedi dátum, max: {latest}")

        state = {}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)

        prev_str = state.get("latest_date")
        prev = datetime.strptime(prev_str, "%Y-%m-%d").date() if prev_str else None

        state["latest_date"] = latest.isoformat()
        state["event_count"] = event_count
        state["checked_at_budapest"] = budapest_now().isoformat()
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

        result["latest"] = latest
        result["prev"] = prev

        if prev is None:
            result["status"] = "first_run"
            result["detail"] = f"Első futás. Max dátum: {latest} ({event_count} dátum)"
        elif latest > prev:
            result["status"] = "new_date"
            result["detail"] = f"ÚJ! {prev} → {latest} ({event_count} dátum)"
        elif latest < prev:
            result["status"] = "decreased"
            result["detail"] = f"Csökkent! {prev} → {latest} ({event_count} dátum)"
        else:
            result["status"] = "no_change"
            result["detail"] = f"Nincs változás. Max: {latest} ({event_count} dátum)"

        print(f"[RADNÓTI] {result['detail']}")
        return result

    except Exception as e:
        result["detail"] = f"Hiba: {e}"
        print(f"[RADNÓTI] {result['detail']}")
        return result


if __name__ == "__main__":
    r = check()
    print(f"\nEredmény: {r}")
