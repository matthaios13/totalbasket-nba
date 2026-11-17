"""One-off: post 70223's test tagline was cut mid-word; put the whole sentence. Delete after use."""
import bot

pid = 70223
t = "Ο νέος παίκτης της Μακάμπι Τελ Αβίβ προσγειώθηκε στο Ισραήλ και αναμένεται διαθέσιμος για το ματς με τον Ολυμπιακό."
bot.wp("POST", f"posts/{pid}", {"tb_bot_meta": {"tagline": t}})
print(pid, bot.wp("GET", f"posts/{pid}?context=edit&_fields=tb_bot_meta"))
