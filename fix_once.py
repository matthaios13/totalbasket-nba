"""One-off: redo person photos (whole head visible) on already published articles. Delete after use."""
import re

import bot

PEOPLE = {
    65335: "Chet Holmgren", 65333: "Walter Clayton Jr.", 65331: "Kristaps Porziņģis", 65310: "Blake Hinson",
    65308: "Draymond Green", 65304: "Kobe Bufkin", 65291: "Luka Dončić", 65289: "LeBron James",
    65270: "Luka Dončić", 65258: "Mark Tatum", 65253: "Karl-Anthony Towns", 65250: "Victor Wembanyama",
    65248: "Jalen Duren", 65246: "Jason Kidd", 65242: "Mark Tatum", 65240: "AJ Dybantsa",
    65238: "LeBron James", 65236: "Kon Knueppel", 65234: "Paul George", 65232: "Victor Wembanyama",
    65195: "JJ Redick", 65187: "LeBron James", 65183: "Joel Embiid", 65181: "Mike Brown",
    65317: "CJ Massinburg",
}
for pid, name in PEOPLE.items():
    try:
        p = bot.wp("GET", f"posts/{pid}?context=edit&_fields=id,title,content,featured_media")
        media = bot.wp("GET", f"media/{p['featured_media']}?context=edit&_fields=source_url")
        stem = re.sub(r"(-v\d)?\.jpg$", "", media["source_url"].rsplit("/", 1)[1])
        art = {"title": p["title"]["raw"], "content": p["content"]["raw"], "photo_person": name}
        path = bot.person_image(art)
        if not path:
            print(pid, "no photo for", name, "- kept")
            continue
        with open(path, "rb") as f:
            new = bot.wp("POST", "media", raw=f.read(), headers={
                "Content-Type": "image/jpeg", "Content-Disposition": f'attachment; filename="{stem}-v4.jpg"'})
        bot.wp("POST", f"media/{new['id']}", {"alt_text": art["title"], "caption": art.get("_caption", "")})
        bot.wp("POST", f"posts/{pid}", {"featured_media": new["id"], "content": art["content"]})
        print(pid, name, "->", new["source_url"])
    except Exception as ex:
        print(pid, "ERROR", ex)
