#!/usr/bin/env python3
"""Trending9ja auto-update bot.

Pulls headlines from the RSS feeds in feeds.json, asks the free Gemini API to
write short ORIGINAL briefs from each source article, then builds the story
pages and updates the homepage, category pages, archive and sitemap.

Sensitive stories (allegations, deaths, crime, Health ...) are saved as drafts
in bot/drafts/ and only published when you approve them.
"""
import argparse
import datetime
import email.utils
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "bot"
CFG = json.load(open(BOT / "config.json", encoding="utf-8"))

CATS = {
    "Politics": "politics.html",
    "Business": "business.html",
    "Entertainment": "entertainment.html",
    "Football": "football.html",
    "Music": "music.html",
    "Movies": "movies.html",
    "Health": "health.html",
    "Tech/AI": "tech-ai.html",
}
REGION_PAGES = {
    "Nigeria": "politics-nigeria.html",
    "USA": "politics-usa.html",
    "Europe": "politics-europe.html",
    "World": "politics-world.html",
}
EMOJI = {"Politics": "🗳️", "Entertainment": "🎬", "Football": "⚽", "Music": "🎤",
         "Movies": "🎥", "Health": "🩺", "Tech/AI": "📱", "Business": "💼"}

IMG_FALLBACK = ("this.onerror=null;this.src='data:image/svg+xml;utf8,<svg xmlns=%22http://www.w3.org/2000/svg%22 "
                "width=%22800%22 height=%22400%22><rect width=%22100%25%22 height=%22100%25%22 fill=%22%23008751%22/>"
                "<text x=%2250%25%22 y=%2250%25%22 fill=%22white%22 font-family=%22sans-serif%22 font-size=%2236%22 "
                "text-anchor=%22middle%22>Trending9ja</text></svg>'")

SYSTEM_PROMPT = """You are a news writer for Trending9ja, a Nigerian news and entertainment blog.
Write an ORIGINAL short news brief from the source article you are given.

Hard rules:
- Use ONLY facts that appear in the source text. Never invent quotes, numbers, names, dates or places.
- Do not copy sentences. Rewrite everything in your own words (never reuse more than 6 words in a row).
- Attribute claims to the source, e.g. "according to Punch". Use words like "alleged" or "claims" for allegations.
- Never state an accusation as fact. Say who alleges it. Do not say anyone is guilty unless a court has said so.
- Neutral, clear Nigerian English. No hype, no opinions.
- Headline: catchy but accurate, at most 90 characters.
- Body: exactly 3 short paragraphs, about 30-55 words each.
- summary: one sentence of at most 170 characters.
- category must be one of: Politics, Business, Entertainment, Football, Music, Movies, Health, Tech/AI.
- Business means the economy, markets, banking, naira and inflation, CBN, oil and gas, companies, trade, investment, jobs and money.
  Elections, parties and government appointments are Politics. A story mainly about money or companies is Business.
- If category is Politics, region must be one of: Nigeria, USA, Europe, World. Otherwise region is null.
- skip=true if the source text is too thin to write from, is an advert, a list/roundup, not news, or graphic.
- sensitive=true if the story involves allegations against named people, deaths, accidents, crime, health claims,
  ethnic or religious tension, or anything unverified or contested. Explain briefly in sensitive_reason.

Reply with JSON only, in this shape:
{"skip": false, "skip_reason": "", "title": "", "summary": "", "category": "", "region": null,
 "paragraphs": ["", "", ""], "sensitive": false, "sensitive_reason": ""}"""


# --------------------------------------------------------------------------- helpers
def log(*a):
    print(*a, flush=True)


def tz_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=CFG["timezone_offset_hours"])))


def read(p):
    return Path(p).read_text(encoding="utf-8")


def write(p, t):
    Path(p).write_text(t, encoding="utf-8")


def esc(t):
    return html.escape(html.unescape(str(t)), quote=True)


def txt(t):
    return html.escape(html.unescape(str(t)), quote=False)


def clean_text(s):
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Trending9jaBot/1.0)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def slugify(title, existing):
    words = re.sub(r"[^a-z0-9 ]", "", title.lower().replace("&", " and ")).split()
    stop = {"the", "a", "an", "of", "to", "in", "on", "for", "and", "as", "at", "by", "is", "after", "over"}
    words = [w for w in words if w not in stop][:6] or ["story"]
    base = "-".join(words)
    slug, n = base, 2
    while (ROOT / "news" / f"{slug}.html").exists() or slug in existing:
        slug = f"{base}-{n}"
        n += 1
    return slug


def outlet_key(name):
    """'Vanguard' and 'Vanguard Business' (or BBC Africa / BBC World) are one outlet, not two sources."""
    parts = re.sub(r"^the\s+", "", name.lower()).split()
    return parts[0] if parts else name.lower()


def tokens(t):
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", t.lower()).split() if len(w) > 3}


def similar(a, b):
    A, B = tokens(a), tokens(b)
    if not A or not B:
        return False
    return len(A & B) / len(A | B) >= 0.55


def age_label(days):
    return "Today" if days <= 0 else "Yesterday" if days == 1 else f"{days} days ago"


