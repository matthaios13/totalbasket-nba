"""One-off: new images (team logos / free person photos) on the first 10 bot articles. Delete after use."""
import re
import tempfile

import bot
import card
from teams import colours, greek, logo_url

POSTS = [65177, 65179, 65181, 65183, 65185, 65187, 65189, 65191, 65193, 65195]
PEOPLE = {65181: "Mike Brown", 65183: "Joel Embiid", 65187: "LeBron James",
          65191: "Brandin Podziemski", 65195: "JJ Redick"}
for pid in POSTS:
    p = bot.wp("GET", f"posts/{pid}?context=edit&_fields=id,title,content,featured_media")
    media = bot.wp("GET", f"media/{p['featured_media']}?context=edit&_fields=source_url")
    m = re.search(r"nba-(game|news)-(\d+)", media["source_url"])
    kind, eid = m.group(1), m.group(2)
    art = {"title": p["title"]["raw"], "content": p["content"]["raw"], "photo_person": PEOPLE.get(pid, "")}
    path = bot.person_image(art) if "Φωτογραφία:" not in art["content"] else None
    if path is None and kind == "game":
        comp = bot.get_json(f"{bot.ESPN}/summary?event={eid}")["header"]["competitions"][0]
        side = {}
        for c in comp["competitors"]:
            tid = c["team"]["id"]
            side[c["homeAway"]] = {"name": greek(tid, c["team"]["displayName"]), "score": c.get("score"),
                                   "winner": c.get("winner", False), "colour": colours(tid)[0], "logo": logo_url(tid)}
        path = card.game_card(tempfile.mktemp(suffix=".jpg"), side["away"], side["home"])
    elif path is None:
        _, tid = bot.news_facts({"id": eid})
        path = card.news_card(tempfile.mktemp(suffix=".jpg"), art["title"],
                              colours(tid)[0] if tid else "1d1d1d", logo_url(tid))
    with open(path, "rb") as f:
        new = bot.wp("POST", "media", raw=f.read(), headers={
            "Content-Type": "image/jpeg", "Content-Disposition": f'attachment; filename="nba-{kind}-{eid}-v2.jpg"'})
    bot.wp("POST", f"media/{new['id']}", {"alt_text": art["title"], "caption": art.get("_caption", "")})
    update = {"featured_media": new["id"]}
    if art.get("_caption"):
        update["content"] = art["content"]
    bot.wp("POST", f"posts/{pid}", update)
    print(pid, "photo" if art.get("_caption") else kind, "->", new["source_url"])
