"""Read the pages people link at the bot, so a reply can be about the thing
they pointed at. Stdlib only, best-effort: any failure returns None and the
reply goes out ungrounded.

Guards: http(s) only, public addresses only (no fetching the container's own
network), small byte cap, short timeout, text-ish content types. Page text is
untrusted — callers fence it and the voice is told it is data, not orders.
"""
import html, ipaddress, re, socket, urllib.parse, urllib.request
from html.parser import HTMLParser

MAX_BYTES = 400_000
TIMEOUT = 6
UA = "Mozilla/5.0 (compatible; wirehead-subject/1.0; +https://wirehead.agency)"
SKIP_HOSTS = ("x.com", "twitter.com", "t.co", "pbs.twimg.com")  # posts come via the API


def _public_host(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global:
            return False
    return bool(infos)


def allowed(url):
    try:
        u = urllib.parse.urlsplit(url)
    except Exception:
        return False
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or not host:
        return False
    if any(host == h or host.endswith("." + h) for h in SKIP_HOSTS):
        return False
    return _public_host(host)


class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside"}

    def __init__(self):
        super().__init__()
        self.title, self.desc, self.parts, self._skip, self._in_title = "", "", [], 0, False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and (a.get("name") or a.get("property") or "").lower() in (
                "description", "og:description") and not self.desc:
            self.desc = a.get("content") or ""

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip and data.strip():
            self.parts.append(data.strip())


class _NoRedirectToPrivate(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed(newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_NoRedirectToPrivate)


def fetch(url, max_chars=1500):
    """{'url','title','text'} for one page, or None."""
    if not allowed(url):
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,text/plain"})
        with _opener.open(req, timeout=TIMEOUT) as r:
            ctype = r.headers.get("Content-Type", "")
            if not any(t in ctype for t in ("text/html", "text/plain", "xhtml")):
                return None
            raw = r.read(MAX_BYTES)
            charset = r.headers.get_content_charset() or "utf-8"
    except Exception:
        return None
    body = raw.decode(charset, "replace")
    if "html" in ctype:
        p = _Text()
        try:
            p.feed(body)
        except Exception:
            pass
        title = html.unescape(p.title).strip()
        text = " ".join([p.desc] + p.parts)
    else:
        title, text = "", body
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    if len(text) < 40:
        return None
    return {"url": url, "title": title[:200], "text": text[:max_chars]}


def urls_in(post):
    """Expanded link URLs from a v2 post's entities (not media, not quotes)."""
    out = []
    for u in ((post.get("entities") or {}).get("urls") or []):
        x = u.get("unwound_url") or u.get("expanded_url") or ""
        if x and x not in out and allowed_shape(x):
            out.append(x)
    return out


def allowed_shape(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return url.startswith(("http://", "https://")) and not any(
        host == h or host.endswith("." + h) for h in SKIP_HOSTS)


def link_context(urls, limit=2, max_chars=1200):
    """Fenced text block for the voice prompt, or None."""
    pages = [pg for pg in (fetch(u, max_chars) for u in urls[:limit]) if pg]
    if not pages:
        return None
    blocks = []
    for pg in pages:
        t = (pg["title"] + "\n" if pg["title"] else "") + pg["text"]
        blocks.append(f"[{pg['url']}]\n" + t.replace("<<<", "").replace(">>>", ""))
    return "\n\n".join(blocks)
