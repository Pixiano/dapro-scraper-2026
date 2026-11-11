"""Phase 3: per-entity live collection, bucketed by quarter. No LLM.

Sources per entity (company or competitor):
  news    Hacker News (Algolia API, points > HN_MIN_POINTS) + Google News RSS
  blogs   official blog feed (Wikidata P1581, else probed paths) + Medium feed
  social  YouTube uploads (Data API when keyed, else the channel RSS feed)

Every item: {source, title, url, published (ISO date), quarter}. Only items
whose title names the entity count (collision guard for "Apple", "Meta").
Fail soft: per-source errors are recorded, never raised."""

import email.utils
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .. import quota
from ..collectors import news, youtube
from ..config import settings
from . import UA_PLAIN
from . import wikidata as wd

HN = "https://hn.algolia.com/api/v1/search_by_date"
HN_MIN_POINTS = 20
FEED_PATHS = ("/feed", "/rss", "/rss/", "/rss.xml", "/atom.xml", "/feed.xml",
              "/blog/rss.xml", "/news/rss.xml", "/blog/feed")
YT_MAX_PAGES = 12  # 50 videos/page, 1 quota unit each; stops once past the window start


# ---- quarters ----

def quarter_bounds(today: date) -> list[tuple[str, date, date]]:
    """[(label, start, end_exclusive)] for the two previous quarters and the
    current one to date, oldest first."""
    out = []
    q0 = (today.year * 4) + (today.month - 1) // 3
    for back in (2, 1, 0):
        q = q0 - back
        y, n = divmod(q, 4)
        start = date(y, n * 3 + 1, 1)
        ny, nn = divmod(q + 1, 4)
        end = date(ny, nn * 3 + 1, 1)
        label = f"Q{n + 1} {y}"
        if back == 0:
            end = date.fromordinal(today.toordinal() + 1)
            label += " (to date)"
        out.append((label, start, end))
    return out


def bucket(d: date | None, bounds: list[tuple[str, date, date]]) -> str | None:
    for label, s, e in bounds:
        if d and s <= d < e:
            return label
    return None


def months(start: date, end: date) -> list[tuple[date, date]]:
    out, cur = [], start
    while cur < end:
        nxt = date(cur.year + cur.month // 12, cur.month % 12 + 1, 1)
        out.append((cur, min(nxt, end)))
        cur = nxt
    return out


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(s)  # RSS: RFC 822
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))  # Atom / ISO
        except ValueError:
            return None
    return (dt.astimezone(timezone.utc) if dt.tzinfo else dt).date()  # bucket in UTC


