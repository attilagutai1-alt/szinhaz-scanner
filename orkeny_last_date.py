"""
Örkény István Színház – scraper modul.

Az orkenyszinhaz.hu/jegyvasarlas/kereses/eloadas oldalról
a "Továbbiak betöltése" gombot kattintgatva betölti az összes
előadást, majd kinyeri a legkésőbbi dátumot.
"""

import os
import re
import json
from datetime import datetime, date
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


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


def load_all_events(page, max_clicks: int = 50) -> str:
    print(f"[ÖRKÉNY] Oldal betöltése: {URL}")
    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)

    try:
        page.screenshot(path="debug_orkeny_page.png")
    except Exception:
        pass

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
    return page.inner_text("body")


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
            body_text = load_all_events(page)
            browser.close()

        all_dates = extract_dates_from_text(body_text)

        if not all_dates:
            result["detail"] = "Nem találtam dátumot az oldalon."
            return result

        latest = max(all_dates)
        event_count = len(all_dates)
        print(f"[ÖRKÉNY] {event_count} egyedi dátum, max: {latest}")

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

        print(f"[ÖRKÉNY] {result['detail']}")
        return result

    except Exception as e:
        result["detail"] = f"Hiba: {e}"
        print(f"[ÖRKÉNY] {result['detail']}")
        return result


if __name__ == "__main__":
    r = check()
    print(f"\nEredmény: {r}")
