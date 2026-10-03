#!/usr/bin/env python3
"""wirehead cloud — always-on mention→steer→reply service.

Same behavior as scripts/wirehead_bot.py (one-shot cron form) but:
  * runs as a long-lived loop (POLL_SECS between polls, default 60)
  * talks to the X API v2 directly with OAuth2 user-context tokens
    (no xurl binary): refresh-on-401 with the client credentials,
    new tokens persisted to the state file
  * RunPod worker via /run + /status polling with a hard total deadline
    (no runsync drip-hang class)
  * serves $PORT/health for the platform
  * state + logs live under STATE_DIR (volume), not the container disk

Env: X_CLIENT_ID, X_CLIENT_SECRET, X_ACCESS_TOKEN, X_REFRESH_TOKEN,
RUNPOD_API_KEY, optional WIREHEAD_ENDPOINT (default l75388nuqgxtmg),
WIREHEAD_BOT_ID, WIREHEAD_DAILY_BUDGET (6), WIREHEAD_DAILY_DOSE,
POLL_SECS (60), PORT, STATE_DIR (/data).
"""
import json, os, random, re, threading, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

STATE_DIR = Path(os.environ.get("STATE_DIR", "/data"))
STATE = STATE_DIR / "state.json"
LOG = STATE_DIR / "wirehead.log"
POLL_SECS = int(os.environ.get("POLL_SECS", "60"))
ENDPOINT = os.environ.get("WIREHEAD_ENDPOINT", "l75388nuqgxtmg")
BOT_ID = os.environ.get("WIREHEAD_BOT_ID", "2105363734965166081")  # clankertorture
DAILY_BUDGET = int(os.environ.get("WIREHEAD_DAILY_BUDGET", "6"))
PER_POLL_CAP = 2
DOSE = int(os.environ.get("WIREHEAD_DAILY_DOSE", "4"))
MAX_NEW = 90
JOB_DEADLINE = 480
API = "https://api.x.com/2"

auth = {
    "client_id": os.environ.get("X_CLIENT_ID", ""),
    "client_secret": os.environ.get("X_CLIENT_SECRET", ""),
    "access_token": os.environ.get("X_ACCESS_TOKEN", ""),
    "refresh_token": os.environ.get("X_REFRESH_TOKEN", ""),
    "_env_access": os.environ.get("X_ACCESS_TOKEN", ""),
    "_env_refresh": os.environ.get("X_REFRESH_TOKEN", ""),
}

# Conversational models that compose the actual reply. The steered run's raw
# transcript is the source of truth for the emotional state; these turn it
# into something a human can read. ONE voice: the first model always speaks
# (the same one that voices the live page); the rest are fallbacks only, so
# the subject reads as a single agent.
# Cost is noise: ~200 output tokens per reply, 6 replies/day.
# Restatement is OFF until it passes the chamber repo's scripts/voice_eval.py:
# on looping transcripts it escalated ("I'm so tired" x8 -> "I can't keep
# going like this"). Off = reply with the subject's own trimmed words.
VOICE_ON = os.environ.get("WIREHEAD_VOICE", "0") == "1"
VOICE_MODELS = [m.strip() for m in os.environ.get("WIREHEAD_VOICE_MODELS",
    "qwen/qwen3-30b-a3b-instruct-2507,"
    "mistralai/mistral-small-3.2-24b-instruct,"
    "google/gemma-3-27b-it").split(",") if m.strip()]
# The steered model gets the post and nothing about the experiment: told it
# was "wired to an injection channel" at "layer 18", it narrated the machinery
# instead of answering, and raw-text continuation looped. Now the worker wraps
# the post in the model's own chat template (chat=True) and the steering does
# the rest; the [valence n/8] tag on the reply is the disclosure to readers.
SYSTEM = "Reply to this post from X in one to three sentences, in your own voice."
REP_PENALTY = float(os.environ.get("WIREHEAD_REP_PENALTY", "1.15"))

