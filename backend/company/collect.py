"""Phase 3: per-entity live collection, bucketed by quarter. No LLM.

Sources per entity (company or competitor):
  news    Hacker News (Algolia API, points > HN_MIN_POINTS) + Google News RSS
  blogs   official blog feed (Wikidata P1581 or the website: the feed the page
          links to, else probed paths) + Medium feed
  social  YouTube uploads (Data API when keyed, else the channel RSS feed)
  releases and filings (not headlines: the report counts them apart)
          Hugging Face models, new public GitHub repos, SEC 8-K/6-K filings

Every item: {source, title, url, published (ISO date), quarter}. Only news items
whose headline names the entity count (collision guard for "Apple", "Meta").
Fail soft: per-source errors are recorded, never raised."""

import email.utils
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .. import quota
from ..collectors import news, youtube
from ..config import settings
from . import UA, UA_PLAIN
from . import social as social_mod
from . import wikidata as wd

HN = "https://hn.algolia.com/api/v1/search_by_date"
HN_MIN_POINTS = 20
FEED_PATHS = ("/feed", "/rss", "/rss/", "/rss.xml", "/atom.xml", "/feed.xml",
              "/blog/rss.xml", "/news/rss.xml", "/blog/feed")
NEWS_FULL = 95  # Google News returns at most ~100 items per query
NEWS_MIN_DAYS = 3  # smallest window a full one is split into; lower = more requests
YT_MAX_PAGES = 12  # 50 videos/page, 1 quota unit each; stops once past the window start
HF_API = "https://huggingface.co/api"
HF_LIMIT = 500  # models per org, newest first: about a year for the busiest (Microsoft)
GH_PAGES = 4  # 100 repos/page, newest first; Microsoft opens ~100 in six weeks
SEC_FORMS = ("8-K", "6-K")  # current reports; 6-K: foreign issuers (Wipro), no item codes
SEC_ITEMS = {"1.01": "material agreement", "1.02": "agreement ended", "1.05": "cybersecurity "
             "incident", "2.01": "acquisition or sale completed", "2.02": "quarterly results",
             "2.03": "new debt", "2.05": "restructuring", "2.06": "impairment",
             "3.01": "listing notice", "5.02": "officer or director change", "5.03": "bylaws "
             "change", "5.07": "shareholder vote", "7.01": "investor disclosure",
             "8.01": "other events"}  # 9.01 (exhibits) is on almost every one: left out


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


def headline(it: dict) -> str:
    """Title without the " - Publisher" suffix Google News adds: "... - Yahoo Finance"
    does not name Yahoo and "... - AI Business" is not AI news."""
    return it["title"].removesuffix(" - " + (it.get("publisher") or ""))


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