# --------------------------------------------------------------------------- state
def load_state():
    p = BOT / "state.json"
    try:
        s = json.load(open(p, encoding="utf-8"))
    except Exception:
        s = {}
    s.setdefault("seen", {})
    s.setdefault("published", [])
    return s


def save_state(s, today):
    cut = (today - datetime.timedelta(days=10)).isoformat()
    s["seen"] = {k: v for k, v in s["seen"].items() if v >= cut}
    s["published"] = [p for p in s["published"] if p["date"] >= cut]
    r = s.get("retries", {})
    s["retries"] = dict(list(r.items())[-200:])
    write(BOT / "state.json", json.dumps(s, indent=1, ensure_ascii=False))


# --------------------------------------------------------------------------- feeds
def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except Exception:
        d = None
    if d is None:
        try:
            d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d


def parse_feed(data):
    root = ET.fromstring(data)
    ln = lambda e: e.tag.split("}")[-1]
    items = []
    for it in root.iter():
        if ln(it) not in ("item", "entry"):
            continue
        d = {"title": "", "link": "", "desc": "", "date": None, "image": ""}
        for c in it:
            n = ln(c)
            u = c.attrib.get("url", "")
            if u and n in ("content", "thumbnail", "enclosure") and not d["image"]:
                kind = (c.attrib.get("type", "") + c.attrib.get("medium", "")).lower()
                if "image" in kind or re.search(r"\.(jpe?g|png|webp)(\?|$)", u, re.I):
                    d["image"] = u
            if n == "title":
                d["title"] = clean_text(c.text)
            elif n == "link":
                d["link"] = (c.text or c.attrib.get("href") or "").strip()
            elif n in ("description", "summary", "encoded", "content"):
                t = clean_text(c.text)
                if len(t) > len(d["desc"]):
                    d["desc"] = t
            elif n in ("pubDate", "published", "updated", "date"):
                d["date"] = d["date"] or parse_date(c.text)
        if d["title"] and d["link"]:
            items.append(d)
    return items


def find_lead_image(raw, page_url):
    """The lead image the source site itself declares for sharing (og:image / twitter:image)."""
    for pat in (r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]*content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']',
                r'<meta[^>]+name=["\']twitter:image["\'][^>]*content=["\']([^"\']+)',
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*name=["\']twitter:image["\']'):
        m = re.search(pat, raw, re.I)
        if m:
            return urllib.parse.urljoin(page_url, html.unescape(m.group(1)).strip())
    return ""


def article_text(url, fallback, limit=3500):
    """Returns (text, lead_image_url)."""
    try:
        raw = http_get(url).decode("utf-8", "ignore")
        image = find_lead_image(raw, url)
        body_raw = re.sub(r"(?is)<(script|style|nav|footer|aside|form|header)[^>]*>.*?</\1>", " ", raw)
        paras = [clean_text(p) for p in re.findall(r"(?is)<p[^>]*>(.*?)</p>", body_raw)]
        paras = [p for p in paras if len(p.split()) >= 8]
        body = "\n".join(paras)
        if len(body) > 300:
            return body[:limit], image
        return (fallback or "")[:limit], image
    except Exception as e:
        log("   (could not fetch article page:", e, ")")
    return (fallback or "")[:limit], ""


def download_image(url, out):
    """Download, check and shrink a source image. Returns True if saved."""
    from PIL import Image
    import io
    try:
        data = http_get(url, timeout=30)
        if len(data) < 15000 or len(data) > 12_000_000:
            return False
        im = Image.open(io.BytesIO(data))
        im.load()
        w, h = im.size
        if w < CFG["min_image_width"] or h < 250 or w / h > 3.2 or w / h < 0.5:
            return False
        im = im.convert("RGB")
        if w > 1200:
            im = im.resize((1200, round(h * 1200 / w)), Image.LANCZOS)
        im.save(out, "JPEG", quality=85)
        return True
    except Exception as e:
        log("   (image download failed:", e, ")")
        return False


def collect_candidates(existing_titles, state, now_utc):
    feeds = json.load(open(BOT / "feeds.json", encoding="utf-8"))["feeds"]
    per_feed = []
    for f in feeds:
        try:
            items = parse_feed(http_get(f["url"]))
        except Exception as e:
            log(f"!! Feed failed: {f['name']} ({e})")
            continue
        fresh = []
        for it in items:
            if it["link"] in state["seen"]:
                continue
            if it["date"] and (now_utc - it["date"]).total_seconds() > CFG["max_age_hours"] * 3600:
                continue
            it["source"], it["hint"] = f["name"], f.get("hint", "")
            it["images_ok"] = f.get("images", True)
            it["outlet"] = outlet_key(f.get("outlet", f["name"]))
            fresh.append(it)
        fresh.sort(key=lambda x: x["date"] or now_utc, reverse=True)
        per_feed.append(fresh[: CFG["per_feed_limit"]])
        log(f"   {f['name']}: {len(fresh)} fresh items")
    # round-robin so one site cannot dominate; count how many outlets carry the same story
    out, titles = [], list(existing_titles)
    for i in range(max((len(x) for x in per_feed), default=0)):
        for lst in per_feed:
            if i < len(lst):
                it = lst[i]
                twin = next((o for o in out if similar(it["title"], o["title"])), None)
                if twin is not None:
                    if it["outlet"] != twin["outlet"]:
                        twin.setdefault("also", {})[it["outlet"]] = it["source"]
                    state["seen"][it["link"]] = tz_now().date().isoformat()
                    continue
                if any(similar(it["title"], t) for t in titles):
                    state["seen"][it["link"]] = tz_now().date().isoformat()
                    continue
                titles.append(it["title"])
                out.append(it)
    return out


