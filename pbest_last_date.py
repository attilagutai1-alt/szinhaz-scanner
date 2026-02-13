"""
Pintér Béla és Társulata – scraper modul.

A https://pbest.hu/musor oldalról scrape-eli az összes előadás dátumát és nevét.
Az oldal szerver-renderelt, minden előadás egy oldalon van.
A dátumok az event_rdate URL paraméterből, a címek a linkek szövegéből nyerhetők ki.
"""

import os
import re
import json
from datetime import datetime, date
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from scraper_utils import compare_events


URL = "https://pbest.hu/musor"
STATE_FILE = "pbest_state.json"

HU_SHORT_MONTHS = {
    "jan": 1, "feb": 2, "már": 3, "ápr": 4, "máj": 5, "jún": 6,
    "júl": 7, "aug": 8, "sze": 9, "okt": 10, "nov": 11, "dec": 12,
}


def budapest_now():
    return datetime.now(tz=ZoneInfo("Europe/Budapest"))


def extract_events_from_html(html: str) -> list[tuple[date, str]]:
    """
    (dátum, előadásnév) párok kinyerése a HTML-ből.
    PBEST link formátum: <a href="/musor/SHOW-NAME?event_rdate=YYYYMMDDHHMMSS">Title</a>
    """
    events = []

    # Keresünk linkeket event_rdate paraméterrel és a link szövegével
    for m in re.finditer(
        r'<a[^>]*href="[^"]*?/musor/([^"?]+)\?[^"]*event_rdate=(20\d{2})(\d{2})(\d{2})\d{6}[^"]*"[^>]*>([^<]+)</a>',
        html, re.IGNORECASE
    ):
        slug = m.group(1)
        y, mo, d = int(m.group(2)), int(m.group(3)), int(m.group(4))
        link_text = m.group(5).strip()
        title = link_text if link_text else slug.replace("-", " ").title()
        try:
            events.append((date(y, mo, d), title))
        except ValueError:
            pass

    # Fallback: event_rdate nélkül is keresünk
    if not events:
        for m in re.finditer(r"event_rdate=(20\d{2})(\d{2})(\d{2})\d{6}", html):
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                events.append((date(y, mo, d), "?"))
            except ValueError:
                pass

    return events


def check() -> dict:
    name = "Pintér Béla és Társulata"
    print(f"\n{'='*50}")
    print(f"[PBEST] Scraper indítása: {budapest_now()}")

    result = {"name": name, "latest": None, "prev": None, "status": "error", "detail": ""}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = context.new_page()

            print(f"[PBEST] Oldal betöltése: {URL}")
            page.goto(URL, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)

            try:
                page.screenshot(path="debug_pbest_page.png")
            except Exception:
                pass

            html_content = page.content()
            browser.close()

        all_events = extract_events_from_html(html_content)

        if not all_events:
            result["detail"] = "Nem találtam előadást az oldalon."
            return result

        unique_events = sorted(set((d.isoformat(), t) for d, t in all_events))
        latest = max(d for d, _ in all_events)
        event_count = len(unique_events)
        print(f"[PBEST] {event_count} előadás, max: {latest}")

        # State
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

        print(f"[PBEST] {result['detail']}")
        return result

    except Exception as e:
        result["detail"] = f"Hiba: {e}"
        print(f"[PBEST] {result['detail']}")
        return result


if __name__ == "__main__":
    r = check()
    print(f"\nEredmény: {r}")
