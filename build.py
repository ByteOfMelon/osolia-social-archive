#!/usr/bin/env python3
"""Build a static, read-only archive of social.osolia.xyz.

Input : data/archive-export.json  (made by tools/export_archive.rb on the old server)
        media/                    (public/system without cache/)
Output: docs/                     (served by GitHub Pages from main:/docs; custom domain via CNAME)

Only stdlib. Run:  python build.py
"""
import html
import json
import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "archive-export.json"
MEDIA = ROOT / "media"
OUT = ROOT / "docs"  # GitHub Pages serves main:/docs
DOMAIN = "social.osolia.xyz"
ORIGIN = f"https://{DOMAIN}"

d = json.loads(DATA.read_text(encoding="utf-8"))
INSTANCE = d["instance"]
ACCOUNTS = d["accounts"]
STATUSES = d["statuses"]
by_id = {s["id"]: s for s in STATUSES}
acct_by_id = {a["id"]: a for a in ACCOUNTS}
acct_by_name = {a["username"].lower(): a for a in ACCOUNTS}

originals = [s for s in STATUSES if not s["reblog"]]
first = min(s["created_at"] for s in STATUSES)[:10]
last = max(s["created_at"] for s in STATUSES)[:10]

children = defaultdict(list)
for s in originals:
    if s["in_reply_to_id"] in by_id:
        children[s["in_reply_to_id"]].append(s)

copied = set()
missing_media = set()

# ---------------------------------------------------------------- helpers
e = html.escape


def parse_dt(v):
    v = v.replace(" UTC", "+00:00").replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return datetime.strptime(v[:19], "%Y-%m-%d %H:%M:%S")


def fmt_date(v, with_time=True):
    dt = parse_dt(v)
    s = f"{dt:%b} {dt.day}, {dt.year}"
    return s + (dt.strftime(" · %H:%M UTC") if with_time else "")


def local_media(url):
    """Map an absolute media URL to a site path, copying the file. None if we don't have it."""
    if not url:
        return None
    if url.startswith(ORIGIN + "/system/"):
        rel = url[len(ORIGIN + "/system/"):]
    elif url.startswith("/system/"):
        rel = url[len("/system/"):]
    else:
        return None  # remote media: not archived
    src = MEDIA / rel
    if not src.is_file():
        missing_media.add(rel)
        return None
    if rel not in copied:
        dst = OUT / "system" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.add(rel)
    return "/system/" + rel


def is_local(obj_url):
    return bool(obj_url) and obj_url.startswith(ORIGIN + "/")


def rewrite_links(h):
    # local absolute links -> site-relative
    h = h.replace(f'href="{ORIGIN}/', 'href="/')
    return h


def emojify(text, emojis, already_html=True):
    if not text:
        return ""
    out = text if already_html else e(text)
    for em in emojis or []:
        src = local_media(em.get("url")) or local_media(em.get("static_url"))
        code = em["shortcode"]
        if src:
            img = f'<img class="emoji" src="{src}" alt=":{e(code)}:" title=":{e(code)}:">'
        else:
            img = f":{e(code)}:"
        out = out.replace(f":{code}:", img)
    return out


def display_name(a):
    name = a.get("display_name") or a["username"]
    return emojify(name, a.get("emojis"), already_html=False)


def acct_handle(a):
    acct = a.get("acct") or a["username"]
    return "@" + (acct if "@" in acct else f"{acct}@{DOMAIN}")


def profile_href(a):
    if is_local(a.get("url")) and a["username"].lower() in acct_by_name:
        return f"/@{a['username']}/"
    return a.get("url") or "#"


def status_href(s):
    if s["id"] in by_id and not s["reblog"]:
        return f"/@{s['account']['username']}/{s['id']}/"
    return s.get("url") or s.get("uri")


def initials(a):
    n = (a.get("display_name") or a.get("username") or "?").strip()
    return e(n[:1].upper())


def avatar(a, size="md"):
    src = local_media(a.get("avatar")) or local_media(a.get("avatar_static"))
    if src:
        return f'<img class="avatar {size}" src="{src}" alt="" loading="lazy">'
    return f'<span class="avatar {size} placeholder" aria-hidden="true">{initials(a)}</span>'


