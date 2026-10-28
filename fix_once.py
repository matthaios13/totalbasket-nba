"""One-off: redraw the score cards of published result articles with the home team on the left
(user, 2026-10-28). Photos (articles whose image has a credit) are left alone. Delete after use."""
import html
import json
import re
import tempfile

import bot
import card
import euro
from teams import colours, logo_url

POSTS = json.load(open("gameposts.json"))
euro_games = {lg: {g["gameCode"]: g for g in euro.finished(lg, days=60)} for lg in ("el", "ec")}
for pid, league, gid, _ in POSTS:
    try:
        p = bot.wp("GET", f"posts/{pid}?context=edit&_fields=id,title,featured_media")
        media = bot.wp("GET", f"media/{p['featured_media']}?context=edit&_fields=source_url,caption")
        if html.unescape(re.sub(r"<[^>]+>", "", media["caption"]["raw"] or "")).strip():
            print(pid, "photo, kept")
            continue
        path = tempfile.mktemp(suffix=".jpg")
        if league == "nba":
            facts, _ = bot.game_facts({"id": gid, "season": {"type": 2}, "date": ""})
            side = {t["home_away"]: {"name": t["name_gr"], "score": t["score"], "winner": t["winner"],
                                     "colour": colours(t["espn_id"])[0], "logo": logo_url(t["espn_id"])}
                    for t in facts["teams"]}
            card.game_card(path, side["away"], side["home"])
        else:
            facts = euro.facts(league, euro_games[league][int(gid.split("-")[1])])
            m = re.match(r"\s*(.+?)\s+[–-]\s+(.+?)\s+\d+-\d+", p["title"]["raw"])
            names = {"home": m.group(1), "away": m.group(2)} if m else {}
            side = {t["home_away"]: {"name": names.get(t["home_away"], t["name"]), "score": t["score"],
                                     "winner": t["winner"], "colour": card.crest_colour(t["crest"]), "logo": t["crest"]}
                    for t in facts["teams"]}
            card.game_card(path, side["away"], side["home"], euro.NAMES[league])
        with open(path, "rb") as f:
            new = bot.wp("POST", "media", raw=f.read(), headers={
                "Content-Type": "image/jpeg", "Content-Disposition": f'attachment; filename="{league}-game-{gid}-v2.jpg"'})
        bot.wp("POST", f"media/{new['id']}", {"alt_text": p["title"]["raw"]})
        bot.wp("POST", f"posts/{pid}", {"featured_media": new["id"]})
        print(pid, "->", new["source_url"])
    except Exception as ex:
        print(pid, "ERROR", ex)
