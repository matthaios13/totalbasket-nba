"""One-off: trash duplicates/test drafts (restorable) and redo images on the bot's posts. Delete after use."""
import re
import tempfile

import bot
import card
from teams import colours, greek, logo_url

for pid in (65189, 65179, 65173, 65175):
    try:
        p = bot.wp("DELETE", f"posts/{pid}")          # no force=true -> Trash
        print(pid, "trash:", p.get("status"))
    except Exception as ex:
        print(pid, "trash failed:", ex)

POSTS = [65177, 65181, 65183, 65185, 65187, 65191, 65193, 65195]
PEOPLE = {65181: "Mike Brown", 65183: "Joel Embiid", 65187: "LeBron James",
          65191: "Brandin Podziemski", 65195: "JJ Redick"}
for pid in POSTS:
    p = bot.wp("GET", f"posts/{pid}?context=edit&_fields=id,title,content,featured_media")
    media = bot.wp("GET", f"media/{p['featured_media']}?context=edit&_fields=source_url")
    kind, eid = re.search(r"nba-(game|news)-(\d+)", media["source_url"]).groups()
    art = {"title": p["title"]["raw"], "content": p["content"]["raw"], "photo_person": PEOPLE.get(pid, "")}
    # drop any earlier credit line (e.g. the wrong LeBron photo); person_image adds the right one
    art["content"] = re.sub(r'\s*<p class="wp-block-paragraph"><em>Φωτογραφία:.*?</em></p>', "", art["content"])
    path = bot.person_image(art)
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
            "Content-Type": "image/jpeg", "Content-Disposition": f'attachment; filename="nba-{kind}-{eid}-v3.jpg"'})
    bot.wp("POST", f"media/{new['id']}", {"alt_text": art["title"], "caption": art.get("_caption", "")})
    bot.wp("POST", f"posts/{pid}", {"featured_media": new["id"], "content": art["content"]})
    print(pid, "photo" if art.get("_caption") else kind, "->", new["source_url"])