def names_entity(title: str, name: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", title, re.I) is not None


def _item(source: str, title: str, url: str, d: date | None,
          bounds: list[tuple[str, date, date]], **extra) -> dict | None:
    q = bucket(d, bounds)
    if not q or not url:
        return None
    return {"source": source, "title": title.strip(), "url": url,
            "published": d.isoformat() if d else None, "quarter": q, **extra}


def _http(url: str, **params) -> httpx.Response:
    return httpx.get(url, params=params or None, headers={"User-Agent": UA_PLAIN},
                     timeout=20, follow_redirects=True)


# ---- sources ----

def hacker_news(name: str, bounds, errors: list[str]) -> list[dict]:
    start = int(datetime.combine(bounds[0][1], datetime.min.time(), timezone.utc).timestamp())
    try:
        r = _http(HN, query=f'"{name}"', tags="story", hitsPerPage=1000,
                  numericFilters=f"created_at_i>={start},points>{HN_MIN_POINTS}")
        r.raise_for_status()
        hits = r.json().get("hits") or []
    except Exception as exc:
        errors.append(f"hn: {type(exc).__name__}: {exc}")
        return []
    out = []
    for h in hits:
        title = h.get("title") or ""
        if not names_entity(title, name):
            continue
        it = _item("hacker_news", title,
                   f"https://news.ycombinator.com/item?id={h.get('objectID')}",
                   _parse_date(h.get("created_at")), bounds,
                   points=h.get("points"), comments=h.get("num_comments"),
                   link=h.get("url"))
        if it:
            out.append(it)
    return out


def google_news(name: str, bounds, errors: list[str]) -> list[dict]:
    out = []
    # one query per month: the feed caps at 100 items per query.
    # ponytail: very loud names (OpenAI) can still hit 100/month; split weekly if needed
    for s, e in months(bounds[0][1], bounds[-1][2]):
        # after: may be exclusive; start a day early, dedupe + bucketing absorb the overlap
        text, err = news._fetch(news._feed_url(
            f'"{name}" after:{s - timedelta(days=1)} before:{e}'))
        if err:
            errors.append(f"google_news: {err}")
            continue
        for it in news._parse_items(text or ""):
            if not names_entity(it["title"], name):
                continue
            x = _item("google_news", it["title"], it["link"], _parse_date(it["published"]),
                      bounds, publisher=it["publisher"])
            if x:
                out.append(x)
    return out


def feed_items(xml: str, base: str = "") -> list[dict]:
    """RSS <item> and Atom <entry> -> [{title, link, published}]. Relative links
    are resolved against `base` (the feed URL)."""
    soup = BeautifulSoup(xml, "xml")
    out = []
    for it in soup.find_all(["item", "entry"]):
        # Atom: the article is rel="alternate" (Blogger lists rel="replies" first)
        link_tag = it.find("link", rel="alternate") or it.find("link")
        link = ((link_tag.get("href") or link_tag.get_text(strip=True)) if link_tag else "")
        # first tag *in this order*: Atom <updated> (last edit) only as a fallback
        pub = next((t for n in ("pubDate", "published", "dc:date", "date", "updated")
                    if (t := it.find(n))), None)
        title = it.find("title")
        out.append({"title": title.get_text(strip=True) if title else "",
                    "link": urljoin(base, str(link)) if link else "",
                    "published": pub.get_text(strip=True) if pub else None})
    return out


def _try_feed(url: str) -> list[dict] | None:
    try:
        r = _http(url)
        if r.status_code != 200 or not re.search(r"<(rss|feed|rdf:RDF)\b", r.text[:2000]):
            return None
        return feed_items(r.text, str(r.url))
    except Exception:
        return None


def blogs(links: dict, bounds, errors: list[str]) -> tuple[list[dict], str | None]:
    """-> (items, feed URL used). First-party feed first, then Medium."""
    out: list[dict] = []
    used = None
    bases = [b.rstrip("/") for b in (links.get("blog"), links.get("website")) if b]
    candidates = ([links["blog"]] if links.get("blog") else []) + \
        [b + p for b in bases for p in FEED_PATHS]
    for url in candidates:
        items = _try_feed(url)
        if items:
            used = url
            out += [x for it in items if (x := _item("blog", it["title"], it["link"],
                                                    _parse_date(it["published"]), bounds))]
            break
    if links.get("medium"):  # Medium feeds only return the latest 10 posts
        user = links["medium"].rstrip("/").rsplit("/", 1)[-1]
        items = _try_feed(f"https://medium.com/feed/{user}")
        if items is None:
            errors.append(f"medium: no feed for {user}")
        out += [x for it in items or [] if (x := _item("medium", it["title"], it["link"],
                                                      _parse_date(it["published"]), bounds,
                                                      note="latest 10 only"))]
    return out, used


def youtube_videos(channel_url: str | None, bounds, errors: list[str]) -> list[dict]:
    if not channel_url:
        return []
    cid = channel_url.rstrip("/").rsplit("/", 1)[-1]
    if settings.youtube_api_key:
        try:
            return _yt_api(cid, bounds)
        except Exception as exc:  # YouTubeError / QuotaExhausted -> key-free feed
            errors.append(f"youtube api: {type(exc).__name__}: {exc}; using RSS (last 15)")
    items = _try_feed(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}")
    if items is None:
        errors.append(f"youtube rss: no feed for {cid}")
    return [x for it in items or [] if (x := _item("youtube", it["title"], it["link"],
                                                  _parse_date(it["published"]), bounds,
                                                  note="RSS: latest 15 only"))]


def _yt_api(cid: str, bounds) -> list[dict]:
    quota.init_db()
    out: list[dict] = []
    token = None
    with httpx.Client(timeout=20) as client:
        for _ in range(YT_MAX_PAGES):
            data = youtube._get(client, "playlistItems", part="snippet,contentDetails",
                                playlistId="UU" + cid[2:], maxResults=50, pageToken=token)
            past_start = False
            for v in data.get("items") or []:
                d = _parse_date((v.get("contentDetails") or {}).get("videoPublishedAt"))
                vid = (v.get("contentDetails") or {}).get("videoId")
                if d and d < bounds[0][1]:
                    past_start = True
                    continue
                x = _item("youtube", (v.get("snippet") or {}).get("title", ""),
                          f"https://www.youtube.com/watch?v={vid}", d, bounds)
                if x:
                    out.append(x)
            token = data.get("nextPageToken")
            if past_start or not token:
                break
    return out


# ---- per entity ----

def entity(e: dict, bounds) -> dict:
    """e: {label, links, ...} (company or competitor) -> e + collected items."""
    errors: list[str] = []
    # search with the name it was found/typed as: "Meta", not "Meta Platforms"
    name = e.get("name") or (e.get("matched_names") or [None])[0] or e["label"]
    links = e.get("links") or {}
    blog_items, feed = blogs(links, bounds, errors)
    items = (hacker_news(name, bounds, errors) + google_news(name, bounds, errors)
             + blog_items + youtube_videos(links.get("youtube"), bounds, errors))
    seen, uniq = set(), []
    for it in items:
        if it["url"] not in seen:
            seen.add(it["url"])
            uniq.append(it)
    return {"label": e["label"], "searched_as": name, "qid": e.get("qid"), "links": links,
            "blog_feed": feed,
            "items": sorted(uniq, key=lambda i: i["published"] or "", reverse=True),
            "errors": errors}


def collect(company: dict, competitors: list[dict], today: date | None = None) -> dict:
    bounds = quarter_bounds(today or date.today())
    targets = [{"label": company.get("label") or company["query"],
                "name": wd.base_name(company.get("label") or company["query"]),
                "qid": company.get("qid"),
                "links": company.get("links") or {}}] + competitors
    # ponytail: 4 threads; sources are different hosts. Raise if runs stay slow.
    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(lambda t: entity(t, bounds), targets))
    return {"quarters": [{"label": lbl, "start": s.isoformat(), "end": e.isoformat()}
                         for lbl, s, e in bounds],
            "company": results[0], "competitors": results[1:]}
