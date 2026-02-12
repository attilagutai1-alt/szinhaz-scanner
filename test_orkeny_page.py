"""
Tesztelő script az Örkény Színház oldalához.
Megnyitja a böngészőt (headless=False), betölti az összes eseményt,
és kiírja a talált dátumokat + screenshotot készít.

Futtatás: python test_orkeny_page.py
"""
from playwright.sync_api import sync_playwright
from orkeny_last_date import extract_dates_from_text, URL


def test_page():
    print("=" * 60)
    print("ÖRKÉNY SZÍNHÁZ – OLDAL TESZT")
    print("=" * 60)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        page = context.new_page()

        print(f"\n1. Oldal betöltése: {URL}")
        try:
            page.goto(URL, wait_until="networkidle", timeout=30000)
            print("   ✓ Betöltve")
        except Exception as e:
            print(f"   ✗ Hiba: {e}")
            browser.close()
            return

        page.wait_for_timeout(3000)

        print(f"\n2. Oldal információk:")
        print(f"   - Title: {page.title()}")
        print(f"   - URL:   {page.url}")

        # Kezdeti állapot
        text = page.inner_text("body")
        dates = extract_dates_from_text(text)
        print(f"\n3. Kezdeti dátumok: {len(dates)} db")
        if dates:
            print(f"   - Legkorábbi: {min(dates)}")
            print(f"   - Legkésőbbi: {max(dates)}")

        # "Továbbiak betöltése" kattintás
        print(f"\n4. 'Továbbiak betöltése' gomb keresése...")
        click_count = 0
        for i in range(50):
            try:
                btn = page.locator("text=Továbbiak betöltése").first
                if not btn.is_visible(timeout=2000):
                    break
                btn.click()
                click_count += 1
                page.wait_for_timeout(1500)
                if click_count % 5 == 0:
                    print(f"   ... {click_count} kattintás")
            except Exception:
                break

        print(f"   ✓ Összesen {click_count} kattintás")

        # Végső állapot
        text = page.inner_text("body")
        all_dates = extract_dates_from_text(text)
        print(f"\n5. Összes dátum betöltés után: {len(all_dates)} db")
        if all_dates:
            print(f"   - Legkorábbi: {min(all_dates)}")
            print(f"   - Legkésőbbi: {max(all_dates)}")
            print(f"\n6. Utolsó 10 dátum:")
            for d in sorted(all_dates)[-10:]:
                print(f"      {d}")

        # Screenshot
        page.screenshot(path="test_orkeny_screenshot.png", full_page=True)
        print(f"\n7. Screenshot mentve: test_orkeny_screenshot.png")

        print("\n" + "=" * 60)
        print("TESZT KÉSZ – Nyomd meg az Enter-t a böngésző bezárásához...")
        print("=" * 60)
        input()
        browser.close()


if __name__ == "__main__":
    test_page()