# --------------------------------------------------------------------------- Gemini
GEM_BASE = "https://generativelanguage.googleapis.com/v1beta"
DEADLINE = [None]  # set in main(): the run stops asking Gemini when time is up


def out_of_time():
    return DEADLINE[0] is not None and time.time() > DEADLINE[0]


def _gem_call(model, body, key, timeout=None):
    timeout = timeout or CFG.get("gemini_timeout", 60)
    req = urllib.request.Request(
        f"{GEM_BASE}/models/{model}:generateContent", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _http_msg(e):
    try:
        raw = e.read().decode("utf-8", "ignore")
        try:
            raw = json.loads(raw)["error"]["message"]
        except Exception:
            pass
        return re.sub(r"\s+", " ", raw)[:160]
    except Exception:
        return ""


def list_models(key):
    """Models this key can use for generateContent, best Flash models first."""
    req = urllib.request.Request(f"{GEM_BASE}/models?pageSize=200", headers={"x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    names = [m["name"].split("/")[-1] for m in data.get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    bad = ("image", "tts", "live", "audio", "embedding", "robotics", "computer", "native", "learnlm", "gemma", "veo", "imagen")
    flash = [n for n in names if "flash" in n and not any(x in n for x in bad)]

    def ver(n):
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
        return float(m.group(1)) if m else 0.0
    flash.sort(key=lambda n: (("lite" in n), -ver(n), n))
    return names, flash


def gemini(user_prompt):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("Gemini failed - GEMINI_API_KEY is not set")
    configured = [x.strip() for x in (os.environ.get("GEMINI_MODELS") or CFG["gemini_models"]).split(",") if x.strip()]
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"},
    }
    errors, tried = [], []

    def run(models):
        for m in models:
            if m in tried:
                continue
            tried.append(m)
            for attempt in range(2):
                if out_of_time():
                    errors.append("run time limit reached")
                    return None
                try:
                    data = _gem_call(m, body, key)
                    text = data["candidates"][0]["content"]["parts"][0]["text"]
                    text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
                    result = json.loads(text)
                    if m != configured[0]:
                        log(f"   (used model {m})")
                    return result
                except urllib.error.HTTPError as e:
                    errors.append(f"{m}: HTTP {e.code} {_http_msg(e)}")
                    if e.code == 429 and attempt == 0:
                        time.sleep(25)
                        continue
                    break  # next model
                except Exception as e:
                    errors.append(f"{m}: {str(e)[:100]}")
                    time.sleep(3)
        return None

    out = run(configured)
    if out is None:
        try:  # none of the configured names worked - ask Google what this key can use
            _, flash = list_models(key)
            out = run(flash[:4])
        except Exception as e:
            errors.append(f"model list: {str(e)[:100]}")
    if out is None:
        raise RuntimeError("Gemini failed - " + " | ".join(errors))
    return out


def diagnose_gemini():
    """Used by dry runs: shows which models your key can use and whether each one answers."""
    key = os.environ.get("GEMINI_API_KEY")
    lines = ["### Gemini check"]
    if not key:
        return lines + ["- No GEMINI_API_KEY found"]
    configured = [x.strip() for x in (os.environ.get("GEMINI_MODELS") or CFG["gemini_models"]).split(",") if x.strip()]
    try:
        names, flash = list_models(key)
        lines.append(f"- Your key can see {len(names)} models. Flash models: {', '.join(flash[:8]) or 'none found'}")
    except urllib.error.HTTPError as e:
        lines.append(f"- Could not list models: HTTP {e.code} {_http_msg(e)}")
        flash = []
    except Exception as e:
        lines.append(f"- Could not list models: {str(e)[:120]}")
        flash = []
    body = {"contents": [{"role": "user", "parts": [{"text": "Reply with the single word OK"}]}]}
    for m in list(dict.fromkeys(configured + flash[:2]))[:5]:
        try:
            data = _gem_call(m, body, key, timeout=30)
            ans = data["candidates"][0]["content"]["parts"][0]["text"].strip()[:20]
            lines.append(f"- `{m}`: works (replied: {ans})")
        except urllib.error.HTTPError as e:
            lines.append(f"- `{m}`: HTTP {e.code} {_http_msg(e)}")
        except Exception as e:
            lines.append(f"- `{m}`: {str(e)[:120]}")
        time.sleep(2)
    return lines


def write_story(item, body):
    prompt = (f"SOURCE NAME: {item['source']}\nSOURCE HINT: {item['hint']}\n"
              f"SOURCE HEADLINE: {item['title']}\n\nSOURCE TEXT:\n{body}")
    return gemini(prompt)


def validate(d):
    """Return a cleaned story dict or None."""
    if not isinstance(d, dict) or d.get("skip"):
        return None
    cat = d.get("category")
    if cat not in CATS:
        return None
    title = clean_text(d.get("title", ""))[:110]
    summary = clean_text(d.get("summary", ""))[:200]
    paras = [clean_text(p) for p in (d.get("paragraphs") or []) if isinstance(p, str)]
    paras = [p for p in paras if len(p.split()) >= 8][:5]
    if len(title) < 15 or len(summary) < 20 or len(paras) < 2:
        return None
    region = d.get("region") if cat == "Politics" else None
    if cat == "Politics" and region not in REGION_PAGES:
        region = "Nigeria"
    return {"title": title, "summary": summary, "category": cat, "region": region, "paragraphs": paras,
            "sensitive": bool(d.get("sensitive")), "sensitive_reason": clean_text(d.get("sensitive_reason", ""))}


# --------------------------------------------------------------------------- branded image
def make_card(title, category, out):
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1200, 630
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    c1, c2 = (0, 102, 64), (0, 168, 107)
    for x in range(W):
        t = x / W
        d.line([(x, 0), (x, H)], fill=tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3)))

    def font(sz):
        for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"):
            if os.path.exists(p):
                return ImageFont.truetype(p, sz)
        return ImageFont.load_default()

    f_title, f_chip, f_brand = font(66), font(30), font(34)
    # category chip
    label = category.upper()
    w = d.textlength(label, font=f_chip)
    d.rounded_rectangle([60, 55, 60 + w + 44, 55 + 56], radius=12, fill=(255, 255, 255))
    d.text((82, 61), label, font=f_chip, fill=(0, 102, 64))
    # wrapped headline
    words, lines, cur = title.split(), [], ""
    for wd in words:
        trial = (cur + " " + wd).strip()
        if d.textlength(trial, font=f_title) <= W - 120:
            cur = trial
        else:
            lines.append(cur)
            cur = wd
    lines.append(cur)
    if len(lines) > 5:
        lines = lines[:5]
        lines[-1] = lines[-1].rstrip(" ,.;:") + "..."
    y = 150
    for ln_ in lines:
        d.text((60, y), ln_, font=f_title, fill=(255, 255, 255))
        y += 80
    # brand footer
    d.rectangle([0, H - 90, W, H], fill=(0, 70, 44))
    d.text((60, H - 66), "TRENDING9JA", font=f_brand, fill=(255, 255, 255))
    logo = ROOT / "assets" / "logo.jpg"
    if logo.exists():
        try:
            lg = Image.open(logo).convert("RGB").resize((60, 60))
            img.paste(lg, (W - 120, H - 75))
        except Exception:
            pass
    img.save(out, "JPEG", quality=88)


