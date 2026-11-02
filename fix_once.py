"""One-off (user, 2026-11-02): fewer NBA in «Περισσότερα Νέα». The bot's NBA articles of the last 7 days keep
3rdTOP on at most 2 per day (the newest); the rest only lose the 3rdTOP tag. Editors' posts are untouched.
Delete after use."""
import datetime as dt
import re
from collections import defaultdict

import bot

since = (dt.datetime.utcnow() - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
posts = bot.wp("GET", f"posts?categories=6&tags=114&after={since}&per_page=100&context=edit"
                      "&_fields=id,date,title,tags,featured_media")
by_day = defaultdict(list)
for p in posts:
    media = bot.wp("GET", f"media/{p['featured_media']}?context=edit&_fields=source_url") if p["featured_media"] else {}
    if re.search(r"/nba-(game|news)-", media.get("source_url", "")):
        by_day[p["date"][:10]].append(p)
for day, ps in sorted(by_day.items()):
    for p in sorted(ps, key=lambda p: p["date"], reverse=True)[2:]:
        bot.wp("POST", f"posts/{p['id']}", {"tags": [t for t in p["tags"] if t != bot.TAG_3RD_TOP]})
        print(day, p["id"], "3rdTOP removed:", p["title"]["raw"][:60])
