"""Bespoke GitHub collector.

GitHub's public REST API (api.github.com) exposes a clean, structured view of a
user or organization: profile fields, the top repositories by stars, the profile
README and the READMEs of the top 3 repositories. No scraping or auth is needed
for public data (an optional GITHUB_TOKEN only raises the rate limit), so we read
the API directly for facts and text, and use Playwright only to capture a
screenshot of the profile page for the vision stage.

Best-effort and contained: unauthenticated API calls are rate-limited (403 with
X-RateLimit-Remaining: 0) and missing resources 404 — both are handled quietly.
Never raises out of collect(); every failure lands in art.errors."""

from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import httpx

from ..config import settings
from .base import GITHUB, SourceArtifact, screenshot as _screenshot
from .website import UA

_API = "https://api.github.com"


def _login_from_url(url: str) -> str | None:
    path = urlparse(url if "://" in url else "https://" + url).path
    parts = [p for p in path.split("/") if p]
    return parts[0] if parts else None


def collect(url: str, job_dir: Path) -> SourceArtifact:
    art = SourceArtifact(url=url, platform=GITHUB, method="github_api")
    login = _login_from_url(url)
    if not login:
        art.errors.append("github: no login in URL")
        return art

    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    top: list[str] = []
    try:
        with httpx.Client(headers=headers, timeout=15, follow_redirects=True) as c:
            # 1. Profile
            r = c.get(f"{_API}/users/{login}")
            if r.status_code == 404:
                art.errors.append(f"github: user {login} not found (404)")
                _screenshot(url, job_dir, art)
                return art
            if r.status_code == 403 and r.headers.get("X-RateLimit-Remaining") == "0":
                art.errors.append("github: API rate limit reached")
                _screenshot(url, job_dir, art)
                return art
            if r.status_code != 200:
                art.errors.append(f"github: user fetch HTTP {r.status_code}")
            else:
                u = r.json()
                for key, fact in (("name", "name"), ("login", "login"),
                                  ("type", "type"), ("bio", "bio"),
                                  ("company", "company"), ("location", "location"),
                                  ("blog", "blog"), ("followers", "followers"),
                                  ("public_repos", "public_repos")):
                    val = u.get(key)
                    if val not in (None, ""):
                        art.facts[fact] = val
                bio = (u.get("bio") or "").strip()
                if bio:
                    art.text_blocks.append({"label": "bio", "text": bio})

            # 2. Repositories by stars. Search, because the list-repos endpoint
            # cannot sort by stars; user: matches organisations too. Forks are
            # left out by search, which suits "their own top work".
            rr = c.get(f"{_API}/search/repositories?q=user:{login}"
                       f"&sort=stars&order=desc&per_page={settings.github_repos}")
            if rr.status_code == 200:
                repos = rr.json().get("items")
                if isinstance(repos, list) and repos:
                    top = [x["name"] for x in repos if x.get("name")
                           and x["name"].lower() != login.lower()][:3]
                    langs: Counter[str] = Counter()
                    lines = []
                    for repo in repos:
                        name = repo.get("name") or ""
                        desc = (repo.get("description") or "").strip()
                        lang = repo.get("language")
                        stars = repo.get("stargazers_count") or 0
                        if lang:
                            langs[lang] += 1
                        meta = []
                        if lang:
                            meta.append(lang)
                        meta.append(f"⭐{stars}")
                        tail = f" [{', '.join(meta)}]"
                        line = f"- {name}"
                        if desc:
                            line += f" — {desc}"
                        lines.append(line + tail)
                    if langs:
                        art.facts["top_languages"] = [l for l, _ in langs.most_common()]
                    art.text_blocks.append(
                        {"label": f"repositories ({len(lines)})",
                         "text": "\n".join(lines)})
            else:
                art.errors.append(f"github: repos fetch HTTP {rr.status_code}")

            # 3. READMEs: the profile one (repo <login>/<login>) and the top 3
            # repos by stars (404 is normal — ignore quietly)
            for label, repo in [("profile README", login)] + [
                    (f"README: {name}", name) for name in top]:
                rm = c.get(f"{_API}/repos/{login}/{repo}/readme",
                           headers={"Accept": "application/vnd.github.raw+json"})
                if rm.status_code == 200:
                    readme = (rm.text or "").strip()
                    if readme:
                        art.text_blocks.append(
                            {"label": label,
                             "text": readme[:settings.github_readme_chars]})
    except Exception as exc:
        art.errors.append(f"github: {type(exc).__name__}: {exc}")

    _screenshot(url, job_dir, art)
    art.ok = bool(art.text_blocks) or bool(
        {"followers", "public_repos"} & set(art.facts))
    return art