# ---------------------------------------------------------------- render pieces
def render_media(atts, sensitive, remote_url):
    if not atts:
        return ""
    items, lost = [], 0
    for m in atts:
        src = local_media(m.get("url"))
        alt = e(m.get("description") or "")
        if not src:
            lost += 1
            continue
        t = m.get("type")
        if t in ("video", "gifv"):
            attrs = "autoplay loop muted playsinline" if t == "gifv" else "controls preload=\"metadata\""
            items.append(f'<video src="{src}" {attrs} aria-label="{alt}"></video>')
        elif t == "audio":
            items.append(f'<audio src="{src}" controls preload="none" aria-label="{alt}"></audio>')
        else:
            items.append(f'<a href="{src}"><img src="{src}" alt="{alt}" loading="lazy"></a>')
    out = ""
    if items:
        grid = f'<div class="media n{min(len(items), 4)}">{"".join(items)}</div>'
        out = f'<details class="sensitive"><summary>Sensitive media — show</summary>{grid}</details>' if sensitive else grid
    if lost:
        out += (f'<p class="note">{lost} attachment{"s" if lost > 1 else ""} from another server '
                f'{"were" if lost > 1 else "was"} not archived. <a href="{e(remote_url or "#")}">View original</a></p>')
    return out


def render_card(c):
    if not c or not c.get("url"):
        return ""
    title = e(c.get("title") or c["url"])
    desc = e(c.get("description") or "")
    host = e(re.sub(r"^https?://([^/]+).*$", r"\1", c["url"]))
    return (f'<a class="card" href="{e(c["url"])}" rel="nofollow noopener">'
            f'<span class="card-host">{host}</span><strong>{title}</strong>'
            f'{f"<span>{desc}</span>" if desc else ""}</a>')


def render_poll(p):
    if not p:
        return ""
    total = p.get("votes_count") or 0
    rows = []
    for o in p.get("options", []):
        v = o.get("votes_count") or 0
        pct = round(100 * v / total) if total else 0
        rows.append(f'<li><span class="bar" style="width:{pct}%"></span><span class="opt">'
                    f'{emojify(o.get("title",""), p.get("emojis"), already_html=False)}</span><span class="pct">{pct}%</span></li>')
    return f'<ul class="poll">{"".join(rows)}</ul><p class="note">{total} vote{"s" if total != 1 else ""} · closed</p>'


def render_status(s, *, boosted_by=None, focus=False, link=True):
    """One post. If s is a boost, renders the boosted post with a 'boosted' line."""
    if s["reblog"]:
        return render_status(s["reblog"], boosted_by=(s["account"], s["created_at"]), link=link)
    a = s["account"]
    href = status_href(s)
    local = s["id"] in by_id
    content = rewrite_links(emojify(s.get("content") or "", s.get("emojis")))
    body = content
    if s.get("spoiler_text"):
        body = (f'<details class="cw"><summary>{emojify(s["spoiler_text"], s.get("emojis"), already_html=False)}'
                f' <span class="cw-btn">Show more</span></summary>{content}</details>')
    reply = ""
    if s.get("in_reply_to_id") and not focus:
        parent = by_id.get(s["in_reply_to_id"])
        if parent:
            reply = f'<p class="reply-to">Replying to <a href="{status_href(parent)}">{e(acct_handle(parent["account"]))}</a></p>'
        else:
            reply = '<p class="reply-to">Replying to a post on another server</p>'
    boost = ""
    if boosted_by:
        ba, bt = boosted_by
        boost = (f'<p class="boosted">↻ <a href="{profile_href(ba)}">{display_name(ba)}</a> boosted · '
                 f'<time datetime="{e(bt)}">{fmt_date(bt, False)}</time></p>')
    when = f'<time datetime="{e(s["created_at"])}">{fmt_date(s["created_at"], focus)}</time>'
    when_link = f'<a class="when" href="{e(href)}">{when}</a>' if link and href else when
    edited = f' <span class="edited" title="Edited {e(s["edited_at"])}">· edited</span>' if s.get("edited_at") else ""
    origin = "" if local else f'<a class="origin" href="{e(s.get("url") or s.get("uri") or "#")}">original ↗</a>'
    stats = (f'<footer class="stats"><span title="Replies">↩ {s.get("replies_count", 0)}</span>'
             f'<span title="Boosts">↻ {s.get("reblogs_count", 0)}</span>'
             f'<span title="Favourites">★ {s.get("favourites_count", 0)}</span>'
             f'{render_reactions(s.get("reactions"))}{origin}</footer>')
    return f"""<article class="status{' focus' if focus else ''}" id="s{e(s['id'])}">
  {boost}
  <header>
    <a class="who" href="{profile_href(a)}">{avatar(a)}<span><strong>{display_name(a)}</strong><span class="handle">{e(acct_handle(a))}</span></span></a>
    {when_link}{edited}
  </header>
  {reply}
  <div class="content" lang="{e(s.get('language') or '')}">{body}</div>
  {render_media(s.get('media_attachments'), s.get('sensitive'), s.get('url'))}
  {render_poll(s.get('poll'))}
  {render_card(s.get('card'))}
  {stats}
</article>"""


