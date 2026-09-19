"""Build the findings site: ``blog/`` in, static ``site/`` out.

A post is ``blog/posts/YYYY-MM-DD-slug.md`` with a small header::

    ---
    title: What the bots draft first
    summary: One sentence for the index.
    ---

and Markdown after it. ``{{figure name}}`` inlines ``blog/figures/name.svg`` (or a ``.html`` table)
(generated from simulation output by ``site.figures``), so charts pick up the
page's light/dark theme. Posts are never rewritten by the build: each day's
entry stays as it was published, and a new finding is a new post.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BLOG = ROOT / "blog"
SITE = ROOT / "site"
REPO = "https://github.com/deknapp/mulligan"
TITLE = "mulligan: simulated Limited"
FIGURE = re.compile(r"\{\{\s*figure\s+([\w-]+)\s*\}\}")


@dataclass
class Post:
    slug: str
    date: str
    title: str
    summary: str
    body: str
    order: int = 0   # breaks ties between posts on the same day: higher is newer


def load_posts(blog: Path = BLOG) -> list[Post]:
    posts = []
    for path in sorted((blog / "posts").glob("*.md")):
        text = path.read_text()
        meta: dict[str, str] = {}
        if text.startswith("---"):
            head, text = text[3:].split("\n---", 1)
            for line in head.strip().splitlines():
                key, _, value = line.partition(":")
                meta[key.strip()] = value.strip().strip('"')
        date = path.stem[:10]
        posts.append(Post(path.stem, date, meta.get("title", path.stem),
                          meta.get("summary", ""), text.strip(), int(meta.get("order", 0))))
    return sorted(posts, key=lambda p: (p.date, p.order, p.slug), reverse=True)


def _render(post: Post, figures: Path) -> str:
    import markdown

    def figure(match: re.Match) -> str:
        svg = figures / f"{match.group(1)}.svg"
        if svg.exists():
            return f'\n<figure class="fig">{svg.read_text()}</figure>\n'
        table = figures / f"{match.group(1)}.html"
        if table.exists():
            return f'\n<div class="table">{table.read_text()}</div>\n'
        raise FileNotFoundError(f"{post.slug}: missing figure {match.group(1)}")

    html = markdown.markdown(post.body, extensions=["tables", "fenced_code", "sane_lists"])
    # Figures are block-level: unwrap the paragraph Markdown puts around them.
    html = re.sub(r"<p>\s*(\{\{\s*figure\s+[\w-]+\s*\}\})\s*</p>", r"\1", html)
    return FIGURE.sub(figure, html)


def _date(d: str) -> str:
    import datetime
    return datetime.date.fromisoformat(d).strftime("%B %-d, %Y")


def _page(title: str, body: str, depth: int = 0, description: str = "") -> str:
    up = "../" * depth
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)}</title>
<meta name="description" content="{escape(description)}">
<link rel="stylesheet" href="{up}style.css">
<link rel="alternate" type="application/rss+xml" title="{TITLE}" href="{up}feed.xml">
</head>
<body>
<header class="top"><a class="brand" href="{up}index.html">mulligan</a>
<nav><a href="{up}index.html">Findings</a><a href="{up}method.html">How it works</a>
<a href="{REPO}">Code</a></nav></header>
<main>
{body}
</main>
<footer>Simulated with <a href="{REPO}">mulligan</a>, an open-source Magic: The Gathering
Limited simulator. Card data from Scryfall; real-game comparisons from
<a href="https://www.17lands.com">17Lands</a>' public data. Not affiliated with
Wizards of the Coast.</footer>
</body>
</html>
"""


def build(blog: Path = BLOG, site: Path = SITE) -> list[Path]:
    """Render every post, the index, the method page and the feed."""
    posts = load_posts(blog)
    figures = blog / "figures"
    if site.exists():
        shutil.rmtree(site)
    (site / "posts").mkdir(parents=True)
    shutil.copy(Path(__file__).with_name("style.css"), site / "style.css")
    written = []
    for post in posts:
        body = (f'<article><p class="date">{_date(post.date)}</p>'
                f"<h1>{escape(post.title)}</h1>{_render(post, figures)}</article>")
        path = site / "posts" / f"{post.slug}.html"
        path.write_text(_page(post.title, body, depth=1, description=post.summary))
        written.append(path)
    items = "\n".join(
        f'<li><a href="posts/{p.slug}.html"><span class="date">{_date(p.date)}</span>'
        f"<strong>{escape(p.title)}</strong><span class=\"summary\">{escape(p.summary)}"
        f"</span></a></li>" for p in posts)
    intro = (blog / "intro.md").read_text() if (blog / "intro.md").exists() else ""
    import markdown
    index = (f'<section class="intro">{markdown.markdown(intro)}</section>'
             f'<h2 class="list-head">Findings</h2><ul class="posts">{items}</ul>')
    (site / "index.html").write_text(_page(TITLE, index, description=TITLE))
    method = blog / "method.md"
    if method.exists():
        body = f"<article>{markdown.markdown(method.read_text(), extensions=['tables'])}</article>"
        (site / "method.html").write_text(_page("How it works", body))
    (site / "feed.xml").write_text(_feed(posts))
    (site / ".nojekyll").write_text("")
    return written + [site / "index.html"]


def _rfc822(d: str) -> str:
    import datetime
    from email.utils import format_datetime
    day = datetime.date.fromisoformat(d)
    return format_datetime(datetime.datetime(day.year, day.month, day.day,
                                             tzinfo=datetime.UTC))


def _feed(posts: list[Post]) -> str:
    base = "https://deknapp.github.io/mulligan/"
    items = "".join(
        f"<item><title>{escape(p.title)}</title><link>{base}posts/{p.slug}.html</link>"
        f"<guid>{base}posts/{p.slug}.html</guid><pubDate>{_rfc822(p.date)}</pubDate>"
        f"<description>{escape(p.summary)}</description></item>" for p in posts)
    return (f'<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>'
            f"<title>{TITLE}</title><link>{base}</link>"
            f"<description>Findings from simulated Magic: The Gathering Limited games"
            f"</description>{items}</channel></rss>")
