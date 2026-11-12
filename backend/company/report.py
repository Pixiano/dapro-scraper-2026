"""Phase 4: AI filter, live link check, rule-based outlook, Markdown -> PDF.
No LLM: every number is a count, every inference a stated rule."""

import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from ..pdf import render_markdown_pdf
from . import linkcheck, visual
from .analysis import prose

# Word-boundary AI vocabulary. "AI" is case-sensitive ("ai" in other words is
# not AI); the entity's own name is removed first so "Mistral AI" / "OpenAI"
# headlines are not AI just by naming the company.
# ponytail: keyword list, not a classifier; extend the list when misses show up
AI = re.compile(
    r"(?<![\w])(?:(?i:AI agents?|artificial intelligence|machine learning|deep learning|"
    r"neural (?:networks?|engine)|large language models?|language models?|"
    r"foundation models?|reasoning models?|generative|chatbots?|agentic|copilot|chatgpt|"
    r"gpt-?\d[\w.]*|gemini|claude|llama|grok|inference)|AI|A\.I\.|AGI|GenAI|LLMs?|GPUs?)"
    r"(?![\w])")  # hyphens allowed: "AI-powered", "Gen-AI"
SRC = {"hacker_news": "Hacker News", "google_news": "Google News", "blog": "Official blog",
       "medium": "Medium", "youtube": "YouTube"}
FIRST_PARTY = {"blog", "medium", "youtube"}
# Feeds that only return the latest N posts reach back into this quarter but
# not the last one: counting them would bias the outlook toward "up".
CAPPED = {"blog", "medium"}
BASE = {"ai", "a.i."}  # in every AI headline: says nothing about focus
TOP = 6  # items listed per quarter per entity


# Also the Gemini exchange, people named Claude, the animal, GitHub's non-AI news...
AMBIG = {"gemini", "claude", "llama", "grok", "copilot", "inference"}
_A = r"(?:gemini|claude|llama|grok|copilot)"
AMBIG_CTX = re.compile(
    rf"(?i:(?:google|anthropic|meta|xai|microsoft|github)(?:['’]s)?\W+{_A}(?![\w])"
    rf"|(?<![\w]){_A}\W+(?:\d|(?:pro|flash|ultra|nano|opus|sonnet|haiku|code|advanced)(?![\w])))")


def ai_terms(title: str, *names: str | None) -> list[str]:
    """Names (search name, label) are removed first, with a trailing "AI":
    a competitor found as "Mistral" still strips "Mistral AI". Ambiguous terms
    count only with a non-ambiguous AI term or in model context (AMBIG_CTX).
    ponytail: context list; a bare "Gemini tops charts" is missed"""
    ctx = AMBIG_CTX.search(title)  # before stripping: "Google Gemini" for Google itself
    for n in filter(None, names):
        title = re.sub(rf"(?<![\w]){re.escape(n)}(?:\s+AI)?(?![\w])", " ", title, flags=re.I)
    terms = [m.lower() for m in AI.findall(title)]
    return terms if ctx or set(terms) - AMBIG else []


def capped(i: dict) -> bool:
    return i["source"] in CAPPED or bool(i.get("note"))  # note = "RSS: latest 15 only"


