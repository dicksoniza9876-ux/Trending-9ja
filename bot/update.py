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
         "Movies": "🎥", "Health": "🩺", "Tech/AI": "📱"}

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
- Neutral, clear Nigerian English. No hype, no opinions.
- Headline: catchy but accurate, at most 90 characters.
- Body: exactly 3 short paragraphs, about 30-55 words each.
- summary: one sentence of at most 170 characters.
- category must be one of: Politics, Entertainment, Football, Music, Movies, Health, Tech/AI.
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
            fresh.append(it)
        fresh.sort(key=lambda x: x["date"] or now_utc, reverse=True)
        per_feed.append(fresh[: CFG["per_feed_limit"]])
        log(f"   {f['name']}: {len(fresh)} fresh items")
    # round-robin so one site cannot dominate
    out, titles = [], list(existing_titles)
    for i in range(max((len(x) for x in per_feed), default=0)):
        for lst in per_feed:
            if i < len(lst):
                it = lst[i]
                if any(similar(it["title"], t) for t in titles):
                    state["seen"][it["link"]] = tz_now().date().isoformat()
                    continue
                titles.append(it["title"])
                out.append(it)
    return out


# --------------------------------------------------------------------------- Gemini
def gemini(user_prompt):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("Gemini failed - GEMINI_API_KEY is not set")
    models = (os.environ.get("GEMINI_MODELS") or CFG["gemini_models"]).split(",")
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"},
    }
    last = "no model tried"
    for m in [x.strip() for x in models if x.strip()]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    url, data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json", "x-goog-api-key": key})
                with urllib.request.urlopen(req, timeout=90) as r:
                    data = json.load(r)
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
                return json.loads(text)
            except urllib.error.HTTPError as e:
                last = f"{m}: HTTP {e.code}"
                if e.code == 429:
                    time.sleep(20 * (attempt + 1))
                    continue
                if e.code in (400, 403, 404):
                    break  # try the next model
                time.sleep(5)
            except Exception as e:  # bad JSON, timeout ...
                last = f"{m}: {e}"
                time.sleep(5)
    raise RuntimeError("Gemini failed - " + last)


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
    src = (f'<strong>Sources:</strong> <a href="{esc(s["source_url"])}" target="_blank" rel="noopener">'
           f'{txt(s["source_name"])}</a>')
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
    now = tz_now()
    today = now.date()
    state = load_state()
    summary = [f"## Auto update - {now.strftime('%d %b %Y %H:%M')} (WAT)"]
    published_now, drafted, skipped = [], [], []

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
        made, tried = 0, 0
        for it in cands:
            if made >= min(CFG["max_per_run"], room_day) or tried >= CFG["max_per_run"] * 3:
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
                skipped.append((it["title"], str(e)[:120]))
                if "Gemini failed" in str(e):
                    break  # quota or key problem - stop, try again next run
                continue
            time.sleep(CFG["seconds_between_calls"])
            if not s:
                skipped.append((it["title"], "Gemini skipped or output invalid"))
                continue
            s.update(id=slugify(s["title"], {x["id"] for x in published_now}),
                     source_name=it["source"], source_url=it["link"], date=today.isoformat(),
                     image_url=(page_img or it.get("image", "")) if it.get("images_ok", True) else "")
            review = s["sensitive"] or s["category"] in CFG["review_categories"]
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
    summary += [f"- [{s['category']}] {s['title']}  (`{s['id']}`)" for s in published_now]
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
