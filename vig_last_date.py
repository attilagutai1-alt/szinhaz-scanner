"""
Vígszínház – scraper modul.

A https://vigszinhaz.hu/hu/musor oldalról scrape-eli az előadások
dátumait. Az oldal havi bontásban mutatja a műsort, a következő hónapra
a navigációs nyíllal lehet lépni. A dátumok a produkciós URL-ekből
nyerhetők ki (YYYYMMDD-HHMM formátum).
"""

import os
import re
import json
from datetime import datetime, date
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


URL = "https://vigszinhaz.hu/hu/musor"
STATE_FILE = "vig_state.json"

HU_MONTHS = {
    "január": 1, "február": 2, "március": 3, "április": 4, "május": 5, "június": 6,
    "július": 7, "augusztus": 8, "szeptember": 9, "október": 10, "november": 11, "december": 12,
}


def budapest_now():
    return datetime.now(tz=ZoneInfo("Europe/Budapest"))


def extract_dates_from_html(html: str) -> list[date]:
    """
    Dátumok kinyerése a HTML-ből, elsősorban a produkciós URL-ekből.
    Formátum: /hu/produkciok/DARABNEV/YYYYMMDD-HHMM
    """
    dates = []

    # 1) Produkciós URL-ek: /hu/produkciok/xyz/20260213-1900
    for m in re.finditer(r"/hu/produkciok/[^/]+/(20\d{2})(\d{2})(\d{2})-\d{4}", html):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            dates.append(date(y, mo, d))
        except ValueError:
            pass

    return sorted(set(dates))


def extract_dates_from_text(text: str) -> list[date]:
    """
    Fallback: dátumok kinyerése a szövegből.
    Formátum: "2026. február 13.péntek 19:00"
    """
    dates = []

    for m in re.finditer(
        r"(20\d{2})\.\s*(január|február|március|április|május|június|július|augusztus|szeptember|október|november|december)\s+(\d{1,2})\.",
        text, re.IGNORECASE
    ):
        y = int(m.group(1))
        mon_name = m.group(2).lower()
        d = int(m.group(3))
        mo = HU_MONTHS.get(mon_name)
        if mo:
            try:
                dates.append(date(y, mo, d))
            except ValueError:
                pass

    return sorted(set(dates))


def scrape_all_months(page, max_months: int = 12) -> list[date]:
    """
    Betölti az aktuális hónapot, kinyeri a dátumokat, majd a következő
    hónap gombra kattintva továbblép. Addig megy, amíg van előadás.
    """
    all_dates = []
    empty_streak = 0

    print(f"[VÍG] Oldal betöltése: {URL}")
    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)

    try:
        page.screenshot(path="debug_vig_page.png")
    except Exception:
        pass

    for month_idx in range(max_months):
        # Dátumok kinyerése
        html = page.content()
        text = page.inner_text("body")

        month_dates = extract_dates_from_html(html)
        if not month_dates:
            month_dates = extract_dates_from_text(text)

        if month_dates:
            print(f"[VÍG] Hónap {month_idx}: {len(month_dates)} dátum, {min(month_dates)} - {max(month_dates)}")
            all_dates.extend(month_dates)
            empty_streak = 0
        else:
            print(f"[VÍG] Hónap {month_idx}: nincs dátum")
            empty_streak += 1

        if empty_streak >= 2:
            print(f"[VÍG] 2 üres hónap, befejezem")
            break

        # Következő hónap – keressük a jobbra nyilat / "next" gombot
        next_clicked = False
        for selector in [
            "a[href*='offset=1']",
            "button[aria-label*='next']",
            "button[aria-label*='Next']",
            "button[aria-label*='következő']",
            "[class*='next']",
            "[class*='forward']",
            "svg[class*='right'] >> xpath=..",
            # A Vígszínház oldalon valószínűleg van egy jobbra nyíl
            "button >> nth=-1",  # utolsó gomb a navigációban
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
            # Próbáljunk egyedi megközelítést: keressünk jobbra nyilat a hónap címnél
            try:
                # A Vígszínház oldalon a hónap navigáció általában nyilakkal működik
                arrows = page.locator("button, a").all()
                for arrow in arrows:
                    try:
                        text_content = arrow.inner_text(timeout=500)
                        # Üres gomb vagy ">" karakter - valószínűleg nyíl
                        if text_content.strip() in ["›", "»", ">", "→", ""]:
                            bbox = arrow.bounding_box()
                            if bbox and bbox.get("x", 0) > 500:  # jobb oldalon van
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

    return sorted(set(all_dates))


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
            all_dates = scrape_all_months(page)
            browser.close()

        if not all_dates:
            result["detail"] = "Nem találtam dátumot az oldalon."
            return result

        latest = max(all_dates)
        event_count = len(all_dates)
        print(f"[VÍG] {event_count} egyedi dátum, max: {latest}")

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

        print(f"[VÍG] {result['detail']}")
        return result

    except Exception as e:
        result["detail"] = f"Hiba: {e}"
        print(f"[VÍG] {result['detail']}")
        return result


if __name__ == "__main__":
    r = check()
    print(f"\nEredmény: {r}")
