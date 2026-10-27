"""EuroLeague (E) / EuroCup (U) finished games from the official, open EuroLeague API (api-live.euroleague.net):
score, quarter partials, coaches and every player's line. The euroleaguebasketball.net website itself sits
behind a bot checkpoint and is not used."""
import datetime as dt
import json
import time
import urllib.request

API = "https://api-live.euroleague.net"
UA = {"User-Agent": "Mozilla/5.0 (totalbasket.gr)"}
COMPS = {"el": "E", "ec": "U"}
NAMES = {"el": "EuroLeague", "ec": "EuroCup"}


def _get(path):
    for i in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(API + path, headers=UA), timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError:
            raise
        except OSError:
            if i == 2:
                raise
            time.sleep(5 * (i + 1))


def season_code(comp):
    t = time.gmtime()
    return f"{comp}{t.tm_year if t.tm_mon >= 7 else t.tm_year - 1}"


def finished(league, days=3):
    """Played games of the last `days` days, oldest first."""
    comp = COMPS[league]
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    games = _get(f"/v2/competitions/{comp}/seasons/{season_code(comp)}/games").get("data", [])
    out = []
    for g in games:
        if not g.get("played"):
            continue
        when = dt.datetime.fromisoformat((g.get("utcDate") or g["date"]).replace("Z", "")).replace(tzinfo=dt.timezone.utc)
        if when >= since:
            out.append(g)
    return sorted(out, key=lambda g: g["date"])


def _person(name):
    """"SURNAME, FIRST" -> "First Surname"."""
    if "," in name:
        last, first = [x.strip() for x in name.split(",", 1)]
        name = f"{first} {last}"
    return name.title()


def _players(side):
    out = []
    for p in side.get("players", []):
        st = p.get("stats") or {}
        if not st.get("timePlayed"):
            continue                       # DNP
        out.append({
            "name": _person(p["player"]["person"]["name"]),
            "min": round(st["timePlayed"] / 60),
            "pts": int(st.get("points") or 0),
            "2p": f'{int(st.get("fieldGoalsMade2") or 0)}/{int(st.get("fieldGoalsAttempted2") or 0)}',
            "3p": f'{int(st.get("fieldGoalsMade3") or 0)}/{int(st.get("fieldGoalsAttempted3") or 0)}',
            "threes": int(st.get("fieldGoalsMade3") or 0),
            "ft": f'{int(st.get("freeThrowsMade") or 0)}/{int(st.get("freeThrowsAttempted") or 0)}',
            "reb": int(st.get("totalRebounds") or 0),
            "ast": int(st.get("assistances") or 0),
            "stl": int(st.get("steals") or 0),
            "to": int(st.get("turnovers") or 0),
            "blk": int(st.get("blocksFavour") or 0),
            "pir": int(st.get("valuation") or 0),
        })
    return out


def facts(league, g):
    comp = COMPS[league]
    stats = _get(f"/v3/competitions/{comp}/seasons/{season_code(comp)}/games/{g['gameCode']}/stats")
    teams = []
    for side in ("local", "road"):
        t, s = g[side], stats.get(side, {})
        partials = t.get("partials") or {}
        quarters = [partials.get(f"partials{i}") for i in range(1, 5)] + list((partials.get("extraPeriods") or {}).values())
        tot = (s.get("total") or {})
        teams.append({
            "home_away": "home" if side == "local" else "away",
            "name": t["club"]["name"],
            "crest": (t["club"].get("images") or {}).get("crest"),
            "score": int(t.get("score") or 0),
            "winner": int(t.get("score") or 0) > int(g["road" if side == "local" else "local"].get("score") or 0),
            "quarters": quarters,
            "coach": _person((s.get("coach") or {}).get("name", "")),
            "players": _players(s),
            "team_totals": {k: tot.get(k) for k in ("totalRebounds", "assistances", "turnovers", "steals",
                                                    "fieldGoalsMade3", "fieldGoalsAttempted3", "valuation")},
        })
    return {
        "type": "game", "competition": f"{NAMES[league]} — {g.get('roundName') or ''}".strip(" —"),
        "date_utc": (g.get("utcDate") or g["date"]) + ("Z" if not str(g.get("utcDate", "")).endswith("Z") else ""),
        "venue": (g.get("venue") or {}).get("name"),
        "teams": teams,
    }
