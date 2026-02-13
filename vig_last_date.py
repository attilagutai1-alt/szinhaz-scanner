"""
Vígszínház – scraper modul.

A https://vigszinhaz.hu/hu/musor oldalról scrape-eli az előadások dátumait és neveit.
Next.js szerver-renderelt oldal, havi megjelenítéssel.
A dátumok és címek a produkciós URL-ekből nyerhetők ki:
  /hu/produkciok/DARABNEV/YYYYMMDD-HHMM
"""

import os
import re
import json
from datetime import datetime, date
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from scraper_utils import compare_events


URL = "https://vigszinhaz.hu/hu/musor"
STATE_FILE = "vig_state.json"

HU_MONTHS = {
    "január": 1, "február": 2, "március": 3, "április": 4, "május": 5, "június": 6,
    "július": 7, "augusztus": 8, "szeptember": 9, "október": 10, "november": 11, "december": 12
}


def budapest_now():
    return datetime.now(tz=ZoneInfo("Europe/Budapest"))


def extract_events_from_html(html: str) -> list[tuple[date, str]]:
    """
    (dátum, előadásnév) párok kinyerése a HTML-ből.
    URL formátum: /hu/produkciok/SHOW_NAME/YYYYMMDD-HHMM
    """
    events = []

    # 1) Keresünk <a> tageket produkciós URL-lel ÉS link szöveggel
    for m in re.finditer(
        r'<a[^>]*href="[^"]*?/hu/produkciok/([^/]+)/(20\d{2})(\d{2})(\d{2})-\d{4}[^"]*"[^>]*>([^<]+)</a>',
        html, re.IGNORECASE
    ):
        slug = m.group(1)
        y, mo, d = int(m.group(2)), int(m.group(3)), int(m.group(4))
        link_text = m.group(5).strip()
        title = link_text if link_text else slug.replace("_", " ").replace("-", " ").title()
        try:
            events.append((date(y, mo, d), title))
        except ValueError:
            pass

    # 2) Fallback: ha nincs link szöveg, használjuk a slug-ot
    if not events:
        for m in re.finditer(
            r'/hu/produkciok/([^/]+)/(20\d{2})(\d{2})(\d{2})-\d{4}',
            html
        ):
            slug = m.group(1)
            y, mo, d = int(m.group(2)), int(m.group(3)), int(m.group(4))
            title = slug.replace("_", " ").replace("-", " ").title()
            try:
                events.append((date(y, mo, d), title))
            except ValueError:
                pass

    return events


def scrape_all_months(page, max_months: int = 12) -> list[tuple[date, str]]:
    """
    Betölti az aktuális hónapot, kinyeri az előadásokat, majd a következő
    hónap gombra kattintva továbblép.
    """
    all_events = []
    empty_streak = 0

    print(f"[VÍG] Oldal betöltése: {URL}")
    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)

    try:
        page.screenshot(path="debug_vig_page.png")
    except Exception:
        pass

    for month_idx in range(max_months):
        html = page.content()
        month_events = extract_events_from_html(html)

        if month_events:
            month_dates = [d for d, _ in month_events]
            print(f"[VÍG] Hónap {month_idx}: {len(month_events)} előadás, {min(month_dates)} - {max(month_dates)}")
            all_events.extend(month_events)
            empty_streak = 0
        else:
            print(f"[VÍG] Hónap {month_idx}: nincs előadás")
            empty_streak += 1

        if empty_streak >= 2:
            print(f"[VÍG] 2 üres hónap, befejezem")
            break

        # Következő hónap
        next_clicked = False
        for selector in [
            "a[href*='offset=1']",
            "button[aria-label*='next']",
            "button[aria-label*='Next']",
            "button[aria-label*='következő']",
            "[class*='next']",
            "[class*='forward']",
            "svg[class*='right'] >> xpath=..",
            "button >> nth=-1",
        ]:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    next_clicked = True
                    page.wait_for_timeout(3000)
                    break
            except Exception:
                continue

        if not next_clicked:
            try:
                arrows = page.locator("button, a").all()
                for arrow in arrows:
                    try:
                        text_content = arrow.inner_text(timeout=500)
                        if text_content.strip() in ["›", "»", ">", "→", ""]:
                            bbox = arrow.bounding_box()
                            if bbox and bbox.get("x", 0) > 500:
                                arrow.click()
                                next_clicked = True
                                page.wait_for_timeout(3000)
                                break
                    except Exception:
                        continue
            except Exception:
                pass

        if not next_clicked:
            print(f"[VÍG] Nem találtam következő hónap gombot")
            break

    return all_events


def check() -> dict:
    name = "Vígszínház"
    print(f"\n{'='*50}")
    print(f"[VÍG] Scraper indítása: {budapest_now()}")

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
        print(f"[VÍG] {event_count} előadás, max: {latest}")

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

        print(f"[VÍG] {result['detail']}")
        return result

    except Exception as e:
        result["detail"] = f"Hiba: {e}"
        print(f"[VÍG] {result['detail']}")
        return result


if __name__ == "__main__":
    r = check()
    print(f"\nEredmény: {r}")