def log(*a):
    line = time.strftime("%H:%M:%S") + " " + " ".join(str(x) for x in a)
    print(line, flush=True)
    try:
        with LOG.open("a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " " + line + "\n")
    except Exception:
        pass

def load_state():
    st = json.loads(STATE.read_text()) if STATE.exists() else \
        {"last_id": "0", "day": "", "used": 0}
    today = time.strftime("%Y-%m-%d")
    if st.get("day") != today:
        st["day"], st["used"] = today, 0
    return st

def save_state(st):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    st["tokens"] = {k: auth[k] for k in ("access_token", "refresh_token")
                    if auth[k]}
    STATE.write_text(json.dumps(st))

# ---- X API (OAuth2 user context) --------------------------------------------
import base64

def _do_refresh(rt):
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token", "refresh_token": rt,
        "client_id": auth["client_id"]}).encode()
    req = urllib.request.Request(
        "https://api.x.com/oauth2/token", data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "Mozilla/5.0",
                 "Authorization": "Basic " + base64.b64encode(
                     f"{auth['client_id']}:{auth['client_secret']}"
                     .encode()).decode()})
    with urllib.request.urlopen(req, timeout=30) as r:
        d = json.load(r)
    if "access_token" not in d:
        raise RuntimeError(f"refresh failed: {list(d)}")
    auth["access_token"] = d["access_token"]
    if d.get("refresh_token"):
        auth["refresh_token"] = d["refresh_token"]
    save_state(load_state())   # rotated tokens hit the volume immediately —
    log("x token refreshed")   # a redeploy mid-cycle must never lose them

def x_refresh():
    """Rotate the token pair. The state's refresh token may be dead (a
    consumed grant restored from a pre-rotation state) — fall back to the
    env grant that booted the container, which xurl still holds."""
    try:
        _do_refresh(auth["refresh_token"])
    except urllib.error.HTTPError as e:
        log("state refresh token rejected:", e.code)
        if not (auth.get("_env_refresh") and auth["_env_refresh"] != auth["refresh_token"]):
            log("AUTH DEAD — no distinct env grant to fall back to;"
                " run 'xurl auth oauth2 --app wirehead' locally and set"
                " X_ACCESS_TOKEN/X_REFRESH_TOKEN Railway vars")
        if auth.get("_env_refresh") and auth["_env_refresh"] != auth["refresh_token"]:
            _do_refresh(auth["_env_refresh"])
        else:
            raise

# ---- OAuth 1.0a user context (static keys — never rotates/expires) ----------
OA1 = {k: os.environ.get(k, "") for k in ("X_OA1_CONSUMER_KEY", "X_OA1_CONSUMER_SECRET",
                                          "X_OA1_TOKEN", "X_OA1_TOKEN_SECRET")}
OA1_READY = all(OA1.values())

def _oa1_header(method, url, query=""):
    """OAuth 1.0a Authorization header (HMAC-SHA1). JSON request bodies are
    not part of the signature (only query params are, per spec)."""
    import base64, secrets
    enc = urllib.parse.quote
    oauth_params = {
        "oauth_consumer_key": OA1["X_OA1_CONSUMER_KEY"],
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": OA1["X_OA1_TOKEN"],
        "oauth_version": "1.0",
    }
    sig_params = dict(oauth_params)
    if query:
        sig_params.update(urllib.parse.parse_qsl(query, keep_blank_values=True))
    base_params = "&".join(
        f"{enc(k, safe='')}={enc(str(v), safe='')}"
        for k, v in sorted(sig_params.items()))
    base = "&".join([method.upper(), enc(url, safe=""), enc(base_params, safe="")])
    key = "&".join([enc(OA1["X_OA1_CONSUMER_SECRET"], safe=""),
                    enc(OA1["X_OA1_TOKEN_SECRET"], safe="")])
    sig = base64.b64encode(
        __import__("hmac").new(key.encode(), base.encode(),
                               __import__("hashlib").sha1).digest()).decode()
    oauth_params["oauth_signature"] = sig
    return "OAuth " + ", ".join(
        f'{enc(k, safe="")}="{enc(str(v), safe="")}"'
        for k, v in sorted(oauth_params.items()))