def quarter_end(start: date) -> date:
    m = start.month + 2  # 0-based month index three months on
    return date(start.year + m // 12, m % 12 + 1, 1)


def outlook(last: int, to_date: int, q_start: date, today: date) -> dict:
    """Straight-line run-rate of the current quarter vs the last full one.
    A stated rule, not a forecast."""
    total = (quarter_end(q_start) - q_start).days
    elapsed = min((today - q_start).days + 1, total)
    proj = round(to_date * total / elapsed)
    frac = elapsed / total
    conf = ("high" if frac >= .5 and to_date >= 20 else
            "medium" if frac >= .25 and to_date >= 5 else "low")
    trend = ("new" if proj else "none") if last == 0 else \
        "up" if proj > last * 1.2 else "down" if proj < last * .8 else "flat"
    return {"projection": proj, "trend": trend, "confidence": conf,
            "elapsed_days": elapsed, "quarter_days": total}


def analyse(e: dict, labels: list[str], cur_start: date, today: date) -> dict:
    ai = []
    for it in e["items"]:
        terms = ai_terms(it["title"], e.get("searched_as"), e.get("label"))
        if terms:
            ai.append({**it, "terms": terms,
                       "confidence": "high" if it["source"] in FIRST_PARTY else "medium"})
    counts = {lb: sum(i["quarter"] == lb for i in ai) for lb in labels}
    full = {lb: sum(i["quarter"] == lb and not capped(i) for i in ai) for lb in labels}
    return {"ai": ai, "counts": counts, "total": {lb: sum(i["quarter"] == lb for i in e["items"])
                                                  for lb in labels},
            "by_source": {s: [sum(i["quarter"] == lb and i["source"] == s for i in ai)
                              for lb in labels] for s in SRC},
            "themes": Counter(t for i in ai for t in set(i["terms"])).most_common(6),
            "themes_q": {lb: [t for t, _ in Counter(t for i in ai if i["quarter"] == lb
                                                    for t in set(i["terms"]) - BASE).most_common(5)]
                         for lb in labels},
            "outlook": {**outlook(full[labels[1]], full[labels[2]], cur_start, today),
                        "last": full[labels[1]], "to_date": full[labels[2]]}}


def top(ai: list[dict], label: str, n: int) -> list[dict]:
    """Round-robin across sources (HN by points, the rest newest first) so one
    loud source cannot fill the list."""
    xs = sorted((i for i in ai if i["quarter"] == label),
                key=lambda i: i["published"] or "", reverse=True)
    xs.sort(key=lambda i: -(i.get("points") or 0))
    groups = [[i for i in xs if i["source"] == s] for s in SRC]
    out: list[dict] = []
    while len(out) < n and any(groups):
        for g in groups:
            if g and len(out) < n:
                out.append(g.pop(0))
    return out


def _t(s: str) -> str:
    return re.sub(r"[\[\]*`]", "", s or "").strip()  # keep the PDF markup intact


def _ago(days: int) -> str:
    """Recent only: '(today)', '(3 days ago)', '(last week)', '(2 weeks ago)'; older -> ''."""
    if not 0 <= days <= 30:
        return ""
    return (" (today)" if days == 0 else " (yesterday)" if days == 1 else
            f" ({days} days ago)" if days < 7 else " (last week)" if days < 14 else
            f" ({days // 7} weeks ago)")


def _d(iso: str | date | None, now: datetime | None = None) -> str:
    """'2026-09-17' -> '17 Sep 2026'; timestamps -> system local time with its zone:
    '12 Nov 2026, 14:32 India Standard Time (today)'."""
    if not iso:
        return "-"
    s = str(iso)
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return s
    now = now or datetime.now().astimezone()
    if "T" in s:
        d = d.astimezone(now.tzinfo) if d.tzinfo else d
        return f"{d.day} {d:%b %Y, %H:%M} {d.tzname() or ''}".rstrip() + \
            _ago((now.date() - d.date()).days)
    return f"{d.day} {d:%b %Y}" + _ago((now.date() - d.date()).days)


def _st(url: str, checks: dict) -> str:
    c = checks.get(url) or {}
    code = f" {c['code']}" if c.get("code") and c.get("status") != "live" else ""
    return f"{c.get('status', 'unchecked')}{code}"


def focus(a: dict, last: str, cur: str) -> str:
    """Inference rule: AI terms in both quarters are the ongoing focus, terms only in
    the current quarter are new. A stated rule over headline words, not a forecast."""
    tl, tc = a["themes_q"][last], a["themes_q"][cur]
    cont, new = [t for t in tc if t in tl], [t for t in tc if t not in tl]
    parts = ([f"continuing {', '.join(cont)} (in both quarters)"] if cont else []) + \
            ([f"newer: {', '.join(new)} (this quarter only)"] if new else [])
    return "; ".join(parts) or "too few AI headlines to tell"


def build(raw: dict, out_dir: Path, use_model: bool = False) -> dict:
    """use_model: add the checked local-model analysis (pipeline: on)."""
    col = raw["collected"]
    quarters = col["quarters"]
    labels = [q["label"] for q in quarters]
    cur_start = date.fromisoformat(quarters[-1]["start"])
    today = date.fromisoformat(quarters[-1]["end"]) - timedelta(days=1)
    ents = [col["company"], *col["competitors"]]
    metas = [raw["company"], *raw["competitors"]["overall"]]  # same order as collected
    an = [analyse(e, labels, cur_start, today) for e in ents]
    picks = [{lb: top(a["ai"], lb, 2 * TOP) for lb in labels[1:]} for a in an]

    urls: list[str] = []
    for e, m, p in zip(ents, metas, picks):
        urls += [v for v in (e.get("links") or {}).values() if v]
        urls += [x["url"] for x in (m.get("evidence") or [])[:3]]
        urls += [i[k] for its in p.values() for i in its for k in ("url", "link") if i.get(k)]
    low = [x for c in raw["competitors"].get("per_category") or [] for x in c["appendix"]
           if x.get("evidence")]
    urls += [x["evidence"][0]["url"] for x in low]
    checks = linkcheck.check_all(urls)
    alive = {u for u, c in checks.items() if c["status"] != "dead"}

    last, cur = labels[1], labels[2]
    nxt = cur.replace(" (to date)", "")
    company = raw["company"]
    # numbered sources: the top half cites [n], the bottom half lists them
    srcs: dict[str, tuple[int, str, str]] = {}

    def cite(url: str, title: str, kind: str) -> str:
        return f"[{srcs.setdefault(url, (len(srcs) + 1, title, kind))[0]}]"

    def did(i: dict) -> str:
        pts = f", {i['points']} points" if i.get("points") else ""
        refs = cite(i["url"], _t(i["title"]), SRC[i["source"]])
        if i.get("link") in alive:
            refs += cite(i["link"], _t(i["title"]), "linked article")
        return (f"- {_d(i['published'])} · {_t(i['title'])} · *{SRC[i['source']]}{pts}, "
                f"confidence {i['confidence']}* {refs}")

    rows = list(zip(ents, metas, an, picks))
    own = {company.get("label"), company.get("search_name")}
    secs, facts = [], []  # per-company sections (assigns [n]) and the model's fact sheet
    for n, (e, m, a, p) in enumerate(rows):
        o = a["outlook"]
        cats = ["direct rival" if c in own else c for c in m.get("categories") or []]
        head = [f"## {_t(e['label'])} (the company)" if n == 0 else f"## {n}. {_t(e['label'])}"]
        if n:
            ev = [x for x in (m.get("evidence") or []) if x["url"] in alive][:3]
            head.append(f"- **Competes in**: {_t(', '.join(cats) or 'named by you (--competitors)')}"
                        f" · confidence {m.get('confidence')}, named as a competitor on "
                        f"{len(m.get('domains') or [])} sites "
                        + "".join(cite(x["url"], _t(x["title"][:120]),
                                       f"competitor evidence via {x['engine']}") for x in ev)
                        + (" · different Wikidata class from the category (-1 level)"
                           if m.get("off_class") else "")
                        + (f" · same owner, merged: {_t(', '.join(m['also']))}"
                           if m.get("also") else ""))
        head.append("- **AI items / all items collected**: " + " · ".join(
            f"{lb} {a['counts'][lb]}/{a['total'][lb]}" for lb in labels))
        did_last = [i for i in p[last] if i["url"] in alive][:TOP]
        so_far = [i for i in p[cur] if i["url"] in alive][:3]
        body = [f"### What they did in AI in {last}"]
        body += [did(i) for i in did_last] or ["- no AI headlines found"]
        if so_far:
            body += [f"### So far in {cur}"] + [did(i) for i in so_far]
        rule = [f"- **Activity (rule)**: **{o['trend']}**, run-rate ~{o['projection']} AI items "
                f"vs {o['last']} in {last} (confidence {o['confidence']})",
                f"- **Likely focus (rule)**: {focus(a, last, cur)}"]
        secs.append((head, body, rule))
        role = "COMPANY (the subject)" if n == 0 else f"COMPETITOR {n}"
        facts.append(
            f"{role}: {e['label']}"
            + (f"; competes in: {', '.join(cats) or 'named by the user'}; competitor "
               f"confidence: {m.get('confidence')}" if n else "")
            + f"\nAI items {last}: {a['counts'][last]}; {cur}: {a['counts'][cur]}; "
            f"outlook rule for {nxt}: {o['trend']}, run-rate ~{o['projection']} "
            f"({o['confidence']} confidence); focus rule: {focus(a, last, cur)}\nHeadlines:\n"
            + "\n".join(f"[{srcs[i['url']][0]}] {_d(i['published'])} {SRC[i['source']]}: "
                        f"{_t(i['title'])}" for i in did_last + so_far))

    ana: dict = {}
    if use_model:
        from . import analysis
        ana = analysis.run("\n\n".join(facts), [e["label"] for e in ents],
                           {n: title for n, title, _ in srcs.values()}, company=company["label"],
                           today=_d(today), last=last, cur=cur, nxt=nxt)
        # audit trail: the model's reply as given, before the code checks
        (out_dir / "analysis_raw.txt").write_text(ana.get("raw") or "", encoding="utf-8")
    good = bool(ana) and not ana.get("error")
    tag = ("*Written by the local model (gpt-oss-20b) from the facts in this report; every "
           "sentence cites its sources and was checked by code for citations and numbers.*")

    md = [f"# AI competitor report: {_t(company['label'])}",
          f"Generated {_d(today)} · what each competitor did in AI in {last} and so far in "
          f"{cur}, and an outlook for {nxt}. Sources and method at the end."]
    if good and ana["summary"]:
        md += ["## Summary (analysis)", tag, prose(ana["summary"])]
    md += ["## How to use this report",
           "Start with the scorecard to see who is most active in AI and whose activity is "
           f"rising. Each company section then lists what it actually did in {last} (dated "
           "headlines you can open through the [n] source numbers at the end), followed by "
           "an outlook. Treat the analysis paragraphs as a starting point: they are only as "
           "good as the headlines they cite, so open the sources before relying on a claim."]

    md.append("## Key findings")
    if company.get("ambiguous_with"):
        md.append(f"- **Shared name**: without the website pin, Wikidata reads "
                  f"'{_t(company.get('search_name') or company['label'])}' as "
                  f"{_t(company['ambiguous_with'])}. "
                  + (f"News and search required the word '{_t(company['context'])}'."
                     if company.get("context") else
                     "News may mix in the other meaning; rerun with `--context <word>`."))
    user = raw["competitors"]["mode"] == "user"
    md.append(f"- **{len(ents) - 1} competitors named by you** (`--competitors`), not found "
              "or ranked by search." if user else
              f"- **{len(ents) - 1} of 10 competitors** reached medium or high confidence"
              + (" (live search found too few; low-confidence names are listed at the end, "
                 "or name them yourself with `--competitors`)." if len(ents) < 11 else "."))
    if len(ents) > 1:
        busiest = max(rows[1:], key=lambda r: r[2]["counts"][last])
        md.append(f"- **Most AI news in {last}**: {_t(busiest[0]['label'])} "
                  f"({busiest[2]['counts'][last]} AI items).")
    for word in ("up", "down", "new"):
        xs = [_t(e["label"]) for e, _, a, _ in rows if a["outlook"]["trend"] == word]
        if xs:
            md.append(f"- **AI activity {word} into {nxt}** (run-rate rule): {', '.join(xs)}.")

    md.append("## Scorecard")
    md.append(f"| # | Company | Competes in | Confidence | AI items {last} | {cur} | "
              f"{nxt} outlook |")
    md.append("|---|---|---|---|---|---|---|")
    for n, (e, m, a, _) in enumerate(rows):
        o = a["outlook"]
        cats = ["direct rival" if c in own else c for c in m.get("categories") or []]
        where = "(the company)" if n == 0 else _t(", ".join(cats) or "named by you")
        md.append(f"| {n or '-'} | {_t(e['label'])} | {where} | "
                  f"{'-' if n == 0 else m.get('confidence', '?')} | {a['counts'][last]} | "
                  f"{a['counts'][cur]} | {o['trend']}, ~{o['projection']} ({o['confidence']}) |")
    if good and ana["compare"]:
        md += [f"### {_t(company['label'])} vs its competitors (analysis)",
               prose(ana["compare"])]

    # caveats: facts the code knows, written as sentences (not model output)
    cav = []
    if user:
        cav.append("The competitors were named by you, so this report does not show whether "
                   "the web agrees they are the closest rivals.")
    elif len(ents) < 11:
        cav.append(f"Only {len(ents) - 1} of the 10 requested competitors were confirmed by "
                   "two or more independent sites, so the comparison is narrower than it looks.")
    if not user and not any(x.get("engine") == "brave" for m in metas[1:]
                            for x in m.get("evidence") or []):
        cav.append("Competitors were found with DuckDuckGo alone, which returns few results; "
                   "adding a Brave Search key usually finds more.")
    cav.append("The outlook counts headlines, not deals or revenue: a busy news quarter means "
               f"more AI coverage, not necessarily more AI work. The {nxt} figure extends the "
               "pace so far; it is not a forecast.")
    cav.append("Google News is dominated by press releases and finance sites, and company "
               "blogs and YouTube feeds only return their latest posts.")
    if use_model and good:
        cav.append("The analysis paragraphs come from a local language model. Code checked "
                   "that each sentence cites real sources and uses no number missing from them"
                   + (f" ({len(ana['dropped'])} sentences failed and were removed)"
                      if ana["dropped"] else "")
                   + " and that names it mentions appear in those sources, but not that "
                   "the reading is right: a word like 'agentic' can still be the model's "
                   "own gloss. High confidence needs two or more sources; predictions are "
                   "capped at medium."
                   + (f" The model's reply for {', '.join(_t(x) for x in ana['failed'])} could "
                      "not be read, so they have no analysis paragraphs."
                      if ana.get("failed") else ""))
    elif use_model:
        cav.append(f"The local model analysis was unavailable this run "
                   f"({_t(ana.get('error') or '')}), so only rule-based outlooks are shown.")
    md += ["## Caveats", " ".join(cav)]

    for (head, body, rule), e in zip(secs, ents):
        c = (ana.get("companies") or {}).get(e["label"], {}) if good else {}
        md += head + body
        if c.get("means"):
            md += ["### What this means (analysis)", prose(c["means"])]
        md.append(f"### Outlook for {nxt}")
        if c.get("next"):
            md.append(prose(c["next"]))
        md += rule

    # ---------------- everything below is sources and method ----------------
    md += ["---", "# Sources and method", "## How to read this",
          "- **AI item**: a headline/post whose title matches the AI keyword list (word "
          "boundary; the entity's own name is removed first, so 'Mistral AI' alone is not AI). "
          "Ambiguous terms (Gemini, Claude, Llama, Grok, Copilot, inference) count only "
          "next to another AI term or in model context ('Google Gemini', 'Claude 4').",
          "- **Item confidence**: high = first-party (own blog, Medium, YouTube); "
          "medium = third party naming the entity in the title (Hacker News, Google News).",
          "- **Competitor confidence**: distinct web domains naming it as a competitor "
          "(3+ high, 2 medium); names not on Wikidata, or whose Wikidata class differs "
          "from the category's, drop one level. Products with the same owner are merged.",
          f"- **Outlook for {nxt}**: straight-line run-rate of the quarter so far vs "
          f"{last} (more than 20% change = up/down), counting only sources with full "
          "history (blog/Medium feeds and YouTube RSS return just the latest posts). "
          "A stated rule, **not a forecast**.",
          f"- **Links** were fetched live at {_d(next(iter(checks.values()), {}).get('checked_at'))}: "
          "live, unverified (bot wall, rate limit, timeout or server error) or dead "
          "(404/410/no connection, removed from this report).",
          "- **Vision notes**: a local vision model reads a screenshot of each homepage "
          "and lists the AI products visible on it. Model output, **unverified**: check "
          "against the screenshot; never used in any count, confidence or outlook.",
          "- **Analysis paragraphs** (marked 'analysis'): written by the local model "
          "gpt-oss-20b from this report's own headlines and counts. Code drops any "
          "sentence without a valid [n] citation, without a confidence, or with a number "
          "not in its facts. Never used in any count, confidence or rule-based outlook.",
          f"- **Likely focus**: AI terms in {last} and {cur} headlines; terms in both "
          "quarters are 'continuing', terms only in the current one are 'newer'. A rule "
          "over headline words, not a forecast.",
          "- **[n]** numbers point to the numbered source list below."]

    md.append("## Profiles, sources by type and collection notes")
    for n, (e, m, a, _) in enumerate(rows):
        prof = [f"[Wikidata]({m['wikidata_url']})"] if m.get("wikidata_url") else []
        prof += [f"[{k}]({v}) ({_st(v, checks)})" for k, v in (e.get("links") or {}).items()
                 if v and v in alive]
        md.append(f"### {_t(e['label'])}")
        md.append(f"- **Profile**: {' · '.join(prof) or 'no official links found'} · "
                  f"searched as '{_t(e.get('searched_as') or '')}'")
        md.append("- **AI items by source** (" + " / ".join(labels) + "): " + (" · ".join(
            f"{SRC[s]} {'/'.join(map(str, v))}" for s, v in a["by_source"].items() if any(v))
            or "none"))
        if a["themes"]:
            md.append("- **AI terms, all quarters**: "
                      + ", ".join(f"{t} ({c})" for t, c in a["themes"]))
        o = a["outlook"]
        md.append(f"- **Outlook working**: {o['to_date']} AI items from full-history sources "
                  f"in {o['elapsed_days']} of {o['quarter_days']} days -> ~{o['projection']} "
                  f"vs {o['last']} in {last}")
        if e.get("social"):  # --social: profile-level, never in the counts
            md.append("- **Social profiles** (public page summary, not in counts): " + " · ".join(
                f"[{k}]({s['url']}): " + (", ".join(f"{v} {f.replace('_', ' ')}"
                                                    for f, v in s.items()
                                                    if f not in ("url", "available"))
                                          if s["available"] else f"unavailable ({s['reason']})")
                for k, s in e["social"].items()))
        if e.get("errors"):
            md.append("- *Collection errors*: " + "; ".join(_t(x) for x in e["errors"][:5]))

    vis = [(e, e["vision"]) for e, *_ in rows if e.get("vision")]
    if vis:  # model output, quarantined at the back in its own labelled block
        md.append("## Vision notes (local model, unverified)")
        for e, v in vis:
            md.append(f"### {_t(e['label'])}: {v.get('url') or '-'}")
            if v.get("text"):
                md.append(f"*Read from a homepage screenshot at {_d(v['at'])}; "
                          f"saved as screenshots/{Path(v['screenshot']).name}.*")
                md += [("- " + _t(ln.strip().lstrip("-•* "))) for ln in v["text"].splitlines()
                       if visual.keep(ln)][:15]
            else:
                md.append(f"- unavailable: {_t(v.get('error') or 'no output')}")

    low = [x for x in low if x["evidence"][0]["url"] in alive]
    if low:
        md.append("## Appendix: low-confidence names (one domain only)")
        md += [f"- {_t(x['label'])}: [{_t(x['evidence'][0]['title'][:120])}]"
               f"({x['evidence'][0]['url']}) ({_st(x['evidence'][0]['url'], checks)})"
               for x in low]
    md.append("## Sources")
    md += [f"- [{n}] [{title}]({url}) · {kind} ({_st(url, checks)})"
           for url, (n, title, kind) in srcs.items()]
    stats = Counter(c["status"] for c in checks.values())
    md.append("## Link check")
    md.append(f"- {len(checks)} links fetched: " + ", ".join(f"{k} {v}" for k, v in stats.items())
              + ". Google News links are Google redirect pages, so live means the redirect "
              "resolved, not that the publisher page did.")

    text = "\n".join(md)
    (out_dir / "report.md").write_text(text, encoding="utf-8")
    pdf = render_markdown_pdf(text, out_dir / "report.pdf",
                              f"AI competitor report: {company['label']}")
    return {"pdf": pdf, "checks": checks, "link_stats": dict(stats)}