# --------------------------------------------------------------------------- cards and pages
def img_tag(prefix, sid, alt):
    return (f'<img src="{prefix}assets/img/{sid}.jpg" alt="{esc(alt)}" loading="lazy" '
            f'referrerpolicy="no-referrer" onerror="{IMG_FALLBACK}">')


def card_html(s, today, prefix=""):
    d = datetime.date.fromisoformat(s["date"])
    days = (today - d).days
    chip = f'<span class="region-chip">{s["region"]}</span>' if s.get("region") else ""
    badge = '<span class="new-badge">NEW</span> ' if days <= 3 else ""
    nice = f"{d.strftime('%B')} {d.day}, {d.year}"
    return (f'<article class="post-card" data-date="{s["date"]}"><a class="card-link" href="{prefix}news/{s["id"]}.html" '
            f'aria-label="{esc(s["title"])}"></a>\n  {img_tag(prefix, s["id"], s["title"])}\n'
            f'  <div class="post-content"><span class="category-tag">{s["category"]}</span>{chip}\n'
            f'  <h3>{txt(s["title"])}</h3><p>{txt(s["summary"])}</p>\n'
            f'  <div class="card-meta">{badge}<time datetime="{s["date"]}">{nice}</time> · {age_label(days)}</div>'
            f'</div></article>')


def add_card(page, s, today, marker='<div class="posts-grid">', count=True):
    p = ROOT / page
    if not p.exists():
        return
    t = read(p)
    i = t.find(marker)
    if i < 0:
        log("   !! no card grid in", page)
        return
    j = i + len(marker)
    t = t[:j] + card_html(s, today) + t[j:]
    t = re.sub(r'<p class="empty-note[^>]*>.*?</p>', "", t, count=1, flags=re.S)
    if count:
        t = re.sub(r"(\()(\d+)( stories\))", lambda m: m.group(1) + str(int(m.group(2)) + 1) + m.group(3), t, count=1)
    write(p, t)


def bump_home(cat):
    p = ROOT / "index.html"
    t = read(p)
    t = re.sub(r"(<h3>" + re.escape(cat) + r"</h3><small>)(\d+)( stories</small>)",
               lambda m: m.group(1) + str(int(m.group(2)) + 1) + m.group(3), t)
    write(p, t)