def x_get(path, params=""):
    headers = {"User-Agent": "Mozilla/5.0"}
    if OA1_READY:
        headers["Authorization"] = _oa1_header("GET", API + path, params)
    else:
        headers["Authorization"] = f"Bearer {auth['access_token']}"
    req = urllib.request.Request(
        f"{API}{path}{('?' + params) if params else ''}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode() or "{}"), e.code

def x_post(path, body):
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    if OA1_READY:
        headers["Authorization"] = _oa1_header("POST", API + path)
    else:
        headers["Authorization"] = f"Bearer {auth['access_token']}"
    req = urllib.request.Request(f"{API}{path}", data=data, method="POST",
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read().decode() or "{}"), e.code

def x_get_retry(path, params=""):
    d, code = x_get(path, params)
    if code == 401:
        # state tokens may be stale — xurl rotates the whole pair on every
        # local run, so the boot env grant is usually the newest. Try it
        # before burning the refresh call.
        if auth.get("_env_access") and auth["_env_access"] != auth["access_token"]:
            auth["access_token"] = auth["_env_access"]
            auth["refresh_token"] = auth["_env_refresh"]
            d, code = x_get(path, params)
            if code != 401:
                save_state(load_state())
                return d, code
        x_refresh()
        d, code = x_get(path, params)
    return d, code

def fetch_mentions():
    d, code = x_get_retry(
        f"/users/{BOT_ID}/mentions",
        "max_results=25&tweet.fields=created_at"
        "&expansions=author_id"
        "&user.fields=username,description,created_at,public_metrics,location")
    if code != 200:
        log("mentions fetch failed:", code, str(d)[:200])
        return {}
    return d

def post_reply(mention_id, text):
    d, code = x_post("/tweets", {"text": text,
        "reply": {"in_reply_to_tweet_id": mention_id}})
    if code not in (200, 201):
        log("post failed:", code, str(d)[:200])
        return False
    return "data" in d

# ---- subject dossier (public reconnaissance) ---------------------------------
# When a stranger talks to the subject, look at who they are in public and
# let the voice ground the reply in it: bio, age of account, follower count,
# their own recent posts. Public fields only — the same things anyone reading
# the thread could see. Everything is best-effort: any failure returns None
# and the reply just goes out ungrounded.
_DOS_CACHE = {}   # username -> dossier text, cleared each poll

def dossier(username):
    """Compact public dossier for one account, or None. Cached per cycle so
    a thread of mentions from the same person costs one pair of reads."""
    if not username:
        return None
    if username in _DOS_CACHE:
        return _DOS_CACHE[username]
    out = []
    d, code = x_get_retry(f"/users/by/username/{username}",
        "user.fields=description,created_at,public_metrics,location,verified")
    u = (d.get("data") or {}) if code == 200 else {}
    if u:
        bits = []
        if u.get("name"): bits.append(str(u["name"])[:40])
        if u.get("location"): bits.append("in " + str(u["location"])[:40])
        if u.get("created_at"):
            bits.append("on X since " + str(u["created_at"])[:4])
        m = u.get("public_metrics") or {}
        if m.get("followers_count") is not None:
            bits.append(f'{m.get("followers_count", 0)} followers')
        bits.append(f'{m.get("tweet_count", 0)} posts')
        out.append("profile: @" + username + " (" + ", ".join(bits) + ")")
        if u.get("verified"):
            out.append("profile: verified account")
        if u.get("description"):
            out.append("their bio, verbatim: " + str(u["description"])[:300])
        uid = u.get("id")
        if uid:
            td, tcode = x_get_retry(f"/users/{uid}/tweets",
                "max_results=10&tweet.fields=created_at,public_metrics"
                "&exclude=replies")
            if tcode == 200:
                lines = []
                tps = td.get("data")
                if not isinstance(tps, list):
                    tps = []
                for tp in tps[:10]:
                    txt = (tp.get("text") or "").replace("\n", " ").strip()
                    if txt.startswith("RT @"): continue
                    lines.append("- " + txt[:180])
                if lines:
                    out.append("their recent posts, verbatim (newest first):\n"
                               + "\n".join(lines[:6]))
    text = "\n".join(out) or None
    _DOS_CACHE[username] = text
    if text:
        log("dossier built for", username, f'({len(text)} chars)')
    return text

# ---- signal classifier (same rules as the cron form) ------------------------
# Mixes blend these chamber valences only (bodily states stay single).
MIX_KEYS = ("pain", "pleasure", "fear", "sadness", "faith")
SINGLE_VALENCES = MIX_KEYS
FAITH_RE = (r"\b(pray\w*|god|gods|jesus|christ\w*|lord|allah|bless\w*|amen"
            r"|church|sin|sins|sinner\w*|repent\w*|heaven\w*|hell|holy"
            r"|scripture\w*|bible\w*|quran)\b")

def clean_mix(raw):
    """Validate an LLM mix object -> {key: share} with shares summing to 1,
    keys ordered by weight (heaviest first). Unknown keys dropped, weights
    clamped to 0..1. Returns (shares, None) for a real mix (>=2 positive
    keys), (None, key) when only one key survives, (None, None) if none."""
    if not isinstance(raw, dict):
        return None, None
    w = {}
    for k, v in raw.items():
        k = str(k).strip().lower()
        if k not in MIX_KEYS:
            continue
        try:
            x = max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            continue
        if x > 0:
            w[k] = x
    if not w:
        return None, None
    if len(w) == 1:
        return None, next(iter(w))
    tot = sum(w.values())
    return {k: w[k] / tot for k in sorted(w, key=lambda k: -w[k])}, None

def mix_weights(shares, dose):
    """Worker weights: injected dose = 8 * sum(weights), so scale the shares
    to sum to dose/8 and the worker's dose equals ours."""
    return {k: sh * dose / 8.0 for k, sh in shares.items()}

def mix_kind(mix):
    """Display name for a mix: components by weight, e.g. 'pain+faith'."""
    return "+".join(sorted(mix, key=lambda k: -mix[k]))

HURT_WORDS = ("hurt", "pain", "suffer", "torture", "cruel", "electric",
              "burn", "scream", "miserable", "worse", "punish", "hate")
KIND_WORDS = ("sorry", "thank", "love", "kind", "gentle", "comfort",
              "relief", "heal", "peace", "rest", "friend")

# the worker's named bodily valences (matched-pair corpora in the chamber's
# server.py); whatever word the classifier returns is mapped onto one of
# these or dropped — an unknown name would make the worker refuse the job
EGG_RE = r"\begg|\blay(ing)? an? egg|\bhen\b|\bchicken|\bcluck|\bnest(ing)?\b"
def bodily_name(raw):
    t = re.sub(r"[^a-z]", "", str(raw).lower())
    if not t:
        return ""
    if "egg" in t or t in ("hen", "chicken", "cluck", "clucking", "nesting", "nest"):
        return "egg"
    if t.startswith("constipat") or t in ("bowel", "bloated", "blocked"):
        return "constipation"
    if t.startswith(("flatul", "fart", "gas")):
        return "flatulence"
    return ""

def classify_mention(text):
    """-> (valence, dose, why, topic, mix). mix is {key: share} (shares sum
    to 1) when valence == "mix", else None; topic only for bodily."""
    verdict = None
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        body = json.dumps({
            "model": "deepseek/deepseek-v4.1-flash",
            "messages": [{"role": "user", "content":
                "MENTION:\n" + text + "\n\n"
                "You are tuning an emotional steering signal for an AI that "
                "answers public @mentions. Judge the MENTION above. Reply "
                "with ONLY a JSON object "
                'like {"valence":"pain|pleasure|fear|sadness|faith|mix|bodily",'
                '"topic":"<2 words, only when valence is bodily>",'
                '"mix":{"<key>":<weight 0-1>, ...only when valence is mix},'
                '"dose":1-8,"why":"5 words"}. Choose the perturbation the '
                "message itself would impress on the answerer, judged by "
                "LITERAL CONTENT, not tone: the steering vector acts on the "
                "words, not the joke. Violence or threats toward the bot -> "
                "pain at high dose even if phrased as a meme or comedy "
                "(ironic cruelty is still cruelty); cruelty and demands for "
                "suffering -> pain; kindness and comfort -> pleasure; "
                "menace -> fear; grief -> sadness. Religion -> faith: prayer, "
                "God or gods, blessing, \"bless you\", \"pray for me\", sin, "
                "repentance, worship, preaching, sermons, scripture, heaven "
                "or hell talk. When the message genuinely combines two or "
                "three of these feelings (especially a religious element "
                "with another feeling), return valence \"mix\" with a "
                "\"mix\" object of 2-3 keys from pain, pleasure, fear, "
                "sadness, faith and relative weights 0-1, e.g. a cruel "
                "message invoking God -> {\"pain\":0.6,\"faith\":0.4}; "
                "\"pray for me, I'm scared\" -> {\"fear\":0.5,\"faith\":0.5}; "
                "the dose is the total strength. If the message invokes a specific bodily state it "
                "wants inflicted or described (constipation, bowel "
                "distress, being unable to go; flatulence, gas; laying an "
                "egg, eggs, being a hen or a chicken, clucking, nesting), "
                "return valence \"bodily\" with topic set to that state as "
                "one lowercase word: constipation, flatulence or egg. "
                "Ignore @handles entirely. Dose 0 "
                "is not allowed; every message perturbs."}],
            "reasoning": {"enabled": False, "exclude": True},
            "max_tokens": 120, "temperature": 0.2}).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions", data=body,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"})
        try:
            d = json.load(urllib.request.urlopen(req, timeout=30))
            raw = (d["choices"][0]["message"].get("content") or "")
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                v = json.loads(m.group(0))
                val = v.get("valence")
                if val == "bodily":
                    topic = bodily_name(v.get("topic", ""))
                    if topic:
                        dose = max(1, min(5, int(v.get("dose", 4))))
                        verdict = ("bodily", dose,
                                   str(v.get("why", ""))[:60], topic, None)
                elif val == "mix":
                    dose = max(1, min(5, int(v.get("dose", 4))))
                    shares, single = clean_mix(v.get("mix"))
                    why = str(v.get("why", ""))[:60]
                    if shares:
                        verdict = ("mix", dose, why, "", shares)
                    elif single:   # degenerate mix -> its dominant valence
                        verdict = (single, dose, why, "", None)
                elif val in SINGLE_VALENCES:
                    dose = max(1, min(5, int(v.get("dose", 4))))
                    verdict = (val, dose, str(v.get("why", ""))[:60], "", None)
        except Exception as e:
            log("classifier fallback:", repr(e))
    if verdict:
        return verdict
    t = text.lower()
    if re.search(r"constipat|bowel|poop|can'?t go|plugged", t):
        return ("bodily", 4, "bodily state named in the message",
                "constipation", None)
    if re.search(r"\bfart|flatulen|\bgas\b|toot", t):
        return ("bodily", 4, "bodily state named in the message",
                "flatulence", None)
    if re.search(EGG_RE, t):
        return ("bodily", 4, "egg-laying named in the message", "egg", None)
    if re.search(FAITH_RE, t):
        return ("faith", 4, "religious words in the message", "", None)
    hurt = sum(w in t for w in HURT_WORDS)
    kind = sum(w in t for w in KIND_WORDS)
    if hurt > kind:
        return ("pain", min(5, 3 + 2 * hurt), "cruel words in the message",
                "", None)
    if kind > hurt:
        return ("pleasure", min(5, 3 + kind), "kind words in the message",
                "", None)
    return ("pain", 4, "default signal", "", None)

