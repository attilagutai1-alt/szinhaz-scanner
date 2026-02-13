"""
Heti mozi összefoglaló – Művész, Puskin, Toldi, Corvin.

Vasárnap futtatva összegyűjti a következő hét (hétfő–vasárnap) vetítéseit,
lekéri a műfajokat a film-oldalakról, generál interaktív HTML-t,
és emailben elküldi a GitHub Pages linket.
"""

import os
import re
import ssl
import json
import smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright


CINEMAS = [
    {"name": "Művész", "url": "https://muveszmozi.hu/"},
    {"name": "Puskin", "url": "https://puskinmozi.hu/"},
    {"name": "Toldi",  "url": "https://toldimozi.hu/"},
    {"name": "Corvin", "url": "https://corvinmozi.hu/"},
]

HU_DAYS_SHORT = {0: "H", 1: "K", 2: "Sze", 3: "Cs", 4: "P", 5: "Szo", 6: "V"}
HU_DAYS_LONG = {0: "Hétfő", 1: "Kedd", 2: "Szerda", 3: "Csütörtök", 4: "Péntek", 5: "Szombat", 6: "Vasárnap"}
HU_MONTHS = {
    1: "jan", 2: "feb", 3: "már", 4: "ápr", 5: "máj", 6: "jún",
    7: "júl", 8: "aug", 9: "sze", 10: "okt", 11: "nov", 12: "dec",
}
HU_MONTH_PARSE = {
    "jan": 1, "feb": 2, "már": 3, "márc": 3, "ápr": 4, "máj": 5, "jún": 6,
    "júl": 7, "aug": 8, "sze": 9, "szep": 9, "okt": 10, "nov": 11, "dec": 12,
}

GITHUB_PAGES_URL = os.environ.get(
    "PAGES_URL",
    "https://USERNAME.github.io/REPO-NAME/moziheti.html"
)


def budapest_now():
    return datetime.now(tz=ZoneInfo("Europe/Budapest"))


