"""One-off: publish the test drafts, move the duplicate Rizzetta interview to Trash. Delete after use."""
import html

import bot

PUBLISH = [65304, 65306, 65308, 65310, 65313, 65317, 65319]
TRASH = 65315
for pid in PUBLISH + [TRASH]:
    p = bot.wp("GET", f"posts/{pid}?context=edit&_fields=id,status,title,categories")
    title = p["title"]["raw"]
    if pid == TRASH:
        if "Ριτζ" not in title:
            print(pid, "NOT trashed, unexpected title:", title)
            continue
        bot.wp("DELETE", f"posts/{pid}")            # no force -> Trash
        print(pid, "trash", title[:70])
    elif p["status"] == "draft":
        r = bot.wp("POST", f"posts/{pid}", {"status": "publish"})
        print(pid, r["status"], p["categories"], html.unescape(title)[:70], r["link"])
    else:
        print(pid, "already", p["status"], title[:70])
