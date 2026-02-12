"""
Katona József Színház – scraper modul.

A katona.jegymester.hu oldalról bináris kereséssel megkeresi
az utolsó oldalt ahol van esemény, majd kinyeri a legkésőbbi dátumot.
"""

import os
import re
import json
from datetime import datetime, date
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


BASE_URL = "https://katona.jegymester.hu/main"
STATE_FILE = "state.json"
NO_EVENTS_TEXT = "Sajnáljuk, de az Ön által megadott szűrési feltételek alapján nem találtunk egy eseményt sem."

HU_MONTHS = {
    "január": 1, "február": 2, "március": 3, "április": 4, "május": 5, "június": 6,
    "július": 7, "augusztus": 8, "szeptember": 9, "október": 10, "november": 11, "december": 12
}


def budapest_now():
    return datetime.now(tz=ZoneInfo("Europe/Budapest"))


def build_url(active_page: int) -> str:
    return f"{BASE_URL}?activePage={active_page}&osl=events&ot=tickets&searchPhrase="


def page_is_empty(page) -> bool:
    try:
        body_text = page.inner_text("body")
        return NO_EVENTS_TEXT in body_text
    except Exception:
        return True


def extract_dates_from_text(text: str) -> list[date]:
    dates = []

    # 2026. január 30.
    for m in re.finditer(
        r"\b(20\d{2})\.\s*(január|február|március|április|május|június|július|augusztus|szeptember|október|november|december)\s+(\d{1,2})\.",
        text, re.IGNORECASE
    ):
        y, mon_name, d = int(m.group(1)), m.group(2).lower(), int(m.group(3))
        mo = HU_MONTHS.get(mon_name)
        if mo:
            try:
                dates.append(date(y, mo, d))
            except ValueError:
                pass

    # január 30, csütörtök / január 30.
    for m in re.finditer(
        r"\b(január|február|március|április|május|június|július|augusztus|szeptember|október|november|december)\s+(\d{1,2})[.,]",
        text, re.IGNORECASE
    ):
        mon_name, d = m.group(1).lower(), int(m.group(2))
        mo = HU_MONTHS.get(mon_name)
        if mo:
            now = budapest_now()
            y = now.year if mo >= now.month else now.year + 1
            try:
                dates.append(date(y, mo, d))
            except ValueError:
                pass

    # 2026.01.30 / 2026-01-30
    for m in re.finditer(r"\b(20\d{2})[.\-](0[1-9]|1[0-2])[.\-](0[1-9]|[12]\d|3[01])\b", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dates.append(date(y, mo, d))
        except ValueError:
            pass

    return sorted(set(dates))


def find_last_nonempty_page(page, max_pages=60) -> int:
    print("[KATONA] Ellenőrzöm az 1. oldalt...")
    page.goto(build_url(1), wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(2000)

    try:
        page.screenshot(path="debug_page1.png")
    except Exception:
        pass

    if page_is_empty(page):
        print("[KATONA] Az 1. oldal üres!")
        return 0

    lo, hi = 1, max_pages
    while lo < hi:
        mid = (lo + hi + 1) // 2
        page.goto(build_url(mid), wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(1500)
        if page_is_empty(page):
            hi = mid - 1
        else:
            lo = mid

    print(f"[KATONA] Utolsó nem üres oldal: {lo}")
    return lo


def scrape_latest_date(page, last_page: int) -> date | None:
    all_dates = []
    for p in range(1, last_page + 1):
        print(f"[KATONA] Scraping oldal {p}/{last_page}...")
        page.goto(build_url(p), wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(1500)
        if page_is_empty(page):
            continue
        text = page.inner_text("body")
        dates = extract_dates_from_text(text)
        all_dates.extend(dates)

    return max(all_dates) if all_dates else None


def check() -> dict:
    """
    Futtatja a Katona scrapelést.
    Visszaad egy dict-et: {"name", "latest", "prev", "status", "detail"}
    """
    name = "Katona József Színház"
    print(f"\n{'='*50}")
    print(f"[KATONA] Scraper indítása: {budapest_now()}")

    result = {"name": name, "latest": None, "prev": None, "status": "error", "detail": ""}

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = context.new_page()

            last_page = find_last_nonempty_page(page, max_pages=60)
            if last_page == 0:
                result["detail"] = "Az 1. oldal is üres (hálózati hiba / oldalváltozás / blokkolás)."
                browser.close()
                return result

            latest = scrape_latest_date(page, last_page)
            browser.close()

        if latest is None:
            result["detail"] = f"Nem találtam dátumot. Utolsó nem üres oldal: {last_page}"
            return result

        # State
        state = {}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)

        prev_str = state.get("latest_date")
        prev = datetime.strptime(prev_str, "%Y-%m-%d").date() if prev_str else None

        state["latest_date"] = latest.isoformat()
        state["last_page"] = last_page
        state["checked_at_budapest"] = budapest_now().isoformat()
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

        result["latest"] = latest
        result["prev"] = prev

        if prev is None:
            result["status"] = "first_run"
            result["detail"] = f"Első futás. Max dátum: {latest}"
        elif latest > prev:
            result["status"] = "new_date"
            result["detail"] = f"ÚJ! {prev} → {latest}"
        elif latest < prev:
            result["status"] = "decreased"
            result["detail"] = f"Csökkent! {prev} → {latest}"
        else:
            result["status"] = "no_change"
            result["detail"] = f"Nincs változás. Max: {latest}"

        print(f"[KATONA] {result['detail']}")
        return result

    except Exception as e:
        result["detail"] = f"Hiba: {e}"
        print(f"[KATONA] {result['detail']}")
        return result


if __name__ == "__main__":
    r = check()
    print(f"\nEredmény: {r}")
