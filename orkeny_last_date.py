"""
Örkény István Színház – scraper modul.

Az orkenyszinhaz.hu/jegyvasarlas/kereses/eloadas oldalról
a "Továbbiak betöltése" gombot kattintgatva betölti az összes
előadást, majd kinyeri a dátumot és a címet.
"""

import os
import re
import json
from datetime import datetime, date
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from scraper_utils import compare_events


URL = "https://orkenyszinhaz.hu/jegyvasarlas/kereses/eloadas"
STATE_FILE = "orkeny_state.json"


def budapest_now():
    return datetime.now(tz=ZoneInfo("Europe/Budapest"))


def extract_dates_from_text(text: str) -> list[date]:
    dates = []
    for m in re.finditer(r"\b(20\d{2})\.(0[1-9]|1[0-2])\.(0[1-9]|[12]\d|3[01])\.", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dates.append(date(y, mo, d))
        except ValueError:
            pass
    for m in re.finditer(r"\b(20\d{2})-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dates.append(date(y, mo, d))
        except ValueError:
            pass
    return sorted(set(dates))


def extract_events_from_page(page) -> list[tuple[date, str]]:
    """
    Megpróbálja az egyes előadás-bejegyzéseket külön-külön kinyerni,
    hogy a címet is megkapjuk a dátum mellett.
    """
    events = []

    # Stratégia 1: Playwright locatorokkal keresünk event elemeket
    for selector in [
        "article", ".event-item", ".search-result-item",
        ".performance-item", "[class*='event']", "[class*='result']",
        ".card", "li"
    ]:
        try:
            items = page.locator(selector).all()
            if len(items) < 2:
                continue

            found_any = False
            for item in items:
                try:
                    text = item.inner_text(timeout=2000)
                    dates = extract_dates_from_text(text)
                    if not dates:
                        continue

                    # Cím kinyerése: először heading/link, aztán első sor
                    title = "?"
                    for title_sel in ["h2", "h3", "h4", "a[href*='eloadas']", "a[href*='program']", ".title", "[class*='title']"]:
                        try:
                            title_el = item.locator(title_sel).first
                            t = title_el.inner_text(timeout=500).strip()
                            if t and len(t) > 2 and not re.match(r'^\d', t):
                                title = t
                                break
                        except Exception:
                            continue

                    if title == "?":
                        lines = [l.strip() for l in text.split("\n") if l.strip() and len(l.strip()) > 2]
                        for line in lines:
                            if not re.match(r'^[\d.|\s:]+$', line) and not re.search(r'20\d{2}\.', line):
                                title = line
                                break

                    for d in dates:
                        events.append((d, title))
                    found_any = True
                except Exception:
                    continue

            if found_any:
                break
        except Exception:
            continue

    # Stratégia 2: Fallback – csak dátumok, cím nélkül
    if not events:
        text = page.inner_text("body")
        for d in extract_dates_from_text(text):
            events.append((d, "?"))

    return events


def load_all_events(page, max_clicks: int = 50) -> list[tuple[date, str]]:
    print(f"[ÖRKÉNY] Oldal betöltése: {URL}")
    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)

    try:
        page.screenshot(path="debug_orkeny_page.png")
    except Exception:
        pass

    # "Továbbiak betöltése" gomb kattintgatása
    click_count = 0
    for i in range(max_clicks):
        load_more_btn = None
        for selector in [
            "text=Továbbiak betöltése",
            "button:has-text('Továbbiak')",
            ".load-more",
            "[class*='load-more']",
            "a:has-text('Továbbiak')",
        ]:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=2000):
                    load_more_btn = btn
                    break
            except Exception:
                continue

        if load_more_btn is None:
            break

        try:
            load_more_btn.click()
            click_count += 1
            page.wait_for_timeout(2000)
            if click_count % 5 == 0:
                current_dates = extract_dates_from_text(page.inner_text("body"))
                print(f"[ÖRKÉNY] {click_count}. kattintás, {len(current_dates)} dátum")
        except Exception:
            break

    print(f"[ÖRKÉNY] Összesen {click_count} 'Továbbiak betöltése' kattintás")

    # Események kinyerése
    return extract_events_from_page(page)


def check() -> dict:
    name = "Örkény István Színház"
    print(f"\n{'='*50}")
    print(f"[ÖRKÉNY] Scraper indítása: {budapest_now()}")

    result = {"name": name, "latest": None, "prev": None, "status": "error", "detail": ""}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = context.new_page()
            all_events = load_all_events(page)
            browser.close()

        if not all_events:
            result["detail"] = "Nem találtam előadást az oldalon."
            return result

        unique_events = sorted(set((d.isoformat(), t) for d, t in all_events))
        latest = max(d for d, _ in all_events)
        event_count = len(unique_events)
        print(f"[ÖRKÉNY] {event_count} előadás, max: {latest}")

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

        print(f"[ÖRKÉNY] {result['detail']}")
        return result

    except Exception as e:
        result["detail"] = f"Hiba: {e}"
        print(f"[ÖRKÉNY] {result['detail']}")
        return result


if __name__ == "__main__":
    r = check()
    print(f"\nEredmény: {r}")
