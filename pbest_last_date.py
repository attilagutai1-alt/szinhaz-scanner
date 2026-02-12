"""
Pintér Béla és Társulata – scraper modul.

A https://pbest.hu/musor oldalról scrape-eli az összes előadás dátumát.
Az oldal szerver-renderelt, minden előadás egy oldalon van.
A dátumok az event_rdate URL paraméterből nyerhetők ki (YYYYMMDDHHMMSS formátum).
"""

import os
import re
import json
from datetime import datetime, date
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


URL = "https://pbest.hu/musor"
STATE_FILE = "pbest_state.json"

# Rövidített magyar hónapnevek a pbest.hu-n: Feb, Már, Ápr, stb.
HU_SHORT_MONTHS = {
    "jan": 1, "feb": 2, "már": 3, "ápr": 4, "máj": 5, "jún": 6,
    "júl": 7, "aug": 8, "sze": 9, "okt": 10, "nov": 11, "dec": 12,
}


def budapest_now():
    return datetime.now(tz=ZoneInfo("Europe/Budapest"))


def extract_dates_from_text(text: str) -> list[date]:
    """
    Dátumok kinyerése többféle formátumból.
    """
    dates = []

    # 1) event_rdate URL paraméterből: 20260213190000
    for m in re.finditer(r"event_rdate=(20\d{2})(\d{2})(\d{2})\d{6}", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dates.append(date(y, mo, d))
        except ValueError:
            pass

    # 2) "2026. Feb 13." vagy "2026. Már 1." formátum
    for m in re.finditer(
        r"\b(20\d{2})\.\s*(Jan|Feb|Már|Ápr|Máj|Jún|Júl|Aug|Sze|Okt|Nov|Dec)\s+(\d{1,2})\.",
        text, re.IGNORECASE
    ):
        y = int(m.group(1))
        mon_name = m.group(2).lower()
        d = int(m.group(3))
        mo = HU_SHORT_MONTHS.get(mon_name)
        if mo:
            try:
                dates.append(date(y, mo, d))
            except ValueError:
                pass

    # 3) Fallback: 2026.03.14 vagy 2026-03-14
    for m in re.finditer(r"\b(20\d{2})[.\-](0[1-9]|1[0-2])[.\-](0[1-9]|[12]\d|3[01])\b", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dates.append(date(y, mo, d))
        except ValueError:
            pass

    return sorted(set(dates))


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

            # Az oldal teljes HTML-je kell az event_rdate paraméterekhez
            html_content = page.content()
            body_text = page.inner_text("body")
            browser.close()

        # Dátumok kinyerése mind a HTML-ből, mind a szövegből
        all_dates = extract_dates_from_text(html_content)
        all_dates.extend(extract_dates_from_text(body_text))
        all_dates = sorted(set(all_dates))

        if not all_dates:
            result["detail"] = "Nem találtam dátumot az oldalon."
            return result

        latest = max(all_dates)
        event_count = len(all_dates)
        print(f"[PBEST] {event_count} egyedi dátum, max: {latest}")

        # State
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

        print(f"[PBEST] {result['detail']}")
        return result

    except Exception as e:
        result["detail"] = f"Hiba: {e}"
        print(f"[PBEST] {result['detail']}")
        return result


if __name__ == "__main__":
    r = check()
    print(f"\nEredmény: {r}")
