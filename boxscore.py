"""The stats block at the end of a game article, built from the data itself (never by the writer), so every
number is exact. The writer only supplies Greek spellings for team, coach and player names.

    Τα δεκάλεπτα: 26-22, 54-48, 70-75, 93-104
    Παρί (Λεφέβρ): Χιφί 25 (3), Χέιζ-Ντέιβις 12 (2), ...
"""
import html
import unicodedata


def quarters_line(home_partials, away_partials):
    """Running score after each period, home team first (Greek sites' convention)."""
    h = a = 0
    out = []
    for hp, ap in zip(home_partials, away_partials):
        h, a = h + int(hp or 0), a + int(ap or 0)
        out.append(f"{h}-{a}")
    return ", ".join(out)


def _caps(s):
    """Greek capitals carry no accents: "Ολυμπιακός" -> "ΟΛΥΜΠΙΑΚΟΣ"."""
    return "".join(c for c in unicodedata.normalize("NFD", s.upper()) if not unicodedata.combining(c))


def team_line(team, coach, players):
    """players: [{"name": str, "pts": int, "threes": int}] in roster order (starters first)."""
    parts = []
    for p in players:
        s = f"{p['name']} {p['pts']}"
        if p.get("threes"):
            s += f" ({p['threes']})"
        parts.append(s)
    head = team + (f" ({coach})" if coach else "")        # normal case, like the rest of the site
    return f"<strong>{html.escape(head)}:</strong> {html.escape(', '.join(parts))}"


def block(home, away):
    """home/away: {"team", "coach", "partials": [..], "players": [..]} with Greek names already applied."""
    rows = [f"<strong>Τα δεκάλεπτα:</strong> {quarters_line(home['partials'], away['partials'])}",
            team_line(home["team"], home.get("coach"), home["players"]),
            team_line(away["team"], away.get("coach"), away["players"])]
    return "\n\n" + "\n\n".join(f'<p class="wp-block-paragraph">{r}</p>' for r in rows)


def greek(names_gr, latin):
    """Greek spelling from the writer's map, falling back to the Latin name (never mixed in one word)."""
    return (names_gr or {}).get(latin) or latin