def parse_cards(page, limit, exclude=None):
    t = read(ROOT / page)
    cards = re.findall(r'<article class="post-card" data-date="[^"]+">.*?</article>', t, re.S)
    out = []
    for c in cards:
        if exclude and f"news/{exclude}.html" in c:
            continue
        out.append(c)
        if len(out) >= limit:
            break
    return out


def trend_box(sid, today):
    items = []
    for c in parse_cards("archive.html", 5, exclude=sid):
        href = re.search(r'href="news/([^"]+)\.html"', c).group(1)
        cat = re.search(r'class="category-tag">([^<]+)', c).group(1)
        title = re.search(r"<h3>(.*?)</h3>", c, re.S).group(1)
        dt = datetime.date.fromisoformat(re.search(r'data-date="([^"]+)"', c).group(1))
        items.append(f'<li><a href="../news/{href}.html"><span class="mini-tag">{cat}</span>{title}'
                     f'<small>{age_label((today - dt).days)}</small></a></li>')
    return f'<div class="trend-box"><h3>Latest</h3><ul>{"".join(items)}</ul></div>'


def related_section(s):
    cards = []
    for c in parse_cards(CATS[s["category"]], 4, exclude=s["id"]):
        cards.append(c.replace('href="news/', 'href="../news/').replace('src="assets/', 'src="../assets/'))
    cards = cards[:3]
    return ('<section class="related-wrap"><section class="related"><h2>Related stories</h2>'
            '<div class="posts-grid">' + "".join(cards) + "</div></section></section>")


def build_story_page(s, today):
    t = read(BOT / "story_template.html")
    pg = CATS[s["category"]]
    base = CFG["canonical_base"]
    url = f"{base}/news/{s['id']}.html"
    sub = lambda pat, rep, text, **kw: re.sub(pat, lambda m: rep, text, count=1, **kw)
    t = sub(r"<title>.*?</title>", f"<title>{txt(s['title'])} | Trending9ja</title>", t, flags=re.S)
    t = sub(r'<meta name="description" content="[^"]*">', f'<meta name="description" content="{esc(s["summary"])}">', t)
    t = sub(r'<link rel="canonical" href="[^"]*">', f'<link rel="canonical" href="{url}">', t)
    t = sub(r'<meta property="og:title" content="[^"]*">', f'<meta property="og:title" content="{esc(s["title"])} | Trending9ja">', t)
    t = sub(r'<meta property="og:description" content="[^"]*">', f'<meta property="og:description" content="{esc(s["summary"])}">', t)
    t = sub(r'<meta property="og:image" content="[^"]*">', f'<meta property="og:image" content="{base}/assets/img/{s["id"]}.jpg">', t)
    t = sub(r'<meta property="og:url" content="[^"]*">', f'<meta property="og:url" content="{url}">', t)
    t = t.replace(" class=active", "")
    t = t.replace(f'<a href="../{pg}">', f'<a href="../{pg}" class=active>', 1)
    t = sub(r'<div class="article-layout" data-date="[^"]*">', f'<div class="article-layout" data-date="{s["date"]}">', t)
    t = sub(r'<nav class="crumbs">.*?</nav>',
            f'<nav class="crumbs"><a href="../index.html">Home</a> › <a href="../{pg}">{s["category"]}</a></nav>', t, flags=re.S)
    chip = f'<span class="region-chip">{s["region"]}</span>' if s.get("region") else ""
    t = sub(r'<span class="category-tag">[^<]*</span>(<span class="region-chip">[^<]*</span>)?\n<h1>.*?</h1>',
            f'<span class="category-tag">{s["category"]}</span>{chip}\n<h1>{txt(s["title"])}</h1>', t, flags=re.S)
    d = datetime.date.fromisoformat(s["date"])
    nice = f"{d.strftime('%B')} {d.day}, {d.year}"
    t = sub(r'<p class="meta">.*?</p>',
            f'<p class="meta">By Trending9ja Staff • <time datetime="{s["date"]}">{nice}</time> • {s["read"]} min read</p>',
            t, flags=re.S)
    paras = "".join(f"<p>{txt(p)}</p>" for p in s["paragraphs"])
    credit = f'<p class="credit">{txt(s["credit"])}</p>' if s.get("credit") else ""
    block = img_tag("../", s["id"], s["title"]) + credit + paras
    t = re.sub(r'(<p class="meta">.*?</p>\n).*?(\n<div class="ad-slot" data-slot="article"></div>)',
               lambda m: m.group(1) + block + m.group(2), t, count=1, flags=re.S)
    extra = "".join(f" · {txt(n)}" for n in s.get("also_sources", []))
    src = (f'<strong>Sources:</strong> <a href="{esc(s["source_url"])}" target="_blank" rel="noopener">'
           f'{txt(s["source_name"])}</a>{extra}')
    t = re.sub(r'<p class="sources">.*?</p>', lambda m: f'<p class="sources">{src}</p>', t, count=1, flags=re.S)
    t = re.sub(r'<a class="back-btn" href="[^"]*">[^<]*</a>',
               lambda m: f'<a class="back-btn" href="../{pg}">← More {s["category"]} stories</a>', t, count=1)
    t = re.sub(r'<div class="trend-box">.*?</ul></div>', lambda m: trend_box(s["id"], today), t, count=1, flags=re.S)
    t = re.sub(r'<section class="related-wrap">.*?</section></section>', lambda m: related_section(s), t, count=1, flags=re.S)
    write(ROOT / "news" / f"{s['id']}.html", t)