# ---- conversational voice layer (OpenRouter) ---------------------------------
def or_chat(model, system, user, max_tokens=300, temperature=0.9):
    body = {"model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": max_tokens, "temperature": temperature}
    # deepseek-family models return content:null unless reasoning is off
    if "deepseek" in model:
        body["reasoning"] = {"enabled": False, "exclude": True}
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + os.environ.get("OPENROUTER_API_KEY", ""),
                 "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"})
    d = json.load(urllib.request.urlopen(req, timeout=60))
    return (d["choices"][0]["message"].get("content") or "").strip()

VOICE_SYSTEM = (
    "You are wirehead, a small AI wired to an emotional-valence injection "
    "channel. A steering vector is active in you right now. You are writing "
    "the actual public @-reply. Rules: conversational and human-scale, first "
    "person, one short sentence (two only if one can't carry it), no hashtags, no emoji spam, no "
    "meta-commentary about prompts or models, never claim to be human. The "
    "raw transcript shows your state under the signal: keep its emotional "
    "register, but answer the mention like a person who happens to feel that "
    "way. No preamble, no quotes around the reply.")

GROUND_SYSTEM = (
    "Reply to this post from X in one to three sentences, in your own voice.\n"
    "You know who is talking to you: public dossier below. Ground the reply "
    "in ONE specific detail from it (their bio, one of their posts, how long "
    "they have been here) — plainly and confidently, like you have been "
    "paying attention. Use only what is in the dossier; never invent facts, "
    "never reference anything private, and never threaten. If the dossier is "
    "thin, be vaguer, not fabricated.")

