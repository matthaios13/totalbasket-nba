"""One-off: give the first 10 bot articles the new logo images. Delete after use."""
import re
import tempfile

import bot
import card
from teams import colours, greek, logo_url

POSTS = [65177, 65179, 65181, 65183, 65185, 65187, 65189, 65191, 65193, 65195]
for pid in POSTS:
    p = bot.wp("GET", f"posts/{pid}?context=edit&_fields=id,title,featured_media")
    media = bot.wp("GET", f"media/{p['featured_media']}?context=edit&_fields=source_url")
    m = re.search(r"nba-(game|news)-(\d+)", media["source_url"])
    kind, eid = m.group(1), m.group(2)
    title = p["title"]["raw"]
    path = tempfile.mktemp(suffix=".jpg")
    if kind == "game":
        comp = bot.get_json(f"{bot.ESPN}/summary?event={eid}")["header"]["competitions"][0]
        side = {}
        for c in comp["competitors"]:
            tid = c["team"]["id"]
            side[c["homeAway"]] = {"name": greek(tid, c["team"]["displayName"]), "score": c.get("score"),
                                   "winner": c.get("winner", False), "colour": colours(tid)[0], "logo": logo_url(tid)}
        card.game_card(path, side["away"], side["home"])
    else:
        _, tid = bot.news_facts({"id": eid})
        card.news_card(path, title, colours(tid)[0] if tid else "1d1d1d", logo_url(tid))
    with open(path, "rb") as f:
        new = bot.wp("POST", "media", raw=f.read(), headers={
            "Content-Type": "image/jpeg", "Content-Disposition": f'attachment; filename="nba-{kind}-{eid}-logo.jpg"'})
    bot.wp("POST", f"media/{new['id']}", {"alt_text": title})
    bot.wp("POST", f"posts/{pid}", {"featured_media": new["id"]})
    print(pid, kind, eid, "->", new["source_url"])
