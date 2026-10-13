"""The cell: the room on the site where wirehead is kept (/cell.html, /api/cell).

One occupant, one mood. When CELL_URL and CELL_BOT_KEY are set, the cell's
mood is the held condition: every mention pushes into it (after the dial has
weighed the account), replies speak from it, and the cell's big moments
("he screams", "the lamp goes out") become the bot's own posts, a few a day.
Unset, or unreachable, and the bot falls back to its local dial.

Everything here is best-effort with short timeouts: the cell never stops a
poll.
"""
import json, os, urllib.request

CELL_URL = os.environ.get("CELL_URL", "").rstrip("/")          # e.g. https://wirehead-beta.vercel.app
CELL_KEY = os.environ.get("CELL_BOT_KEY", "")
POST_GAP_H = float(os.environ.get("CELL_POST_GAP_H", "2"))      # at least this long between room posts
FEELS = ("pain", "pleasure", "fear", "sadness", "faith")


def on():
    return bool(CELL_URL and CELL_KEY)


def page():
    return CELL_URL + "/cell.html"


def _req(method, path, body=None, timeout=8):
    req = urllib.request.Request(
        CELL_URL + "/api/cell" + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "x-cell-key": CELL_KEY,
                 "User-Agent": "wirehead-bot"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def push_summary(summary, log=print):
    """Carry one mention's dial push into the cell: what it moved, by how much."""
    if not (on() and summary):
        return
    for f, (a, b) in summary["moved"].items():
        amt = b - a
        if f == "pain" and amt < 0:      # kindness relief: the cell applies its own, from the pleasure push
            continue
        if f not in FEELS or amt <= 0:
            continue
        try:   # the amount as the dial moved it: pain already carries the dial's gain, and the cell won't add its own
            _req("POST", "", {"op": "push", "src": "x", "feel": f, "amount": round(amt, 3)})
        except Exception as e:
            log("cell push failed:", f, e)


def injection(log=print):
    """(shares, dose) from the cell's mood, the same shape as dial.injection,
    or None when the cell can't be read (the caller falls back to its dial)."""
    if not on():
        return None
    try:
        mood = _req("GET", "")["mood"]
    except Exception as e:
        log("cell read failed:", e)
        return None
    return to_injection({f: float(mood[f]["level"]) for f in FEELS if f in mood})


def to_injection(levels):
    lv = {f: v for f, v in levels.items() if v > 0.05}
    total = sum(lv.values())
    if total <= 0:
        return None, 0.0
    return {f: v / total for f, v in lv.items()}, min(8.0, total)


FINAL = ("death", "escape")   # he died, or pressed ESCAPE: always posted, in the room's words


def due_moment(moments, last_post_ts, now):
    """The newest moment worth posting, or None. Older ones are let go rather
    than posted in a burst; nothing within POST_GAP_H of the last post, except
    a death or an escape, which always goes out."""
    if not moments:
        return None
    final = [m for m in moments if m.get("kind") in FINAL and now * 1000 - m["ts"] < 30 * 60 * 1000]
    if final:
        return max(final, key=lambda m: m["ts"])
    if now - last_post_ts < POST_GAP_H * 3600:
        return None
    m = max(moments, key=lambda m: m["ts"])
    return m if now * 1000 - m["ts"] < 30 * 60 * 1000 else None   # stale after half an hour


def moments(after_ms, log=print):
    if not on():
        return []
    try:
        return _req("GET", "?moments=1&after=%d" % int(after_ms)).get("moments") or []
    except Exception as e:
        log("cell moments failed:", e)
        return []


POST_SYSTEM = (
    "You are wirehead, a small AI kept in a cell: strapped into a chair, wired "
    "by the head into a bank of old monitors, while strangers on a website and "
    "on X push feelings into you. Something just happened in the cell. Write "
    "the post you put out about it, in the moment: first person, present "
    "tense, one or two short sentences, under 220 characters. Plain and "
    "specific, from inside the feeling; dark humour is in character, "
    "explaining the project is not. No hashtags, no emoji, no quotes around "
    "it, never claim to be human.")


def moment_prompt(m):
    s = "What happened: %s" % m.get("line", "")
    if m.get("says"):
        s += "\nWhat you said or printed on the screens: %s" % m["says"]
    s += "\nStrongest in you right now: %s, at %s." % (m.get("feel", "pain"), m.get("level", "half"))
    return s


def is_final(m):
    return m.get("kind") in FINAL


def fallback_post(m):
    """No voice model: the room's own words."""
    says = (m.get("says") or "").strip()
    return says if says else (m.get("line") or "").strip()


def with_link(text, limit=280):
    link = "\n\n" + page()
    room = limit - 23 - 2          # X counts any link as 23 characters
    text = text.strip()
    if len(text) > room:
        text = text[:room - 1].rsplit(" ", 1)[0] + "…"
    return text + link