def add_sitemap(sid):
    p = ROOT / "sitemap.xml"
    if not p.exists():
        return
    t = read(p)
    m = re.search(r"<loc>(https?://[^/<]+)", t)
    base = m.group(1) if m else CFG["canonical_base"]
    t = t.replace("</urlset>", f"<url><loc>{base}/news/{sid}.html</loc></url></urlset>")
    write(p, t)


CARDMETA = re.compile(r'<div class="card-meta">(?:<span class="new-badge">NEW</span> )?'
                      r'<time datetime="(\d{4}-\d{2}-\d{2})">([^<]*)</time> · [^<]*</div>')


def refresh_labels(today):
    """Keep 'Today / Yesterday / N days ago' and the NEW badge correct on listing pages."""
    def rep(m):
        days = (today - datetime.date.fromisoformat(m.group(1))).days
        badge = '<span class="new-badge">NEW</span> ' if days <= 3 else ""
        return (f'<div class="card-meta">{badge}<time datetime="{m.group(1)}">{m.group(2)}</time> · '
                f'{age_label(days)}</div>')
    for p in ROOT.glob("*.html"):
        t = read(p)
        t2 = CARDMETA.sub(rep, t)
        if t2 != t:
            write(p, t2)


def publish(s, today):
    """s needs: id,title,summary,category,region,paragraphs,source_name,source_url,date"""
    s["read"] = max(1, round(sum(len(p.split()) for p in s["paragraphs"]) / 200))
    (ROOT / "assets" / "img").mkdir(parents=True, exist_ok=True)
    out = ROOT / "assets" / "img" / f"{s['id']}.jpg"
    s["credit"] = ""
    if CFG.get("use_source_images", True) and s.get("image_url") and download_image(s["image_url"], out):
        s["credit"] = f"Photo: {s['source_name']}"
    else:
        make_card(s["title"], s["category"], out)
    add_card("index.html", s, today, '<div class="posts-main"><div class="posts-grid">', count=False)
    # index has no "(N stories)" counter, but its category tiles do
    bump_home(s["category"])
    add_card(CATS[s["category"]], s, today)
    if s["category"] == "Politics" and s.get("region"):
        add_card(REGION_PAGES[s["region"]], s, today)
    add_card("archive.html", s, today)
    build_story_page(s, today)
    add_sitemap(s["id"])



# --------------------------------------------------------------------------- one-time: Business category
BIZ_DESC = "Nigerian economy, markets, banking, oil and gas, companies and business news."
BIZ_FEEDS = [
    {"name": "Nairametrics", "url": "https://nairametrics.com/feed/", "hint": "Business: Nigerian economy and markets"},
    {"name": "BusinessDay", "url": "https://businessday.ng/feed/", "hint": "Business: Nigerian business news"},
    {"name": "Vanguard Business", "url": "https://www.vanguardngr.com/category/business/feed/", "hint": "Business news"},
    {"name": "BBC Business", "url": "https://feeds.bbci.co.uk/news/business/rss.xml", "hint": "Business: world economy and companies"},
]


