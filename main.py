"""
Színház scraper – fő vezérlő.

Futtatja az összes scrapelést, majd egyetlen összesítő emailt küld.
"""

import os
import re
import ssl
import smtplib
from email.message import EmailMessage
from datetime import datetime
from zoneinfo import ZoneInfo

import katona_last_date
import orkeny_last_date
import radnoti_last_date
import pbest_last_date
import vig_last_date


SCRAPERS = [
    katona_last_date,
    orkeny_last_date,
    radnoti_last_date,
    pbest_last_date,
    vig_last_date,
]

STATUS_ICONS = {
    "new_date":      "🟢",
    "count_changed": "🔵",
    "first_run":     "🔵",
    "decreased":     "🔴",
    "no_change":     "⚪",
    "error":         "❌",
}


def budapest_now():
    return datetime.now(tz=ZoneInfo("Europe/Budapest"))


def send_email(subject: str, body: str):
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    to_emails_raw = os.environ.get("TO_EMAILS")

    if not smtp_user or not smtp_pass or not to_emails_raw:
        print("\n[EMAIL] Nincs SMTP beállítva, email tartalom:")
        print(f"  Tárgy: {subject}")
        print(f"  Szöveg:\n{body}")
        return

    to_emails = [e.strip() for e in re.split(r"[;,]", to_emails_raw) if e.strip()]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(to_emails)
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)

    print(f"\n[EMAIL] Elküldve: {subject}")


def main():
    now = budapest_now()
    print(f"{'#'*60}")
    print(f"  SZÍNHÁZ SCRAPER – {now.strftime('%Y.%m.%d. %H:%M')}")
    print(f"{'#'*60}")

    # Összes scraper futtatása
    results = []
    for scraper in SCRAPERS:
        try:
            result = scraper.check()
        except Exception as e:
            result = {
                "name": getattr(scraper, "__name__", "Ismeretlen"),
                "status": "error",
                "detail": f"Váratlan hiba: {e}",
                "latest": None,
                "prev": None,
            }
        results.append(result)

    # Van-e bármilyen változás?
    has_new = any(r["status"] == "new_date" for r in results)
    has_count = any(r["status"] == "count_changed" for r in results)
    has_error = any(r["status"] == "error" for r in results)
    has_decreased = any(r["status"] == "decreased" for r in results)

    # Email tárgy
    if has_new:
        subject = "🎭 Színház – ÚJ dátum!"
    elif has_count:
        subject = "🎭 Színház – dátumszám változott"
    elif has_error:
        subject = "🎭 Színház – hiba történt"
    elif has_decreased:
        subject = "🎭 Színház – figyelem, dátum csökkent"
    else:
        subject = "🎭 Színház – nincs változás"

    # Email szöveg
    lines = []
    lines.append(f"Színház figyelő – {now.strftime('%Y.%m.%d. %H:%M')}")
    lines.append("=" * 45)
    lines.append("")

    for r in results:
        icon = STATUS_ICONS.get(r["status"], "❓")
        lines.append(f"{icon} {r['name']}")
        # A detail lehet többsoros (új előadások listája)
        for detail_line in r['detail'].split('\n'):
            lines.append(f"   {detail_line}")
        lines.append("")

    lines.append("-" * 45)
    lines.append("Katona:  https://katona.jegymester.hu/main")
    lines.append("Örkény:  https://orkenyszinhaz.hu/jegyvasarlas/kereses/eloadas")
    lines.append("Radnóti: https://radnotiszinhaz.hu/musor/")
    lines.append("PBEST:   https://pbest.hu/musor")
    lines.append("Víg:     https://vigszinhaz.hu/hu/musor")

    body = "\n".join(lines)

    send_email(subject, body)

    # Összefoglaló a konzolra
    print(f"\n{'#'*60}")
    print("  ÖSSZESÍTÉS")
    print(f"{'#'*60}")
    for r in results:
        icon = STATUS_ICONS.get(r["status"], "❓")
        detail_first_line = r['detail'].split('\n')[0]
        print(f"  {icon} {r['name']}: {detail_first_line}")


if __name__ == "__main__":
    main()