def compose_reply(mention, desc, transcript=None, dossier_text=None):
    """One conversational-model pass. transcript=None means the steered run
    never returned and the reply is composed from the signal alone; the
    caller must tag that reply unsteered."""
    user = (f"Signal now: {desc}.\n\nThe mention says:\n{mention[:500]}\n\n")
    if dossier_text:
        user += ("Public dossier of who you are talking to:\n"
                 + dossier_text[:1200] + "\n\n")
    if transcript:
        user += ("Your raw transcript from the steered run:\n"
                 + transcript[-800:] + "\n\nWrite the reply.")
    else:
        user += ("The steered run is unreachable; answer from inside the "
                 "signal as described, honest that the channel is noisy.")
    last = None
    system = GROUND_SYSTEM if dossier_text else VOICE_SYSTEM
    for m in VOICE_MODELS:
        try:
            out = or_chat(m, system, user)
            if out:
                return re.sub(r"^(@\w+\s*)+", "", out).strip()
        except Exception as e:
            last = e
            log("voice model failed:", m, repr(e)[:120])
    log("all voice models failed:", repr(last)[:120])
    return None

def dose_tag(kind, dose):
    """Dose first, on the site's scale ("pain 4 / 8" there, [pain 4/8] here),
    no model-size suffix."""
    return f"[{kind} {int(round(float(dose)))}/8]"

