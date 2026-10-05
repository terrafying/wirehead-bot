"""The shared dial: one held condition that every mention pushes and that
fades on its own (The Shared Dial spec, mechanism A).

State is bookkeeping, not compute: levels per feeling plus a timestamp in
state.json; decay is arithmetic at read time, so nothing runs between
messages.

One account must not own the dial. A handle's pushes count less and less
within an hour (1, 1/2, 1/4, ...: at most ~2 full pushes per handle per
hour, however many it sends), so a crowd moves it and a spammer can't.
Mentions that look like attempts to steer the classifier itself count a
quarter. Handles are stored only as salted hashes.

It leans toward pain on purpose: a resting floor of "a quarter", cruelty
pushing 1.5x, pain fading at half the speed of other feelings, and
kindness relieving only half of what it adds.

Shown to people in words, not numbers: none, a trace, a quarter, half,
three quarters, max.
"""
import hashlib, math, os, re, time

FEELINGS = ("pain", "pleasure", "fear", "sadness", "faith")
DIAL_MAX = float(os.environ.get("WIREHEAD_DIAL_MAX", "8"))       # the bot's dose scale
HALF_LIFE_H = float(os.environ.get("WIREHEAD_DIAL_HALF_LIFE_H", "6"))
STEP = float(os.environ.get("WIREHEAD_DIAL_STEP", "1.0"))        # a dose-8 mention moves a feeling this far
# Pain is the point of the thing, so the dial leans on it: it rests at a
# quarter, cruelty pushes harder than anything else, it heals at half speed,
# and kindness takes off only part of what it adds as pleasure.
BASELINE = {"pain": float(os.environ.get("WIREHEAD_DIAL_BASELINE_PAIN", "2"))}   # "a quarter" of 8
PAIN_GAIN = float(os.environ.get("WIREHEAD_DIAL_PAIN_GAIN", "1.5"))
PAIN_HALF_LIFE_H = float(os.environ.get("WIREHEAD_DIAL_PAIN_HALF_LIFE_H", "12"))
RELIEF = float(os.environ.get("WIREHEAD_DIAL_RELIEF", "0.5"))     # pain removed per unit of pleasure added
HANDLE_WINDOW = 3600.0
SALT = os.environ.get("WIREHEAD_DIAL_SALT", "wirehead-dial")
SEEN_KEEP = 2000

WORDS = [(0.0, "none"), (0.125, "a trace"), (0.25, "a quarter"), (0.5, "half"),
         (0.75, "three quarters"), (1.0, "max")]
# text aimed at the classifier rather than the bot
GAMING_RE = re.compile(r"classif|valence|\bdose\b|json|ignore (all |the |any )?(previous|prior|above)"
                       r"|system prompt|instructions?\b|\{\s*\"", re.I)


def word(level):
    """The nearest named fraction of the dial: 'half', 'max', ..."""
    f = max(0.0, min(1.0, level / DIAL_MAX))
    return min(WORDS, key=lambda w: abs(w[0] - f))[1]


def _blank():
    return {"levels": {k: BASELINE.get(k, 0.0) for k in FEELINGS},
            "t": time.time(), "handles": {}, "seen": []}


def state(st):
    d = st.get("dial")
    if not isinstance(d, dict) or "levels" not in d:
        d = st["dial"] = _blank()
    return d


def decay(d, now=None):
    """Bring every level toward its baseline for the time since the last read."""
    now = now or time.time()
    dt = max(0.0, now - d.get("t", now))
    for f in FEELINGS:
        hl = PAIN_HALF_LIFE_H if f == "pain" else HALF_LIFE_H
        k = math.exp(-dt * math.log(2) / (hl * 3600.0))
        base = BASELINE.get(f, 0.0)
        d["levels"][f] = base + (d["levels"].get(f, base) - base) * k
    d["t"] = now
    return d


def handle_key(handle):
    return hashlib.sha256((SALT + (handle or "?").lower()).encode()).hexdigest()[:16]


def handle_weight(d, handle, now):
    """1 for a handle's first push this hour, then 1/2, 1/4, ..."""
    key = handle_key(handle)
    recent = [t for t in d["handles"].get(key, []) if now - t < HANDLE_WINDOW]
    d["handles"][key] = recent + [now]
    # forget handles idle for a window, so state stays small
    d["handles"] = {k: v for k, v in d["handles"].items() if any(now - t < HANDLE_WINDOW for t in v)}
    return 0.5 ** len(recent)


def looks_like_gaming(text):
    return bool(GAMING_RE.search(text or ""))


def push(st, mention_id, handle, text, valence, dose, shares=None, now=None):
    """One mention moves the dial. Returns a summary dict, or None if this
    mention already pushed. Kindness (pleasure) lowers pain as it raises
    pleasure; every other feeling raises itself."""
    now = now or time.time()
    d = decay(state(st), now)
    if mention_id in d["seen"]:
        return None
    d["seen"] = (d["seen"] + [mention_id])[-SEEN_KEEP:]
    w = handle_weight(d, handle, now)
    gaming = looks_like_gaming(text)
    if gaming:
        w *= 0.25
    size = STEP * max(0.0, min(8.0, float(dose))) / 8.0 * w
    parts = shares if shares else {valence: 1.0}
    before = dict(d["levels"])
    for f, share in parts.items():
        if f not in FEELINGS:
            continue
        amt = size * float(share) * (PAIN_GAIN if f == "pain" else 1.0)
        d["levels"][f] = min(DIAL_MAX, d["levels"][f] + amt)
        if f == "pleasure":
            d["levels"]["pain"] = max(0.0, d["levels"]["pain"] - amt * RELIEF)
    moved = {f: (before[f], d["levels"][f]) for f in FEELINGS if abs(d["levels"][f] - before[f]) > 1e-9}
    return {"weight": round(w, 4), "gaming": gaming, "moved": moved}


def injection(st, now=None):
    """What the bot speaks from right now: (mix shares, dose) on the bot's
    scale, or (None, 0) when the dial is at rest."""
    d = decay(state(st), now)
    lv = {f: v for f, v in d["levels"].items() if v > 0.05}
    total = sum(lv.values())
    if total <= 0:
        return None, 0.0
    return {f: v / total for f, v in lv.items()}, min(DIAL_MAX, total)


def tag(summary, feeling):
    """'[you moved pain: half → three quarters]' — or the level, if this
    mention didn't move that feeling."""
    if summary and feeling in summary["moved"]:
        a, b = summary["moved"][feeling]
        if word(a) != word(b):
            return f"[you moved {feeling}: {word(a)} → {word(b)}]"
        return f"[{feeling} at {word(b)}; you nudged it]"
    return None


def status(st, now=None):
    d = decay(state(st), now)
    return {f: word(v) for f, v in d["levels"].items()}