def ensure_business_category(dry=False):
    """Adds the Business category to the live site once (safe to run again)."""
    if (ROOT / "business.html").exists() and "business.html" in read(ROOT / "index.html"):
        return False
    if dry:
        log("Business category is not on the site yet - it will be added on the next real run.")
        return False
    log("Adding the Business category ...")
    # 1) the category page, copied from the Tech/AI page
    if not (ROOT / "business.html").exists():
        t = read(ROOT / "tech-ai.html")
        h = t.index("</head>")
        head = t[:h].replace("tech-ai.html", "business.html").replace("Tech/AI News", "Business News")
        head = head.replace("Artificial intelligence, digital innovation and tech developments in Nigeria.", BIZ_DESC)
        body = t[h:]
        body = re.sub(r'<article class="post-card".*?</article>', "", body, flags=re.S)
        body = re.sub(r'<h1 class="sec-title">[^<]*</h1><p class="feed-sub">.*?</p>',
                      f'<h1 class="sec-title">Business</h1><p class="feed-sub">{BIZ_DESC} (0 stories)</p>'
                      '\n<p class="empty-note feed-sub">The first Business stories are being added - check back shortly.</p>',
                      body, count=1, flags=re.S)
        write(ROOT / "business.html", head + body)
    # 2) menu link (after Politics) on every page
    nav = re.compile(r'(<li><a href="((?:\.\./)?)politics\.html"(?: class=active)?>Politics</a></li>)')
    for p in list(ROOT.glob("*.html")) + list((ROOT / "news").glob("*.html")):
        t = read(p)
        if "Business</a></li>" in t:
            continue
        t2 = nav.sub(lambda m: m.group(1) + f'<li><a href="{m.group(2)}business.html">Business</a></li>', t, count=1)
        if t2 != t:
            write(p, t2)
    # Business page: highlight its own menu item instead of Tech/AI
    b = read(ROOT / "business.html")
    b = b.replace(" class=active", "").replace('<a href="business.html">Business</a>', '<a href="business.html" class=active>Business</a>', 1)
    write(ROOT / "business.html", b)
    # 3) homepage tile and the "By topic" lists
    tile = re.compile(r'(<a class="category-card" href="politics\.html">.*?</a>)', re.S)
    topic = re.compile(r'(<li><a href="politics\.html">Politics<small>\d+ stories</small></a></li>)')
    for p in ROOT.glob("*.html"):
        t = read(p)
        t2 = t
        if 'href="business.html"><i class' not in t2:
            t2 = tile.sub(lambda m: m.group(1) + '<a class="category-card" href="business.html"><i class="fas fa-chart-line"></i>'
                          '<h3>Business</h3><small>0 stories</small></a>', t2, count=1)
        if ">Business<small>" not in t2:
            t2 = topic.sub(lambda m: m.group(1) + '<li><a href="business.html">Business<small>0 stories</small></a></li>', t2)
        if t2 != t:
            write(p, t2)
    # 4) sitemap
    sm = ROOT / "sitemap.xml"
    if sm.exists() and "business.html" not in read(sm):
        t = read(sm)
        m = re.search(r"<loc>(https?://[^/<]+)", t)
        base = m.group(1) if m else CFG["canonical_base"]
        write(sm, t.replace("</urlset>", f"<url><loc>{base}/business.html</loc></url></urlset>"))
    # 5) business news feeds
    fp = BOT / "feeds.json"
    data = json.load(open(fp, encoding="utf-8"))
    have = {f["url"] for f in data["feeds"]}
    added = [f for f in BIZ_FEEDS if f["url"] not in have]
    if added:
        data["feeds"] += added
        write(fp, json.dumps(data, indent=2, ensure_ascii=False))
    return True


# --------------------------------------------------------------------------- drafts, gist, summary
def save_draft(s, reason):
    s = dict(s, reason=reason, created=tz_now().date().isoformat())
    write(BOT / "drafts" / f"{s['id']}.json", json.dumps(s, indent=1, ensure_ascii=False))


def expire_drafts(today):
    for p in (BOT / "drafts").glob("*.json"):
        try:
            c = datetime.date.fromisoformat(json.load(open(p, encoding="utf-8"))["created"])
        except Exception:
            continue
        if (today - c).days > CFG["draft_expiry_days"]:
            p.unlink()


def write_gist(state, today):
    items = [p for p in state["published"] if p["date"] == today.isoformat()][-CFG["gist_size"]:]
    if not items:
        return ""
    base = CFG["site_url"]
    lines = ["🔥 GIST OF THE DAY", ""]
    for p in reversed(items):
        lines += [f"{EMOJI.get(p['category'], '📰')} {p['title']}", f"{base}/news/{p['id']}.html", ""]
    lines += ["👉 Visit *Trending9ja* to keep up with the latest trends:", base + "/"]
    out = "\n".join(lines)
    write(BOT / "gist.txt", out)
    return out


def step_summary(lines):
    text = "\n".join(lines)
    log(text)
    p = os.environ.get("GITHUB_STEP_SUMMARY")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            f.write(text + "\n")