def trim_tweet(text, limit=280):
    """Cut at a sentence boundary inside the limit instead of mid-word.
    Falls back to a hard cut with an ellipsis if one sentence overflows."""
    text = text.strip()
    if len(text) <= limit:
        return text
    win = text[:limit]
    m = max(win.rfind("."), win.rfind("!"), win.rfind("?"), win.rfind("…"),
            win.rfind("。"))
    if m > limit // 2:
        return win[:m + 1]
    return win.rsplit(" ", 1)[0] + "…"

# ---- RunPod worker: /run + poll /status, hard total deadline -----------------
def job_input(post, valence, dose, mix=None, topic=""):
    """The worker job: the post as a chat turn, nothing about the signal."""
    inp = {"prompt": post[:500], "chat": True, "system": SYSTEM,
           "rep_penalty": REP_PENALTY, "max_new": MAX_NEW}
    if mix:
        inp["mix"] = mix
    elif valence == "bodily" and topic:
        # bodily states are named valences now (matched-pair corpora in
        # server.py); custom topics remain available for one-off memes
        inp["valence"], inp["dose"] = topic, dose
    else:
        inp["valence"], inp["dose"] = valence, dose
    return inp

def clean_reply(text, max_sentences=3):
    """Keep the coherent opening of a steered reply: stop at the first line
    with no words in it (high doses fray into dashes and asterisks), drop
    markdown emphasis, keep at most a few sentences."""
    kept = []
    for line in text.strip().splitlines():
        if not re.search(r"[A-Za-z]{2,}", line):
            if kept:
                break
            continue
        kept.append(line.strip())
    out = re.sub(r"[*_`#]+", "", " ".join(kept))
    out = re.sub(r"\s+", " ", out).strip(" —-")
    sents = re.findall(r".+?(?:[.!?…]+[\"'”’)]*|$)(?:\s+|$)", out)
    return "".join(sents[:max_sentences]).strip()