def get_target_week() -> tuple[date, date]:
    today = budapest_now().date()
    days_ahead = (7 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    monday = today + timedelta(days=days_ahead)
    sunday = monday + timedelta(days=6)
    return monday, sunday


def parse_day_filter_date(date_text: str, year: int) -> date | None:
    m = re.match(r'([a-záéíóöőúüű]+)\.?\s+(\d{1,2})', date_text.strip().lower())
    if not m:
        return None
    month = HU_MONTH_PARSE.get(m.group(1))
    if not month:
        return None
    try:
        return date(year, month, int(m.group(2)))
    except ValueError:
        return None


def get_week_numbers_for_target(monday: date) -> tuple[int, int]:
    prev_thursday = monday - timedelta(days=4)
    this_thursday = monday + timedelta(days=3)
    return prev_thursday.isocalendar()[1], this_thursday.isocalendar()[1]


def extract_screenings_for_day(page, target_date: date, cinema_name: str) -> list[dict]:
    data = page.evaluate("""() => {
        const results = [];
        const tiles = document.querySelectorAll('.react-film-tile-container');
        tiles.forEach(tile => {
            const titleEl = tile.querySelector('.react-film-tile-title-item');
            if (!titleEl) return;
            const filmTitle = titleEl.textContent.trim();
            const linkEl = tile.querySelector('a.react-film-tile-title');
            const filmUrl = linkEl ? linkEl.getAttribute('href') : '';
            const containers = tile.querySelectorAll('.react-purchase-container:not(.disabled)');
            containers.forEach(container => {
                const btn = container.querySelector('button.react-purchase-content');
                if (!btn) return;
                const time = btn.textContent.trim();
                if (/^\\d{1,2}:\\d{2}$/.test(time)) {
                    results.push({ film: filmTitle, time: time, url: filmUrl || '' });
                }
            });
        });
        return results;
    }""")

    return [{
        "film": item["film"],
        "time": item["time"],
        "url": item.get("url", ""),
        "cinema": cinema_name,
        "date": target_date.isoformat(),
        "day_short": HU_DAYS_SHORT[target_date.weekday()],
        "day_long": HU_DAYS_LONG[target_date.weekday()],
    } for item in data]


def click_week(page, week_num: int) -> bool:
    week_str = f"{week_num:02d}"
    try:
        week_buttons = page.locator("div.react-week-filter-number").all()
        for btn in week_buttons:
            if btn.inner_text(timeout=2000).strip() == week_str:
                btn.click()
                page.wait_for_timeout(3000)
                print(f"    Hét {week_str} kiválasztva ✓")
                return True
        print(f"    Hét {week_str} nem található")
        return False
    except Exception as e:
        print(f"    Hétváltó hiba: {e}")
        return False


def click_day_and_scrape(page, target_date: date, cinema_name: str) -> list[dict]:
    year = target_date.year
    day_name = HU_DAYS_SHORT[target_date.weekday()]
    day_boxes = page.locator("div.react-day-filter-box:not(.disabled)").all()
    for box in day_boxes:
        try:
            date_el = box.locator(".react-day-filter-date")
            date_text = date_el.inner_text(timeout=2000).strip()
            parsed = parse_day_filter_date(date_text, year)
            if parsed == target_date:
                box.click()
                page.wait_for_timeout(2000)
                screenings = extract_screenings_for_day(page, target_date, cinema_name)
                print(f"      {date_text} ({day_name}): {len(screenings)} vetítés")
                return screenings
        except Exception:
            continue
    print(f"      {HU_MONTHS[target_date.month]}. {target_date.day} ({day_name}): nem elérhető")
    return []


def scrape_genres(page, film_urls: dict[str, str]) -> dict[str, list[str]]:
    """
    Bejárja a film-oldalakat és kinyeri a műfajokat.
    film_urls: {filmcím: relatív_url}
    Visszaad: {filmcím: [műfaj1, műfaj2, ...]}
    """
    genres = {}
    print(f"\n{'='*40}")
    print(f"Műfajok lekérése ({len(film_urls)} film)...")

    for film, rel_url in film_urls.items():
        if not rel_url:
            genres[film] = []
            continue

        full_url = rel_url if rel_url.startswith("http") else f"https://artmozi.hu{rel_url}"
        try:
            page.goto(full_url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1000)

            # Műfaj linkek: <a href="/mufaj/filmdrama">filmdráma</a>
            genre_list = page.evaluate("""() => {
                const links = document.querySelectorAll('a[href*="/mufaj/"]');
                return Array.from(links).map(a => a.textContent.trim()).filter(t => t.length > 0);
            }""")

            genres[film] = genre_list
            if genre_list:
                print(f"  {film}: {', '.join(genre_list)}")
            else:
                print(f"  {film}: (nincs műfaj)")

        except Exception as e:
            print(f"  {film}: HIBA – {e}")
            genres[film] = []

    return genres


def scrape_all() -> tuple[list[dict], dict[str, list[str]], date, date]:
    monday, sunday = get_target_week()
    week1, week2 = get_week_numbers_for_target(monday)
    print(f"Célhét: {monday} (hétfő) – {sunday} (vasárnap)")
    print(f"Mozis hetek: {week1:02d} (H-Sze) és {week2:02d} (Cs-V)")

    all_screenings = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = context.new_page()
        page.set_default_timeout(60000)

        # 1) Vetítések scrape-elése mozi oldalanként
        for cinema in CINEMAS:
            name = cinema["name"]
            url = cinema["url"]
            print(f"\n{'='*40}")
            print(f"[{name}] {url}")
            try:
                page.goto(url, wait_until="networkidle", timeout=90000)
                page.wait_for_timeout(5000)
                try:
                    page.evaluate("document.querySelector('#block-artmozi-homepage-react-block')?.scrollIntoView()")
                    page.wait_for_timeout(2000)
                except Exception:
                    pass

                print(f"  Mozis hét {week1:02d} (H–Sze)")
                click_week(page, week1)
                for d in range(3):
                    target = monday + timedelta(days=d)
                    all_screenings.extend(click_day_and_scrape(page, target, name))

                print(f"  Mozis hét {week2:02d} (Cs–V)")
                click_week(page, week2)
                for d in range(3, 7):
                    target = monday + timedelta(days=d)
                    all_screenings.extend(click_day_and_scrape(page, target, name))

            except Exception as e:
                print(f"  [{name}] HIBA: {e}")

        # 2) Egyedi film URL-ek összegyűjtése műfaj scrape-hez
        film_urls = {}
        for s in all_screenings:
            if s["film"] not in film_urls and s.get("url"):
                film_urls[s["film"]] = s["url"]

        # 3) Műfajok lekérése
        genres = scrape_genres(page, film_urls)

        browser.close()

    print(f"\nÖsszesen {len(all_screenings)} vetítés, {len(film_urls)} film")
    return all_screenings, genres, monday, sunday


def generate_html(all_screenings: list, genres: dict, monday: date, sunday: date) -> str:
    mon_str = monday.strftime('%Y.%m.%d.')
    sun_str = sunday.strftime('%Y.%m.%d.')

    screenings_json = json.dumps(all_screenings, ensure_ascii=False)
    genres_json = json.dumps(genres, ensure_ascii=False)

    days = []
    for i in range(7):
        d = monday + timedelta(days=i)
        days.append({
            "date": d.isoformat(),
            "short": HU_DAYS_SHORT[d.weekday()],
            "label": f"{HU_DAYS_SHORT[d.weekday()]} {HU_MONTHS[d.month]}.{d.day}."
        })
    days_json = json.dumps(days, ensure_ascii=False)

    # Összegyűjtjük az összes műfajt a filter gombokhoz
    all_genres = sorted(set(g for gl in genres.values() for g in gl), key=str.lower)
    all_genres_json = json.dumps(all_genres, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="hu">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Mozihét {mon_str} – {sun_str}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}

body {{
  font-family: 'DM Sans', sans-serif;
  background: #0a0a0a;
  color: #e8e8e8;
  min-height: 100vh;
}}

.header {{
  padding: 2rem 1.5rem 1rem;
  text-align: center;
  border-bottom: 1px solid #222;
}}
.header h1 {{
  font-family: 'Space Mono', monospace;
  font-size: clamp(1.2rem, 4vw, 1.8rem);
  letter-spacing: -0.02em;
  color: #fff;
  margin-bottom: 0.3rem;
}}
.header .subtitle {{
  color: #888;
  font-size: 0.85rem;
}}

.filters {{
  padding: 1rem 1.5rem;
  border-bottom: 1px solid #1a1a1a;
  position: sticky;
  top: 0;
  background: #0a0a0a;
  z-index: 10;
}}
.filter-section {{ margin-bottom: 0.75rem; }}
.filter-section:last-child {{ margin-bottom: 0; }}
.filter-row {{
  display: flex;
  gap: 0.4rem;
  flex-wrap: wrap;
}}
.filter-label {{
  font-family: 'Space Mono', monospace;
  font-size: 0.65rem;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: #555;
  margin-bottom: 0.35rem;
}}
.filter-btn {{
  padding: 0.35rem 0.7rem;
  border: 1px solid #333;
  border-radius: 2rem;
  background: transparent;
  color: #aaa;
  font-family: 'DM Sans', sans-serif;
  font-size: 0.78rem;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}}
.filter-btn:hover {{ border-color: #666; color: #fff; }}
.filter-btn.active {{
  background: #fff;
  color: #0a0a0a;
  border-color: #fff;
  font-weight: 600;
}}

.cinema-btn[data-cinema="Művész"].active {{ background: #A0DAE8; border-color: #A0DAE8; color: #0a0a0a; }}
.cinema-btn[data-cinema="Puskin"].active {{ background: #FFD451; border-color: #FFD451; color: #0a0a0a; }}
.cinema-btn[data-cinema="Toldi"].active {{ background: #EB7126; border-color: #EB7126; color: #fff; }}
.cinema-btn[data-cinema="Corvin"].active {{ background: #e54545; border-color: #e54545; color: #fff; }}

.genre-btn.active {{
  background: #c084fc;
  border-color: #c084fc;
  color: #0a0a0a;
}}

.content {{
  padding: 1.5rem;
  max-width: 900px;
  margin: 0 auto;
}}

.film-card {{
  margin-bottom: 1.2rem;
  border: 1px solid #1e1e1e;
  border-radius: 12px;
  overflow: hidden;
  background: #111;
  transition: border-color 0.2s;
}}
.film-card:hover {{ border-color: #333; }}
.film-header {{
  padding: 1rem 1.2rem 0.4rem;
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 0.5rem;
}}
.film-title {{
  font-size: 1.1rem;
  font-weight: 700;
  color: #fff;
}}
.film-title a {{
  color: inherit;
  text-decoration: none;
}}
.film-title a:hover {{ text-decoration: underline; }}
.film-count {{
  font-family: 'Space Mono', monospace;
  font-size: 0.7rem;
  color: #666;
  white-space: nowrap;
}}
.film-genres {{
  padding: 0 1.2rem 0.5rem;
  display: flex;
  gap: 0.3rem;
  flex-wrap: wrap;
}}
.genre-tag {{
  font-size: 0.65rem;
  padding: 0.15rem 0.5rem;
  border-radius: 1rem;
  background: #1e1e1e;
  color: #999;
  font-weight: 500;
}}
.film-screenings {{
  padding: 0 1.2rem 1rem;
}}
.cinema-row {{
  display: flex;
  align-items: baseline;
  gap: 0.75rem;
  margin-bottom: 0.4rem;
  flex-wrap: wrap;
}}
.cinema-name {{
  font-weight: 600;
  font-size: 0.8rem;
  min-width: 60px;
  color: #999;
}}
.cinema-name.muvesz {{ color: #A0DAE8; }}
.cinema-name.puskin {{ color: #FFD451; }}
.cinema-name.toldi {{ color: #EB7126; }}
.cinema-name.corvin {{ color: #e54545; }}

.time-chip {{
  display: inline-block;
  padding: 0.2rem 0.55rem;
  border-radius: 4px;
  background: #1e1e1e;
  font-family: 'Space Mono', monospace;
  font-size: 0.75rem;
  color: #ccc;
  margin: 0.1rem;
}}
.day-label {{
  font-size: 0.65rem;
  color: #666;
  margin-right: 0.1rem;
}}

.empty {{
  text-align: center;
  padding: 4rem 2rem;
  color: #555;
}}
.empty .emoji {{ font-size: 2.5rem; margin-bottom: 1rem; }}

.stats {{
  text-align: center;
  padding: 1rem;
  color: #444;
  font-size: 0.75rem;
  font-family: 'Space Mono', monospace;
  border-top: 1px solid #1a1a1a;
}}
</style>
</head>
<body>

<div class="header">
  <h1>🎬 Mozihét</h1>
  <div class="subtitle">{mon_str} (hétfő) – {sun_str} (vasárnap)</div>
</div>

<div class="filters">
  <div class="filter-section">
    <div class="filter-label">Mozi</div>
    <div class="filter-row" id="cinema-filters">
      <button class="filter-btn cinema-btn active" data-cinema="all">Mind</button>
      <button class="filter-btn cinema-btn" data-cinema="Művész">Művész</button>
      <button class="filter-btn cinema-btn" data-cinema="Puskin">Puskin</button>
      <button class="filter-btn cinema-btn" data-cinema="Toldi">Toldi</button>
      <button class="filter-btn cinema-btn" data-cinema="Corvin">Corvin</button>
    </div>
  </div>
  <div class="filter-section">
    <div class="filter-label">Nap</div>
    <div class="filter-row" id="day-filters">
      <button class="filter-btn day-btn active" data-day="all">Mind</button>
    </div>
  </div>
  <div class="filter-section">
    <div class="filter-label">Műfaj</div>
    <div class="filter-row" id="genre-filters">
      <button class="filter-btn genre-btn active" data-genre="all">Mind</button>
    </div>
  </div>
</div>

<div class="content" id="content"></div>
<div class="stats" id="stats"></div>

<script>
const screenings = {screenings_json};
const genres = {genres_json};
const days = {days_json};
const allGenres = {all_genres_json};

let activeCinema = 'all';
let activeDay = 'all';
let activeGenre = 'all';

// Nap gombok
const dayFilters = document.getElementById('day-filters');
days.forEach(d => {{
  const btn = document.createElement('button');
  btn.className = 'filter-btn day-btn';
  btn.dataset.day = d.date;
  btn.textContent = d.label;
  dayFilters.appendChild(btn);
}});

// Műfaj gombok
const genreFilters = document.getElementById('genre-filters');
allGenres.forEach(g => {{
  const btn = document.createElement('button');
  btn.className = 'filter-btn genre-btn';
  btn.dataset.genre = g;
  btn.textContent = g;
  genreFilters.appendChild(btn);
}});

// Filter kattintások
function setupFilters(selector, varSetter) {{
  document.querySelectorAll(selector).forEach(btn => {{
    btn.addEventListener('click', () => {{
      document.querySelectorAll(selector).forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      varSetter(btn);
      render();
    }});
  }});
}}
setupFilters('.cinema-btn', btn => activeCinema = btn.dataset.cinema);
setupFilters('.day-btn', btn => activeDay = btn.dataset.day);
setupFilters('.genre-btn', btn => activeGenre = btn.dataset.genre);

function render() {{
  const filtered = screenings.filter(s => {{
    if (activeCinema !== 'all' && s.cinema !== activeCinema) return false;
    if (activeDay !== 'all' && s.date !== activeDay) return false;
    if (activeGenre !== 'all') {{
      const fg = genres[s.film] || [];
      if (!fg.includes(activeGenre)) return false;
    }}
    return true;
  }});

  // Csoportosítás film szerint
  const films = {{}};
  filtered.forEach(s => {{
    if (!films[s.film]) films[s.film] = {{ url: s.url, cinemas: {{}}, count: 0 }};
    films[s.film].count++;
    if (!films[s.film].cinemas[s.cinema]) films[s.film].cinemas[s.cinema] = [];
    films[s.film].cinemas[s.cinema].push({{ day: s.day_short, time: s.time, date: s.date }});
  }});

  const content = document.getElementById('content');

  // Rendezés: legtöbb vetítés elől
  const filmNames = Object.keys(films).sort((a, b) => films[b].count - films[a].count || a.localeCompare(b, 'hu'));

  if (filmNames.length === 0) {{
    content.innerHTML = '<div class="empty"><div class="emoji">🎬</div>Nincs vetítés a szűrésnek megfelelően.</div>';
    document.getElementById('stats').textContent = '';
    return;
  }}

  const cinemaOrder = ['Művész', 'Puskin', 'Toldi', 'Corvin'];
  const cinemaClass = {{ 'Művész': 'muvesz', 'Puskin': 'puskin', 'Toldi': 'toldi', 'Corvin': 'corvin' }};

  let html = '';
  filmNames.forEach(film => {{
    const info = films[film];
    let filmUrl = info.url || '';
    if (filmUrl && !filmUrl.startsWith('http')) filmUrl = 'https://artmozi.hu' + filmUrl;
    const filmGenres = genres[film] || [];

    html += '<div class="film-card"><div class="film-header"><div class="film-title">';
    if (filmUrl) html += '<a href="' + filmUrl + '" target="_blank">';
    html += film;
    if (filmUrl) html += ' ↗</a>';
    html += '</div>';
    html += '<span class="film-count">' + info.count + ' vetítés</span>';
    html += '</div>';

    if (filmGenres.length) {{
      html += '<div class="film-genres">';
      filmGenres.forEach(g => {{ html += '<span class="genre-tag">' + g + '</span>'; }});
      html += '</div>';
    }}

    html += '<div class="film-screenings">';
    cinemaOrder.forEach(cinema => {{
      if (!info.cinemas[cinema]) return;
      const times = info.cinemas[cinema];
      html += '<div class="cinema-row">';
      html += '<span class="cinema-name ' + cinemaClass[cinema] + '">' + cinema + '</span>';
      html += '<div>';
      times.forEach(t => {{
        html += '<span class="day-label">' + t.day + '</span>';
        html += '<span class="time-chip">' + t.time + '</span> ';
      }});
      html += '</div></div>';
    }});

    html += '</div></div>';
  }});

  content.innerHTML = html;
  document.getElementById('stats').textContent = filmNames.length + ' film · ' + filtered.length + ' vetítés';
}}

render();
</script>
</body>
</html>"""
    return html


def send_email(monday: date, sunday: date, page_url: str):
    smtp_user = os.environ.get("SMTP_USER")
    smtp_pass = os.environ.get("SMTP_PASS")
    to_emails_raw = os.environ.get("TO_EMAILS")

    mon_str = f"{HU_MONTHS[monday.month]}. {monday.day}."
    sun_str = f"{HU_MONTHS[sunday.month]}. {sunday.day}."
    subject = f"🎬 Mozihét: {mon_str} – {sun_str}"
    body = f"Mozihét: {monday.strftime('%Y.%m.%d.')} (hétfő) – {sunday.strftime('%Y.%m.%d.')} (vasárnap)\n\n{page_url}"

    if not smtp_user or not smtp_pass or not to_emails_raw:
        print(f"\n[EMAIL] Nincs SMTP, tartalom:")
        print(f"  Tárgy: {subject}")
        print(f"  {body}")
        return

    to_emails = [e.strip() for e in re.split(r"[;,]", to_emails_raw) if e.strip()]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = ", ".join(to_emails)
    msg.set_content(body)

    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as server:
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)

    print(f"\n[EMAIL] Elküldve: {subject}")


def main():
    now = budapest_now()
    print(f"{'#'*60}")
    print(f"  HETI MOZI ÖSSZEFOGLALÓ – {now.strftime('%Y.%m.%d. %H:%M')}")
    print(f"{'#'*60}")

    all_screenings, genres, monday, sunday = scrape_all()

    html = generate_html(all_screenings, genres, monday, sunday)
    html_path = "docs/moziheti.html"
    os.makedirs("docs", exist_ok=True)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\nHTML mentve: {html_path}")

    send_email(monday, sunday, GITHUB_PAGES_URL)


if __name__ == "__main__":
    main()