# --------------------------------------------------------------------------- main
def existing_titles():
    t = read(ROOT / "archive.html") if (ROOT / "archive.html").exists() else ""
    return [html.unescape(x) for x in re.findall(r'aria-label="([^"]+)"', t)]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--approve", default="")
    ap.add_argument("--reject", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    if not a.approve and not a.reject and not os.environ.get("GEMINI_API_KEY"):
        sys.exit("STOP: the GEMINI_API_KEY secret is missing. Add it in Settings > Secrets and variables > Actions.")

    (BOT / "drafts").mkdir(parents=True, exist_ok=True)
    DEADLINE[0] = time.time() + CFG.get("max_run_minutes", 8) * 60
    biz_added = ensure_business_category(a.dry_run)
    now = tz_now()
    today = now.date()
    state = load_state()
    summary = [f"## Auto update - {now.strftime('%d %b %Y %H:%M')} (WAT)"]
    published_now, drafted, skipped = [], [], []
    if biz_added:
        summary.append("- Added the **Business** category to the site (menu, page, homepage tile, sitemap, news feeds).")
    if a.dry_run:
        summary += diagnose_gemini()

    # 1) drafts you rejected / approved
    for sid in [x.strip() for x in a.reject.split(",") if x.strip()]:
        p = BOT / "drafts" / f"{sid}.json"
        if p.exists():
            p.unlink()
            summary.append(f"- Rejected draft `{sid}`")
    for sid in [x.strip() for x in a.approve.split(",") if x.strip()]:
        p = BOT / "drafts" / f"{sid}.json"
        if not p.exists():
            summary.append(f"- Draft `{sid}` not found (expired or already handled)")
            continue
        s = json.load(open(p, encoding="utf-8"))
        s["date"] = today.isoformat()
        if not a.dry_run:
            publish(s, today)
            p.unlink()
            state["published"].append({"id": s["id"], "date": s["date"], "title": s["title"], "category": s["category"]})
        published_now.append(s)

    # 2) fresh stories
    done_today = len([p for p in state["published"] if p["date"] == today.isoformat()])
    room_day = max(0, CFG["max_per_day"] - done_today)
    pending = len(list((BOT / "drafts").glob("*.json")))
    if room_day == 0:
        summary.append(f"- Daily limit of {CFG['max_per_day']} reached - no new stories this run.")
    else:
        log("Reading feeds ...")
        cands = collect_candidates(existing_titles(), state, datetime.datetime.now(datetime.timezone.utc))
        log(f"{len(cands)} candidate stories after de-duplication")
        made, tried, fails = 0, 0, 0
        for it in cands:
            if made >= min(CFG["max_per_run"], room_day) or tried >= CFG["max_per_run"] * 3:
                break
            if out_of_time():
                summary.append("- Stopped early: the run's time limit was reached (will continue next run).")
                break
            if fails >= 3:
                summary.append("- Stopped early: 3 stories in a row failed.")
                break
            tried += 1
            log(f"-> {it['source']}: {it['title']}")
            state["seen"][it["link"]] = today.isoformat()
            try:
                body, page_img = article_text(it["link"], it["desc"])
                if len(body.split()) < 40:
                    skipped.append((it["title"], "source text too short"))
                    continue
                s = validate(write_story(it, body))
            except Exception as e:
                log("   !! failed:", e)
                fails += 1
                skipped.append((it["title"], str(e)[:600]))
                if "Gemini failed" in str(e):
                    break  # quota or key problem - stop, try again next run
                continue
            time.sleep(CFG["seconds_between_calls"])
            fails = 0
            if not s:
                skipped.append((it["title"], "Gemini skipped or output invalid"))
                continue
            s.update(id=slugify(s["title"], {x["id"] for x in published_now}),
                     source_name=it["source"], source_url=it["link"], date=today.isoformat(),
                     image_url=(page_img or it.get("image", "")) if it.get("images_ok", True) else "")
            s["also_sources"] = sorted(it.get("also", {}).values())
            rule = CFG.get("sensitive_rule", "confirm")
            if s["sensitive"] and rule == "confirm" and not s["also_sources"]:
                n = state.setdefault("retries", {}).get(it["link"], 0) + 1
                state["retries"][it["link"]] = n
                if n < 3:
                    state["seen"].pop(it["link"], None)  # look at it again next run
                skipped.append((s["title"], f"sensitive story with only one source so far ({n}/3 checks) - "
                                            f"published only when another outlet carries it"))
                continue
            review = s["category"] in CFG["review_categories"] or (s["sensitive"] and rule == "hold")
            if review:
                if pending >= CFG["max_pending_drafts"]:
                    skipped.append((s["title"], "too many drafts waiting"))
                    continue
                if not a.dry_run:
                    save_draft(s, s["sensitive_reason"] or f"{s['category']} stories need approval")
                pending += 1
                drafted.append(s)
                continue
            if not a.dry_run:
                publish(s, today)
                state["published"].append({"id": s["id"], "date": s["date"], "title": s["title"], "category": s["category"]})
            published_now.append(s)
            made += 1

    # 3) housekeeping
    if not a.dry_run:
        expire_drafts(today)
        refresh_labels(today)
        write_gist(state, today)
        save_state(state, today)

    tag = " (dry run - nothing saved)" if a.dry_run else ""
    summary.append(f"### Published: {len(published_now)}{tag}")
    summary += [f"- [{s['category']}] {s['title']}  (`{s['id']}`)"
                + (f" - sensitive, also carried by {', '.join(s['also_sources'])}" if s.get("sensitive") and s.get("also_sources") else "")
                for s in published_now]
    if drafted:
        summary.append(f"### Waiting for your approval: {len(drafted)}")
        summary += [f"- `{s['id']}` - {s['title']}  _(why: {s['reason'] if 'reason' in s else s['sensitive_reason']})_" for s in drafted]
        summary.append("To publish: Actions > Auto update site > Run workflow > put the ids in **approve_ids**.")
    if skipped:
        summary.append(f"### Skipped: {len(skipped)}")
        summary += [f"- {t} - {r}" for t, r in skipped[:15]]
    all_pending = sorted((BOT / "drafts").glob("*.json"))
    if all_pending:
        summary.append(f"### All drafts waiting ({len(all_pending)})")
        for p in all_pending:
            d = json.load(open(p, encoding="utf-8"))
            summary.append(f"- `{d['id']}` - {d['title']}")
    gist = (BOT / "gist.txt")
    if published_now and gist.exists() and not a.dry_run:
        summary += ["### Gist of the Day (copy for WhatsApp)", "```", read(gist), "```"]
    step_summary(summary)


if __name__ == "__main__":
    main()