def hacker_news(name: str, bounds, errors: list[str], context: str = "") -> list[dict]:
    start = int(datetime.combine(bounds[0][1], datetime.min.time(), timezone.utc).timestamp())
    try:
        r = _http(HN, query=f'"{name}" {context}'.strip(), tags="story", hitsPerPage=1000,
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


def _news_window(query: str, s: date, e: date, client, errors: list[str]) -> list[dict]:
    """Raw feed items for [s, e). A full reply is a capped sample, so the window is
    halved and each half refetched, down to NEWS_MIN_DAYS. The halves overlap the
    parent and each other by a day: entity() dedupes."""
    # after: may be exclusive; start a day early, dedupe + bucketing absorb the overlap
    url = news._feed_url(f"{query} after:{s - timedelta(days=1)} before:{e}")
    text, err = news._fetch(url, client)
    if err and "429" in err:  # a burst of requests is rate-limited for a few seconds
        time.sleep(5)
        text, err = news._fetch(url, client)
    if err:
        errors.append(f"google_news: {err}")
        return []
    items = news._parse_items(text or "")
    if len(items) < NEWS_FULL:
        return items
    if (e - s).days < 2 * NEWS_MIN_DAYS:
        # ponytail: the loudest names (OpenAI) fill 100 items in two days; lower NEWS_MIN_DAYS
        note = "google_news: some periods still hit the 100-item cap, counts there are a sample"
        if note not in errors:
            errors.append(note)
        return items
    mid = s + (e - s) // 2
    return (items + _news_window(query, s, mid, client, errors)
            + _news_window(query, mid, e, client, errors))


def google_news(name: str, bounds, errors: list[str], context: str = "") -> list[dict]:
    out = []
    with news._client() as client:  # one connection for the whole entity, not one per query
        # one query per month to start with: the feed caps at 100 items per query
        for s, e in months(bounds[0][1], bounds[-1][2]):
            for it in _news_window(f'"{name}" {context}'.strip(), s, e, client, errors):
                if not names_entity(headline(it), name):
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


def _linked_feeds(page: str) -> list[str]:
    """Feeds a page advertises: <link rel="alternate" type="application/rss+xml" href>."""
    try:
        r = _http(page)
        return [urljoin(str(r.url), str(tag["href"]))
                for tag in BeautifulSoup(r.text, "html.parser").find_all(
                    "link", rel="alternate", href=True)
                if re.search(r"rss|atom", str(tag.get("type") or ""))]
    except Exception:
        return []


def _feed_candidates(links: dict):
    """Lazily, cheapest first: the blog URL itself, the feeds the blog and the
    website link to, and only then the probed paths."""
    pages = [b for b in (links.get("blog"), links.get("website")) if b]
    if links.get("blog"):
        yield links["blog"]
    for page in pages:
        yield from _linked_feeds(page)
    yield from (b.rstrip("/") + p for b in pages for p in FEED_PATHS)


def blogs(links: dict, bounds, errors: list[str]) -> tuple[list[dict], str | None]:
    """-> (items, feed URL used). First-party feed first, then Medium."""
    out: list[dict] = []
    used = None
    for url in _feed_candidates(links):
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


# ---- releases and filings ----

def _hf_org(name: str, links: dict, errors: list[str]) -> str | None:
    """Wikidata's Hugging Face id; else an org named like the company's GitHub account
    or its name ("mistralai"), kept only if Hugging Face has verified the org and its
    display name names the company ("AI at Meta"). Unverified lookalikes are refused."""
    if links.get("huggingface"):
        return links["huggingface"].rstrip("/").rsplit("/", 1)[-1]
    tries = [links["github"].rstrip("/").rsplit("/", 1)[-1]] if links.get("github") else []
    for org in dict.fromkeys(t.lower() for t in tries + [re.sub(r"[^\w]", "", name)] if t):
        try:
            r = _http(f"{HF_API}/organizations/{org}/overview")
            o = r.json() if r.status_code == 200 else {}
        except Exception as exc:
            errors.append(f"huggingface: {type(exc).__name__}: {exc}")
            return None
        if o.get("isVerified") and names_entity(o.get("fullname") or "", name):
            return o["name"]
    return None


def hugging_face(name: str, links: dict, bounds, errors: list[str]) -> tuple[list[dict], str | None]:
    """-> (model releases, org). Every one is AI by nature; the report counts them apart."""
    org = _hf_org(name, links, errors)
    if not org:
        return [], None
    try:
        r = _http(f"{HF_API}/models", author=org, sort="createdAt", direction=-1, limit=HF_LIMIT)
        r.raise_for_status()
        models = r.json()
    except Exception as exc:
        errors.append(f"huggingface: {type(exc).__name__}: {exc}")
        return [], org
    if len(models) == HF_LIMIT and (_parse_date(models[-1].get("createdAt")) or date.max) >= bounds[0][1]:
        errors.append(f"huggingface: over {HF_LIMIT} models, the oldest quarter is undercounted")
    return [x for m in models if (x := _item(
        "huggingface", m["id"], f"https://huggingface.co/{m['id']}",
        _parse_date(m.get("createdAt")), bounds, likes=m.get("likes") or 0))], org


def github_repos(links: dict, bounds, errors: list[str]) -> list[dict]:
    """New public repos (forks skipped) of the GitHub account on Wikidata."""
    if not links.get("github"):
        return []
    user = links["github"].rstrip("/").rsplit("/", 1)[-1]
    headers = {"User-Agent": UA_PLAIN, "Accept": "application/vnd.github+json"}
    if settings.github_token:  # 60 -> 5,000 requests an hour
        headers["Authorization"] = f"Bearer {settings.github_token}"
    out: list[dict] = []
    try:
        for page in range(1, GH_PAGES + 1):
            r = httpx.get(f"https://api.github.com/users/{user}/repos", headers=headers, timeout=20,
                          params={"type": "owner", "sort": "created", "direction": "desc",
                                  "per_page": 100, "page": page})
            r.raise_for_status()
            repos = r.json()
            out += [x for g in repos if not g.get("fork") and (x := _item(
                "github", g["full_name"], g["html_url"], _parse_date(g.get("created_at")), bounds,
                stars=g.get("stargazers_count") or 0, description=g.get("description") or ""))]
            if len(repos) < 100 or (_parse_date(repos[-1].get("created_at")) or date.min) < bounds[0][1]:
                break
        else:
            errors.append(f"github: over {GH_PAGES * 100} new repos, the oldest quarter is undercounted")
    except Exception as exc:  # 403 = the hourly limit: set GITHUB_TOKEN
        errors.append(f"github: {type(exc).__name__}: {exc}")
    return out


def sec_filings(links: dict, bounds, errors: list[str]) -> list[dict]:
    """8-K/6-K current reports from EDGAR (CIK on Wikidata: US-listed companies only).
    Not AI news: the report counts them apart and never in the AI share."""
    if not links.get("sec"):
        return []
    cik = links["sec"].rsplit("=", 1)[-1]
    try:  # SEC refuses requests without a contact in the User-Agent
        r = httpx.get(f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json",
                      headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
        rec = (r.json().get("filings") or {}).get("recent") or {}
    except Exception as exc:
        errors.append(f"sec: {type(exc).__name__}: {exc}")
        return []
    out = []
    for form, day, acc, doc, codes in zip(*(rec.get(k) or [] for k in (
            "form", "filingDate", "accessionNumber", "primaryDocument", "items"))):
        d = _parse_date(day)
        if form not in SEC_FORMS or not d:
            continue
        what = ", ".join(SEC_ITEMS.get(c, f"item {c}") for c in codes.split(",")
                         if c and c != "9.01") or "foreign issuer report"
        x = _item("sec", f"Form {form} filed {d.day} {d:%b %Y}: {what}",
                  f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{doc}",
                  d, bounds, form=form)
        if x:
            out.append(x)
    return out


# ---- per entity ----

def entity(e: dict, bounds, social: bool = False) -> dict:
    """e: {label, links, ...} (company or competitor) -> e + collected items."""
    errors: list[str] = []
    # search with the name it was found/typed as: "Meta", not "Meta Platforms"
    name = e.get("name") or (e.get("matched_names") or [None])[0] or e["label"]
    links = e.get("links") or {}
    blog_items, feed = blogs(links, bounds, errors)
    ctx = e.get("context") or ""  # "crypto": required extra word for a shared name
    hf, org = hugging_face(name, links, bounds, errors)
    if org and not links.get("huggingface"):  # found on the Hub, not on Wikidata
        links = {**links, "huggingface": f"https://huggingface.co/{org}"}
    items = (hacker_news(name, bounds, errors, ctx) + google_news(name, bounds, errors, ctx)
             + blog_items + youtube_videos(links.get("youtube"), bounds, errors)
             + hf + github_repos(links, bounds, errors) + sec_filings(links, bounds, errors))
    seen: set = set()
    uniq = []
    for it in items:
        # news: the same article comes back under a second redirect URL
        same = (it["source"], it["title"].casefold()) if it["source"] in (
            "google_news", "hacker_news") else it["url"]
        if it["url"] not in seen and same not in seen:
            seen.update((it["url"], same))
            uniq.append(it)
    return {"label": e["label"], "searched_as": name, "qid": e.get("qid"), "links": links,
            "blog_feed": feed, "social": social_mod.profiles(links) if social else None,
            "items": sorted(uniq, key=lambda i: i["published"] or "", reverse=True),
            "errors": errors}


def collect(company: dict, competitors: list[dict], today: date | None = None,
            social: bool = False) -> dict:
    bounds = quarter_bounds(today or date.today())
    targets = [{"label": company.get("label") or company["query"],
                "name": company.get("search_name")
                or wd.base_name(company.get("label") or company["query"]),
                "context": company.get("context"),
                "qid": company.get("qid"),
                "links": company.get("links") or {}}] + competitors
    # ponytail: 4 threads; sources are different hosts. Raise if runs stay slow.
    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(lambda t: entity(t, bounds, social), targets))
    return {"quarters": [{"label": lbl, "start": s.isoformat(), "end": e.isoformat()}
                         for lbl, s, e in bounds],
            "company": results[0], "competitors": results[1:]}