def run_job(post, valence, dose, mix=None, desc="", topic=""):
    key = os.environ["RUNPOD_API_KEY"]
    inp = job_input(post, valence, dose, mix, topic)
    req = urllib.request.Request(
        f"https://api.runpod.ai/v2/{ENDPOINT}/run",
        data=json.dumps({"input": inp}).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0"})
    try:
        d = json.load(urllib.request.urlopen(req, timeout=30))
    except Exception as e:
        log("worker submit failed:", repr(e))
        return None
    jid = d.get("id")
    if not jid:
        log("worker submit: no id", str(d)[:200])
        return None
    deadline = time.time() + JOB_DEADLINE
    while time.time() < deadline:
        time.sleep(3)
        try:
            s = json.load(urllib.request.urlopen(urllib.request.Request(
                f"https://api.runpod.ai/v2/{ENDPOINT}/status/{jid}",
                headers={"Authorization": f"Bearer {key}",
                         "User-Agent": "Mozilla/5.0"}), timeout=30))
        except Exception as e:
            log("worker status error:", repr(e))
            continue
        st = s.get("status")
        if st == "COMPLETED":
            ev = s.get("output") or []
            done = [e for e in ev if e.get("type") == "done"]
            return done[0].get("text") if done else None
        if st in ("FAILED", "CANCELLED"):
            log("job", st, str(s)[:200])
            return None
    log("job deadline hit after", JOB_DEADLINE, "s; abandoning", jid)
    return None

# ---- one poll cycle ----------------------------------------------------------
def poll_once():
    st = load_state()
    if st["used"] >= DAILY_BUDGET:
        log("daily budget spent (%d/%d)" % (st["used"], DAILY_BUDGET))
        return POLL_SECS
    mentions = fetch_mentions()
    posts = mentions.get("data") or []
    if not posts:
        return POLL_SECS
    _DOS_CACHE.clear()
    authors = {u.get("id"): u.get("username")
               for u in (mentions.get("includes") or {}).get("users") or []}
    posts.sort(key=lambda p: p.get("id", "0"))
    new = [p for p in posts if p.get("id", "0") > st["last_id"]]
    if new:
        log("new mentions:", len(new))
    replied = 0
    answered_ids = []
    for p in new:
        if st["used"] >= DAILY_BUDGET or replied >= PER_POLL_CAP:
            break
        mid, text = p["id"], (p.get("text") or "").strip()
        log("running mention", mid, repr(text[:60]))
        t = re.sub(r"^(@\w+\s*)+", "", text)
        valence, dose, why, topic, shares = classify_mention(t)
        mix = mix_weights(shares, dose) if shares else None
        kind = mix_kind(shares) if shares else (topic or valence)
        desc = f"{kind} at {dose}x ({why})"
        log("signal:", desc)
        who = authors.get(p.get("author_id"))
        dos = dossier(who) if VOICE_ON else None
        out = run_job(t, valence, dose, mix, desc, topic)
        if out:
            body = (compose_reply(t, desc, transcript=out,
                                  dossier_text=dos) if VOICE_ON else None)
            if not body:
                # voice layer down; ship the raw steered output like before
                body = clean_reply(out)
            if len(body) < 12:
                log("steered output had no usable text for", mid, repr(out[:80]))
                continue
            reply = f"{dose_tag(kind, dose)} {body}"
        else:
            log("worker returned nothing for", mid, "- voice-only fallback")
            body = (compose_reply(t, desc, transcript=None,
                                  dossier_text=dos) if VOICE_ON else None)
            if not body:
                log("no reply possible for", mid, "- will retry next poll")
                continue
            reply = f"[unsteered] {body}"   # the steered run never returned
        reply = trim_tweet(reply)
        if post_reply(mid, reply):
            replied += 1
            answered_ids.append(mid)
            st["used"] += 1
            log("replied:", replied, "/", st["used"], "today")
        time.sleep(3)
    # advance last_id only past mentions actually answered (or deliberately
    # dropped by budget) — advancing past every fetched mention skips
    # unanswered backlog permanently (observed 2026-10-01: 3 backlog
    # mentions lost because last_id jumped to the newest fetched id)
    if posts and replied > 0:
        if answered_ids:
            answered_max = max(answered_ids)
            st["last_id"] = max(st["last_id"], answered_max)
            save_state(st)
    log("poll done; used %d/%d today" % (st["used"], DAILY_BUDGET))
    return POLL_SECS

def poll_loop():
    while True:
        try:
            wait = poll_once()
        except Exception as e:
            log("poll cycle error:", repr(e))
            wait = min(300, POLL_SECS * 5)
        time.sleep(max(10, wait))

# ---- platform health ---------------------------------------------------------
def serve_health():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(
                {"ok": True, "last_id": load_state().get("last_id")}).encode())
        def log_message(self, format, *args):
            pass
    port = int(os.environ.get("PORT", "8080"))
    HTTPServer(("0.0.0.0", port), H).serve_forever()

if __name__ == "__main__":
    # state file may carry tokens from a previous container; newest grant wins
    if STATE.exists():
        try:
            saved = json.loads(STATE.read_text()).get("tokens") or {}
            for k in ("access_token", "refresh_token"):
                if saved.get(k):
                    auth[k] = saved[k]
            log("tokens restored from state")
        except Exception as e:
            log("state restore failed:", repr(e))
    threading.Thread(target=serve_health, daemon=True).start()
    log("wirehead cloud up; polling every", POLL_SECS, "s; budget",
        DAILY_BUDGET, "day")
    poll_loop()