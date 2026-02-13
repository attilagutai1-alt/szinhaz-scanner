"""
Katona József Színház – scraper modul.

A katona.jegymester.hu oldalról bináris kereséssel megkeresi
az utolsó oldalt ahol van esemény, majd kinyeri a dátumot és az előadás nevét.
"""

import os
import re
import json
from datetime import datetime, date
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from scraper_utils import compare_events


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

    for m in re.finditer(r"\b(20\d{2})[.\-](0[1-9]|1[0-2])[.\-](0[1-9]|[12]\d|3[01])\b", text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dates.append(date(y, mo, d))
        except ValueError:
            pass

    return sorted(set(dates))


def extract_events_from_page(page) -> list[tuple[date, str]]:
    """
    Megpróbálja a jegymester oldalról az egyes eseményeket kinyerni
    (dátum + előadásnév). Több szelektor-stratégiát is kipróbál.
    """
    events = []

    # Stratégia 1: Keresünk event card elemeket
    for selector in [
        ".event-card", ".card", "[class*='event-item']",
        "[class*='event-card']", ".list-group-item", "article",
        "[class*='ticket']", ".row[class*='event']"
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

                    title = "?"
                    for title_sel in ["h3", "h4", "h5", "h2", "a[href*='event']", ".title", "[class*='title']", "[class*='name']"]:
                        try:
                            title_el = item.locator(title_sel).first
                            t = title_el.inner_text(timeout=500).strip()
                            if t and len(t) > 2 and not re.match(r'^[\d.]+$', t):
                                title = t
                                break
                        except Exception:
                            continue

                    if title == "?":
                        lines = [l.strip() for l in text.split("\n") if l.strip() and len(l.strip()) > 2]
                        for line in lines:
                            if not re.search(r'20\d{2}[.\-]|január|február|március|április|május|június|július|augusztus|szeptember|október|november|december|\d{1,2}:\d{2}|Ft|jegy|vásárl', line, re.IGNORECASE):
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

    # Stratégia 2: Szöveg alapú – cím sor a dátum előtt
    if not events:
        text = page.inner_text("body")
        lines = text.split("\n")
        for i, line in enumerate(lines):
            dates = extract_dates_from_text(line)
            if dates:
                # A cím valószínűleg az előző nem-üres sor
                title = "?"
                for j in range(i - 1, max(i - 5, -1), -1):
                    prev_line = lines[j].strip()
                    if prev_line and len(prev_line) > 2 and not re.search(r'20\d{2}[.\-]|\d{1,2}:\d{2}|Ft', prev_line):
                        title = prev_line
                        break
                for d in dates:
                    events.append((d, title))

    # Stratégia 3: Végső fallback – csak dátumok
    if not events:
        text = page.inner_text("body")
        for d in extract_dates_from_text(text):
            events.append((d, "?"))

    return events


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


def scrape_all_events(page, last_page: int) -> list[tuple[date, str]]:
    all_events = []
    for p in range(1, last_page + 1):
        print(f"[KATONA] Scraping oldal {p}/{last_page}...")
        page.goto(build_url(p), wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(1500)
        if page_is_empty(page):
            continue
        page_events = extract_events_from_page(page)
        all_events.extend(page_events)

    return all_events


def check() -> dict:
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

            all_events = scrape_all_events(page, last_page)
            browser.close()

        if not all_events:
            result["detail"] = f"Nem találtam előadást. Utolsó nem üres oldal: {last_page}"
            return result

        unique_events = sorted(set((d.isoformat(), t) for d, t in all_events))
        latest = max(d for d, _ in all_events)
        event_count = len(unique_events)

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
        state["last_page"] = last_page
        state["checked_at_budapest"] = budapest_now().isoformat()
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

        result["latest"] = latest
        result["prev"] = prev
        result["status"], result["detail"] = compare_events(
            latest, event_count, prev, prev_count,
            [list(e) for e in unique_events], prev_events
        )

        print(f"[KATONA] {result['detail']}")
        return result

    except Exception as e:
        result["detail"] = f"Hiba: {e}"
        print(f"[KATONA] {result['detail']}")
        return result


if __name__ == "__main__":
    r = check()
    print(f"\nEredmény: {r}")
