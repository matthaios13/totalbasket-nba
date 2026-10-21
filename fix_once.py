"""One-off: TOP tags on the first 10 bot articles + fix a mixed-script title. Delete after use."""
import bot

POSTS = [65177, 65179, 65181, 65183, 65185, 65187, 65189, 65191, 65193, 65195]
TOP = 65187  # LeBron's Sixers debut
for pid in POSTS:
    p = bot.wp("GET", f"posts/{pid}?context=edit&_fields=id,tags,title")
    tags = [t for t in p["tags"] if t not in (bot.TAG_2ND_TOP, bot.TAG_3RD_TOP)]
    tags.append(bot.TAG_2ND_TOP if pid == TOP else bot.TAG_3RD_TOP)
    data = {"tags": tags}
    title = p["title"]["raw"]
    if "Εμπίid" in title:
        data["title"] = title.replace("Εμπίid", "Εμπίντ")
    bot.wp("POST", f"posts/{pid}", data)
    print(pid, data.get("title", title)[:60], tags)