def render_reactions(rs):
    if not rs:
        return ""
    out = []
    for r in rs:
        name = r.get("name", "")
        icon = e(name)
        if r.get("url"):
            src = local_media(r["url"])
            if src:
                icon = f'<img class="emoji" src="{src}" alt=":{e(name)}:">'
        out.append(f'<span class="reaction">{icon} {r.get("count", 0)}</span>')
    return "".join(out)


# ---------------------------------------------------------------- page shell
CSS = (ROOT / "assets" / "style.css").read_text(encoding="utf-8")


def page(path, title, body, *, description="", noindex=False, og_image=None):
    title = re.sub(r"\s*:[A-Za-z0-9_]+:(?=[\s:\"]|$)", "", title).strip()
    meta =[f'<meta name="description" content="{e(description)}">'] if description else []
    if noindex:
        meta.append('<meta name="robots" content="noindex">')
    meta.append(f'<meta property="og:title" content="{e(title)}">')
    meta.append(f'<meta property="og:site_name" content="{e(INSTANCE["title"])} (archive)">')
    if og_image:
        meta.append(f'<meta property="og:image" content="{ORIGIN}{og_image}">')
    doc = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
{chr(10).join(meta)}
<link rel="icon" href="/system/custom_emojis/{OSOLIA_ICON}" type="image/png">
<link rel="stylesheet" href="/assets/style.css">
</head>
<body>
<div class="banner" role="note">
  <strong>Read-only archive.</strong> {e(INSTANCE["title"])} ran from {fmt_date(first, False)} to {fmt_date(last, False)}.
  Accounts here can no longer be followed or replied to.
</div>
<header class="site">
  <a class="brand" href="/">{emojify(":Osolia:", d["custom_emojis"], already_html=False)} {e(INSTANCE["title"])}</a>
  <nav><a href="/">Posts</a><a href="/directory/">People</a><a href="/about/">About</a></nav>
</header>
<main>
{body}
</main>
<footer class="site">
  <p>Archived from <code>{DOMAIN}</code> on {fmt_date(d["exported_at"], False)}. Public posts only.</p>
