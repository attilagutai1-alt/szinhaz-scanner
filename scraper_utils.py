"""
Közös segédfüggvények a scraperekhez.
"""

from datetime import date


def compare_events(
    latest: date,
    event_count: int,
    prev_latest: date | None,
    prev_count: int | None,
    current_events: list[list[str]],   # [[date_iso, title], ...]
    prev_events: list[list[str]],      # [[date_iso, title], ...]
) -> tuple[str, str]:
    """
    Összehasonlítja az aktuális és korábbi eredményeket.
    Visszaad: (status, detail_szöveg)
    """
    if prev_latest is None:
        return "first_run", f"Első futás. Max dátum: {latest} ({event_count} előadás)"

    parts = []
    status = "no_change"

    # Max dátum változás
    if latest > prev_latest:
        status = "new_date"
        parts.append(f"ÚJ MAX DÁTUM! {prev_latest} → {latest}")
    elif latest < prev_latest:
        status = "decreased"
        parts.append(f"Max dátum csökkent! {prev_latest} → {latest}")

    # Darabszám változás
    if prev_count is not None and event_count != prev_count:
        diff = event_count - prev_count
        sign = "+" if diff > 0 else ""
        parts.append(f"Előadások száma: {prev_count} → {event_count} ({sign}{diff})")
        if status == "no_change":
            status = "count_changed"

    # Új előadások keresése
    current_set = {(e[0], e[1]) for e in current_events}
    prev_set = {(e[0], e[1]) for e in prev_events}
    new_events = sorted(current_set - prev_set)
    removed_events = sorted(prev_set - current_set)

    if new_events:
        if status == "no_change":
            status = "count_changed"
        parts.append(f"Új előadások ({len(new_events)}):")
        for d, title in new_events[:20]:
            parts.append(f"  ✚ {d} – {title}")
        if len(new_events) > 20:
            parts.append(f"  ... és még {len(new_events) - 20} további")

    if removed_events:
        if status == "no_change":
            status = "count_changed"
        parts.append(f"Eltűnt előadások ({len(removed_events)}):")
        for d, title in removed_events[:10]:
            parts.append(f"  ✖ {d} – {title}")
        if len(removed_events) > 10:
            parts.append(f"  ... és még {len(removed_events) - 10} további")

    if not parts:
        return "no_change", f"Nincs változás. Max: {latest} ({event_count} előadás)"

    return status, "\n".join(parts)
