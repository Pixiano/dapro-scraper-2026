"""Phase 4: AI filter, live link check, rule-based outlook, Markdown -> PDF.
No LLM: every number is a count, every inference a stated rule."""

import re
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from ..pdf import render_markdown_pdf
from . import linkcheck, visual

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


def _st(url: str, checks: dict) -> str:
    c = checks.get(url) or {}
    code = f" {c['code']}" if c.get("code") and c.get("status") != "live" else ""
    return f"{c.get('status', 'unchecked')}{code}"


def _item_line(i: dict, checks: dict, alive: set[str]) -> str:
    pts = f" · {i['points']} points" if i.get("points") else ""
    art = (f" · [article]({i['link']}) ({_st(i['link'], checks)})"
           if i.get("link") in alive else "")
    return (f"- {i['published']} · {SRC[i['source']]}{pts} · [{_t(i['title'])}]({i['url']})"
            f" ({_st(i['url'], checks)}){art} · confidence: {i['confidence']}")


def build(raw: dict, out_dir: Path) -> dict:
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
    md = [f"# AI competitor report: {_t(company['label'])}",
          f"Generated {today} · quarters {', '.join(labels)} · "
          f"competitors: {raw['competitors']['mode']} · counts and stated rules; the only "
          "model output is the labelled 'Vision notes' per company",
          "## How to read this",
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
          f"- **Links** were fetched live at {next(iter(checks.values()), {}).get('checked_at', '-')}: "
          "live, unverified (bot wall, rate limit, timeout or server error) or dead "
          "(404/410/no connection, removed from this report).",
          "- **Vision notes**: a local vision model reads a screenshot of each homepage "
          "and lists the AI products visible on it. Model output, **unverified**: check "
          "against the screenshot; never used in any count, confidence or outlook.",
          "## Summary"]
    if len(ents) == 1:  # say it, rather than pad the list with weak matches
        md.append("- **No competitor reached medium confidence** in live search (too little "
                  "coverage, or a name shared with something else). Low-confidence names, if "
                  "any, are in the appendix; name them yourself with `--competitors`.")
    for e, m, a in zip(ents, metas, an):
        o = a["outlook"]
        role = "the company" if m is company else f"{m.get('confidence', '?')} confidence"
        md.append(f"- **{_t(e['label'])}** ({role}): AI items "
                  + " · ".join(f"{lb} {a['counts'][lb]}" for lb in labels)
                  + f" → {nxt} run-rate ~{o['projection']} ({o['trend']} vs {last}, "
                  f"{o['confidence']} confidence)")

    for n, (e, m, a, p) in enumerate(zip(ents, metas, an, picks)):
        o = a["outlook"]
        md.append(f"## {_t(e['label'])} (the company)" if n == 0 else
                  f"## Competitor {n}: {_t(e['label'])}")
        if n:
            why = ", ".join(m.get("categories") or []) or "named by you (--competitors)"
            md.append(f"- **Competes in**: {_t(why)} · confidence: {m.get('confidence')} · "
                      f"{len(m.get('domains') or [])} domains"
                      + (" · different Wikidata class from the category (-1 level)"
                         if m.get("off_class") else "")
                      + (f" · same owner, merged: {_t(', '.join(m['also']))}"
                         if m.get("also") else ""))
            for x in [x for x in (m.get("evidence") or []) if x["url"] in alive][:3]:
                md.append(f"- Evidence: [{_t(x['title'])}]({x['url']}) "
                          f"({_st(x['url'], checks)}, via {x['engine']})")
        prof = [f"[Wikidata]({m['wikidata_url']})"] if m.get("wikidata_url") else []
        prof += [f"[{k}]({v}) ({_st(v, checks)})" for k, v in (e.get("links") or {}).items()
                 if v and v in alive]
        md.append(f"- **Profile**: {' · '.join(prof) or 'no official links found'} · "
                  f"searched as '{_t(e.get('searched_as') or '')}'")
        md.append("- **AI items / all items collected**: " + " · ".join(
            f"{lb} {a['counts'][lb]}/{a['total'][lb]}" for lb in labels))
        md.append("- **AI items by source** (" + " / ".join(labels) + "): " + " · ".join(
            f"{SRC[s]} {'/'.join(map(str, v))}" for s, v in a["by_source"].items() if any(v)))
        if e.get("social"):  # --social: profile-level, never in the counts above
            md.append("- **Social profiles** (public page summary, not in counts): " + " · ".join(
                f"[{k}]({s['url']}): " + (", ".join(f"{v} {f.replace('_', ' ')}"
                                                    for f, v in s.items()
                                                    if f not in ("url", "available"))
                                          if s["available"] else f"unavailable ({s['reason']})")
                for k, s in e["social"].items()))
        if a["themes"]:
            md.append("- **Recurring AI terms** (inference: what its AI news is about): "
                      + ", ".join(f"{t} ({c})" for t, c in a["themes"]))
        md.append(f"- **Outlook {nxt}** (rule, not a forecast): {o['to_date']} AI items from "
                  f"full-history sources in {o['elapsed_days']} of {o['quarter_days']} days → "
                  f"run-rate ~{o['projection']} vs {o['last']} in {last} → **{o['trend']}** "
                  f"(confidence {o['confidence']}: share of quarter elapsed and item count)")
        for lb, head in ((last, f"Last quarter ({last}): top AI items"),
                         (cur, f"This quarter so far ({cur}): top AI items")):
            items = [i for i in p[lb] if i["url"] in alive][:TOP]
            md.append(f"### {head}")
            md += [_item_line(i, checks, alive) for i in items] or ["- none found"]
        v = e.get("vision")
        if v:  # model output, quarantined in its own labelled block
            md.append(f"### Vision notes (local model, unverified) on {v.get('url') or '-'}")
            if v.get("text"):
                md.append(f"*Read from a homepage screenshot at {v['at']}; "
                          f"saved as screenshots/{Path(v['screenshot']).name}.*")
                md += [("- " + _t(ln.strip().lstrip("-•* "))) for ln in v["text"].splitlines()
                       if visual.keep(ln)][:15]
            else:
                md.append(f"- unavailable: {_t(v.get('error') or 'no output')}")
        if e.get("errors"):
            md.append("- *Collection errors*: " + "; ".join(_t(x) for x in e["errors"][:5]))

    low = [x for x in low if x["evidence"][0]["url"] in alive]
    if low:
        md.append("## Appendix: low-confidence names (one domain only)")
        md += [f"- {_t(x['label'])}: [{_t(x['evidence'][0]['title'])}]({x['evidence'][0]['url']})"
               f" ({_st(x['evidence'][0]['url'], checks)})" for x in low]
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