</footer>
</body>
</html>
"""
    dst = OUT / path.strip("/") / "index.html" if not path.endswith(".html") else OUT / path.lstrip("/")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(doc, encoding="utf-8")


def redirect(path, target):
    dst = OUT / path.strip("/") / "index.html"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(f'<!doctype html><meta charset="utf-8"><title>Redirecting…</title>'
                   f'<meta http-equiv="refresh" content="0; url={target}"><link rel="canonical" href="{target}">'
                   f'<a href="{target}">Continue</a>', encoding="utf-8")


# ---------------------------------------------------------------- build
if OUT.exists():
    shutil.rmtree(OUT)
(OUT / "assets").mkdir(parents=True)
(OUT / "assets" / "style.css").write_text(CSS, encoding="utf-8")

osolia_emoji = next((em for em in d["custom_emojis"] if em["shortcode"] == "Osolia"), d["custom_emojis"][0])
OSOLIA_ICON = osolia_emoji["url"].split("/system/custom_emojis/", 1)[1]
local_media(osolia_emoji["url"])

newest = sorted(originals, key=lambda s: s["created_at"], reverse=True)
by_month = defaultdict(list)
for s in newest:
    by_month[parse_dt(s["created_at"]).strftime("%B %Y")].append(s)


def account_card(a):
    return (f'<li><a class="person" href="{profile_href(a)}">{avatar(a, "lg")}<span><strong>{display_name(a)}</strong>'
            f'<span class="handle">{e(acct_handle(a))}</span><span class="note-snippet">{a.get("statuses_count", 0)} posts</span></span></a></li>')


# home: all original posts, newest first, grouped by month
timeline = "".join(f'<h2 class="month">{e(m)}</h2>' + "".join(render_status(s) for s in ss) for m, ss in by_month.items())
page("/", f"{INSTANCE['title']} — archive",
     f'<section class="intro"><h1>{e(INSTANCE["title"])}</h1><p>{e(INSTANCE["short_description"] or "")}</p>'
     f'<p class="note">{len(originals)} posts from {len(ACCOUNTS)} people.</p></section>{timeline}',
     description=INSTANCE["short_description"] or "")

# directory
people = sorted(ACCOUNTS, key=lambda a: (-(a.get("statuses_count") or 0), a["username"].lower()))
page("/directory/", f"People — {INSTANCE['title']}",
     f'<h1>People</h1><ul class="people">{"".join(account_card(a) for a in people)}</ul>')

# about
admin = acct_by_name.get((INSTANCE.get("contact_username") or "").lower())
page("/about/", f"About — {INSTANCE['title']}", f"""
<h1>About this archive</h1>
<p>{e(INSTANCE["short_description"] or "")}</p>
<p>{e(INSTANCE["title"])} was a Mastodon server at <code>{DOMAIN}</code>. It was active from
{fmt_date(first, False)} to {fmt_date(last, False)} and was shut down in {fmt_date(d["exported_at"], False)[:3]} {d["exported_at"][:4]}.
This site is a static copy of everything that was posted publicly.</p>
<ul>
  <li>Only public posts are included. Followers-only posts and direct messages are not.</li>
  <li>Media and avatars from other servers were not kept, so some boosts and replies point to the original instead.</li>
  <li>Like, boost and reply counts are frozen as they were on the last day.</li>
</ul>
{f'<p>Administrator: <a href="{profile_href(admin)}">{e(acct_handle(admin))}</a></p>' if admin else ''}
<p><a href="/archive.json">Download the archive as JSON</a></p>
""")

# profiles + post pages + legacy redirects
for a in ACCOUNTS:
    u = a["username"]
    posts = sorted([s for s in STATUSES if s["account"]["id"] == a["id"]], key=lambda s: s["created_at"], reverse=True)
    pinned = [by_id[i] for i in a.get("pinned_status_ids", []) if i in by_id]
    header_src = local_media(a.get("header")) if "missing.png" not in (a.get("header") or "") else None
    fields = "".join(
        f'<div class="field{" verified" if f.get("verified_at") else ""}"><dt>{emojify(f["name"], a.get("emojis"), False)}</dt>'
        f'<dd>{rewrite_links(emojify(f["value"], a.get("emojis")))}</dd></div>' for f in a.get("fields") or [])
    pinned_html = ("<h2 class=\"month\">Pinned</h2>" + "".join(render_status(s) for s in pinned)) if pinned else ""
    body = f"""
<section class="profile">
  <div class="header-img">{f'<img src="{header_src}" alt="">' if header_src else ''}</div>
  <div class="profile-main">
    {avatar(a, "xl")}
    <h1>{display_name(a)}</h1>
    <p class="handle">{e(acct_handle(a))}{' · <span class="tag">Bot</span>' if a.get('bot') else ''}</p>
    <div class="bio">{rewrite_links(emojify(a.get("note") or "", a.get("emojis")))}</div>
    {f'<dl class="fields">{fields}</dl>' if fields else ''}
    <p class="counts"><span><b>{a.get("statuses_count", 0)}</b> posts</span><span><b>{a.get("following_count", 0)}</b> following</span><span><b>{a.get("followers_count", 0)}</b> followers</span><span>Joined {fmt_date(a["created_at"], False)}</span></p>
  </div>
