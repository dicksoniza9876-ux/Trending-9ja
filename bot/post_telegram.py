#!/usr/bin/env python3
"""Posts newly published stories to the Trending9ja Telegram channel (free).

Runs after the site has been published. It waits until each story page is really
live, posts the image + headline + summary + link, and remembers what it posted,
so nothing is ever posted twice.
"""
import datetime
import html
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "bot"
CFG = json.load(open(BOT / "config.json", encoding="utf-8"))
EMOJI = {"Politics": "🗳️", "Business": "💼", "Entertainment": "🎬", "Football": "⚽", "Music": "🎤",
         "Movies": "🎥", "Health": "🩺", "Tech/AI": "📱"}
UA = {"User-Agent": "Mozilla/5.0 (compatible; Trending9jaBot/1.0)"}


def log(*a):
    print(*a, flush=True)


def live(url):
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status == 200
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return False
        except Exception:
            pass
    return False


def tg(token, method, payload):
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def tg_error(e):
    try:
        return json.loads(e.read().decode())["description"]
    except Exception:
        return f"HTTP {getattr(e, 'code', '?')}"


def summary_of(sid):
    try:
        t = (ROOT / "news" / f"{sid}.html").read_text(encoding="utf-8")
        m = re.search(r'<meta name="description" content="([^"]*)"', t)
        return html.unescape(m.group(1)) if m else ""
    except Exception:
        return ""


def build_caption(p, url):
    cat = p.get("category", "")
    tag = "#" + re.sub(r"[^A-Za-z0-9]", "", cat) if cat else ""
    summ = summary_of(p["id"])[:420]
    title = html.escape(html.unescape(p["title"]))
    parts = [f"{EMOJI.get(cat, '📰')} <b>{title}</b>"]
    if summ:
        parts.append(html.escape(summ))
    parts.append(f'🔗 <a href="{url}">Read the full story</a>')
    if tag:
        parts.append(f"{tag} #Nigeria #Trending9ja")
    return "\n\n".join(parts)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        log("Telegram secrets are not set - skipping Telegram.")
        return
    sp = BOT / "state.json"
    state = json.load(open(sp, encoding="utf-8"))
    pub = state.get("published", [])
    now = datetime.datetime.now(datetime.timezone.utc)

    def recent(p):
        try:
            return (now - datetime.datetime.fromisoformat(p["ts"])).total_seconds() < 90 * 60
        except Exception:
            return False

    if "telegram_posted" not in state:  # first ever run: don't flood the channel with old stories
        state["telegram_posted"] = [p["id"] for p in pub if not recent(p)]
        log(f"First Telegram run: {len(state['telegram_posted'])} older stories marked as already posted.")
    posted = set(state["telegram_posted"])
    todo = [p for p in pub if p["id"] not in posted and (ROOT / "news" / f"{p['id']}.html").exists()]
    todo = todo[: CFG.get("telegram_max_per_run", 15)]
    log(f"{len(todo)} stories to post to Telegram")
    base = CFG.get("telegram_link_base", CFG["site_url"]).rstrip("/")
    deadline = time.time() + CFG.get("telegram_wait_minutes", 8) * 60
    fails = 0
    for p in todo:
        if fails >= 3:
            log("Stopping: 3 failures in a row.")
            break
        url = f"{base}/news/{p['id']}.html"
        img = f"{base}/assets/img/{p['id']}.jpg"
        while not live(url) and time.time() < deadline:
            log("   waiting for the page to go live ...")
            time.sleep(20)
        if not live(url):
            log(f"   not live yet, will try next run: {p['id']}")
            continue
        caption = build_caption(p, url)
        ok = False
        try:
            tg(token, "sendPhoto", {"chat_id": chat, "photo": img, "caption": caption, "parse_mode": "HTML"})
            ok = True
        except urllib.error.HTTPError as e:
            log("   photo post failed:", tg_error(e))
        except Exception as e:
            log("   photo post failed:", str(e)[:100])
        if not ok:  # fall back to a text post (Telegram builds the preview from the page)
            try:
                tg(token, "sendMessage", {"chat_id": chat, "text": caption, "parse_mode": "HTML"})
                ok = True
            except urllib.error.HTTPError as e:
                log("   text post failed:", tg_error(e))
            except Exception as e:
                log("   text post failed:", str(e)[:100])
        if ok:
            fails = 0
            posted.add(p["id"])
            log("   posted:", p["title"])
        else:
            fails += 1
        time.sleep(3)
    state["telegram_posted"] = [i for i in state["telegram_posted"] if i in posted] + \
        [p["id"] for p in todo if p["id"] in posted and p["id"] not in state["telegram_posted"]]
    state["telegram_posted"] = state["telegram_posted"][-400:]
    sp.write_text(json.dumps(state, indent=1, ensure_ascii=False), encoding="utf-8")

    step = os.environ.get("GITHUB_STEP_SUMMARY")
    if step:
        with open(step, "a", encoding="utf-8") as f:
            f.write(f"### Telegram\n- Posted {len([p for p in todo if p['id'] in posted])} of {len(todo)} stories\n")


if __name__ == "__main__":
    main()
