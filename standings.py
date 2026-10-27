"""EuroLeague / EuroCup standings after every game night (user, 2026-10-28): one article per night, even when
the round is not finished (double weeks: Tuesday, Wednesday, Thursday and Friday each get one).

Data from the official, open EuroLeague API: the night's results and the round's basic standings, which
already include every game played so far in that round. The table itself is built here, never by the writer."""
import datetime as dt
import html
from collections import defaultdict

import euro

GROUP_GR = {"A": "Α", "B": "Β", "C": "Γ", "D": "Δ"}


def _when(g):
    return dt.datetime.fromisoformat((g.get("utcDate") or g["date"]).replace("Z", "")).replace(tzinfo=dt.timezone.utc)


def _round(g):
    r = g.get("round")
    return r.get("round") if isinstance(r, dict) else r


def nights(league, hours=30):
    """The latest finished regular-season game night, if its last game tipped off in the last `hours` hours:
    [{"key", "day", "round", "games", "last", "played_total"}] or []. A night counts only when all its games
    are final; an older night is never written once a newer one was played (its standings have moved on)."""
    comp = euro.COMPS[league]
    games = euro._get(f"/v2/competitions/{comp}/seasons/{euro.season_code(comp)}/games").get("data", [])
    days = defaultdict(list)
    for g in games:
        if (g.get("phaseType") or {}).get("code") == "RS":
            days[(g.get("utcDate") or g["date"])[:10]].append(g)
    now = dt.datetime.now(dt.timezone.utc)
    played = [day for day, gs in sorted(days.items()) if any(g.get("played") for g in gs)]
    if not played:
        return []
    day = played[-1]
    gs = days[day]
    last = max(_when(g) for g in gs)
    if not all(g.get("played") for g in gs) or not dt.timedelta(0) < now - last < dt.timedelta(hours=hours):
        return []
    rnd = max(_round(g) for g in gs)
    return [{"key": f"{league}-{day}", "day": day, "round": rnd, "games": sorted(gs, key=_when), "last": last,
             "played_total": sum(1 for d in days.values() for g in d if g.get("played") and _round(g) <= rnd)}]


def table(league, night):
    """Standings after the night, or None while the API has not counted every game yet."""
    comp = euro.COMPS[league]
    rows = euro._get(f"/v3/competitions/{comp}/seasons/{euro.season_code(comp)}/rounds/{night['round']}/basicstandings")["teams"]
    if sum(int(t["gamesPlayed"]) for t in rows) != 2 * night["played_total"]:
        return None
    return [{"group": t.get("groupName") or "", "pos": t["position"], "team": t["club"]["name"],
             "won": t["gamesWon"], "lost": t["gamesLost"], "diff": t["pointsDifference"],
             "form": "".join(t.get("last5Form") or [])} for t in rows]


def facts(league, night, rows, teams_gr):
    gr = lambda n: teams_gr.get(n) or n
    return {
        "type": "standings", "competition": euro.NAMES[league], "round": night["round"], "night_utc": night["day"],
        "results": [{"home": gr(g["local"]["club"]["name"]), "away": gr(g["road"]["club"]["name"]),
                     "score": f'{g["local"]["score"]}-{g["road"]["score"]}'} for g in night["games"]],
        "standings": [{"group": GROUP_GR.get(r["group"], r["group"]) if len(r["group"]) == 1 else "",
                       "pos": r["pos"], "team": gr(r["team"]), "won": r["won"], "lost": r["lost"], "diff": r["diff"]}
                      for r in rows],
    }


def block(rows, teams_gr, highlight):
    """The standings table(s): one per group (EuroCup), one for the whole league (EuroLeague)."""
    groups = defaultdict(list)
    for r in rows:
        groups[r["group"]].append(r)
    out = []
    for name, rs in sorted(groups.items()):
        if len(groups) > 1:
            out.append(f'<h3 class="wp-block-heading">Όμιλος {GROUP_GR.get(name, name)}</h3>')
        body = []
        for r in sorted(rs, key=lambda r: r["pos"]):
            team = html.escape(teams_gr.get(r["team"]) or r["team"])
            if highlight(r["team"]):
                team = f"<strong>{team}</strong>"
            body.append(f'<tr><td>{r["pos"]}</td><td>{team}</td><td>{r["won"]}-{r["lost"]}</td><td>{html.escape(str(r["diff"]))}</td></tr>')
        out.append('<figure class="wp-block-table"><table><thead><tr><th>#</th><th>Ομάδα</th><th>Ν-Η</th><th>Διαφορά</th></tr></thead>'
                   f'<tbody>{"".join(body)}</tbody></table></figure>')
    return "\n\n" + "\n\n".join(out)