</section>
{pinned_html}
<h2 class="month">Posts and boosts</h2>
{''.join(render_status(s) for s in posts) or '<p class="note">No public posts.</p>'}
"""
    plain_note = re.sub(r"<[^>]+>", " ", a.get("note") or "").strip()
    page(f"/@{u}/", f"{a.get('display_name') or u} ({acct_handle(a)})", body,
         description=plain_note[:200], noindex=bool(a.get("noindex")), og_image=local_media(a.get("avatar")))
    redirect(f"/users/{u}/", f"/@{u}/")

    for s in posts:
        if s["reblog"]:
            continue
        chain, p = [], by_id.get(s.get("in_reply_to_id"))
        while p:
            chain.insert(0, p)
            p = by_id.get(p.get("in_reply_to_id"))

        def desc(x, depth=0):
            return "".join(f'<div class="thread-child">{render_status(c)}{desc(c, depth + 1)}</div>'
                           for c in sorted(children.get(x["id"], []), key=lambda c: c["created_at"]))

        thread = ("".join(render_status(c) for c in chain) + render_status(s, focus=True, link=False) + desc(s))
        text = re.sub(r"<[^>]+>", " ", s.get("content") or "").strip()
        page(f"/@{u}/{s['id']}/", f"{a.get('display_name') or u}: \"{text[:60]}\"",
             f'<div class="thread">{thread}</div>', description=text[:200], noindex=bool(a.get("noindex")))
        redirect(f"/users/{u}/statuses/{s['id']}/", f"/@{u}/{s['id']}/")

# tags
tag_posts = defaultdict(list)
for s in originals:
    for t in s.get("tags") or []:
        tag_posts[t["name"].lower()].append(s)
for t, ss in tag_posts.items():
    page(f"/tags/{t}/", f"#{t} — {INSTANCE['title']}",
         f'<h1>#{e(t)}</h1>' + "".join(render_status(s) for s in sorted(ss, key=lambda s: s["created_at"], reverse=True)))

# 404 that handles old URL shapes (e.g. /@user/123 without slash, /users/x/statuses/y/activity)
(OUT / "404.html").write_text(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Not found — {e(INSTANCE['title'])} archive</title>
<link rel="stylesheet" href="/assets/style.css"><script>
(function(){{var p=location.pathname,m;
if((m=p.match(/^\\/users\\/([^/]+)\\/statuses\\/(\\d+)/)))location.replace('/@'+m[1]+'/'+m[2]+'/');
else if((m=p.match(/^\\/@([^/@]+)\\/(\\d+)/)))location.replace('/@'+m[1]+'/'+m[2]+'/');
else if((m=p.match(/^\\/(?:users\\/|@)([^/@]+)/)))location.replace('/@'+m[1]+'/');}})();
</script></head><body><main><h1>Not in the archive</h1>
<p>This page wasn’t kept. It may have been a private post, a remote account or something from after the archive was made.</p>
<p><a href="/">Back to the archive</a></p></main></body></html>""", encoding="utf-8")

# public JSON (drop source_text; content already has the rendered version)
public = {k: v for k, v in d.items() if k != "skipped"}
public["statuses"] = [{k: v for k, v in s.items() if k != "source_text"} for s in STATUSES]
(OUT / "archive.json").write_text(json.dumps(public, ensure_ascii=False, indent=1), encoding="utf-8")
(OUT / "CNAME").write_text(DOMAIN + "\n")
(OUT / ".nojekyll").write_text("")
(OUT / "robots.txt").write_text("User-agent: *\nAllow: /\n")

print(f"built {len(originals)} posts, {len(ACCOUNTS)} profiles, {len(tag_posts)} tags; "
      f"copied {len(copied)} media files; missing locally: {len(missing_media)}")
for m in sorted(missing_media)[:20]:
    print("  missing:", m)
