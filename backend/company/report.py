"""Phase 4: AI filter (keyword list), live link check, rule-based outlook, optional
local-model analysis (analysis.py, checked by code), Markdown -> PDF.
Every number is a count and every outlook a stated rule; the model's paragraphs
are marked "analysis" and never feed a count, a confidence or an outlook."""

import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from ..pdf import TAGS, render_markdown_pdf
from . import linkcheck, visual
from . import wikidata as wd
from .analysis import prose
from .collect import headline

# Word-boundary AI vocabulary. "AI" is case-sensitive ("ai" in other words is
# not AI); the entity's own name is removed first so "Mistral AI" / "OpenAI"
# headlines are not AI just by naming the company.
# AI labs and products count too ("invests in Anthropic", "watsonx", "Apple Intelligence").
# ponytail: keyword list, not a classifier; extend the list when misses show up.
# Measured on 200 stored headlines judged by hand (118 AI): precision 0.99, recall 0.87
# (the list before it: 1.00 and 0.85). Most misses are AI products that are the entity.
AI = re.compile(
    r"(?<![\w])(?:(?i:AI agents?|artificial intelligence|machine[- ]learning|deep[- ]learning|"
    r"neural (?:networks?|engine)|large language models?|language models?|llms?|"
    r"foundation models?|frontier models?|reasoning models?|generative|chatbots?|agentic|"
    r"(?:coding|voice|autonomous|managed) agents?|multi-agent|super[- ]?intelligence|"
    r"robotaxis?|self-driving|driverless|autonomous (?:vehicles?|cars?|driving)|"
    r"humanoid robots?|deepfakes?|prompt (?:engineering|injection)|system prompts?|"
    r"copilot|chatgpt|gpt(?:-?\d[\w.]*)?|gemini|claude|llama|grok|inference|"
    r"openai|anthropic|deepmind|hugging ?face|deepseek|qwen|midjourney|stable diffusion|"
    r"apple intelligence|watsonx|agentforce|codex|bedrock|sagemaker|firefly|notebooklm|"
    r"pytorch|tensorflow)|AI|A\.I\.|AGI|GenAI|ML|MLOps|xAI|MCP|TPUs?|GPUs?)"
    r"(?![\w])")  # hyphens allowed: "AI-powered", "Gen-AI"
SRC = {"hacker_news": "Hacker News", "google_news": "Google News", "blog": "Official blog",
       "medium": "Medium", "youtube": "YouTube"}
FIRST_PARTY = {"blog", "medium", "youtube"}
# Feeds that only return the latest N posts reach back into this quarter but
# not the last one: counting them would bias the outlook toward "up".
CAPPED = {"blog", "medium"}
# Releases and filings: dated and first-party, but not headlines. Counted apart, never in
# the AI items, the AI share or the outlook. Hugging Face models are AI by nature, a
# GitHub repo is AI by the keyword rule, an SEC filing never. source -> (counted as, cited as)
ACT = {"huggingface": ("Hugging Face models", "Hugging Face"),
       "github": ("new public GitHub repos", "GitHub"),
       "sec": ("SEC current reports (8-K/6-K)", "SEC EDGAR")}
BASE = {"ai", "a.i."}  # in every AI headline: says nothing about focus
TOP = 6  # items listed per quarter per entity


# Also the Gemini exchange, people named Claude, the animal, GitHub's non-AI news...
AMBIG = {"gemini", "claude", "llama", "grok", "copilot", "inference"}
GPU = {"gpu", "gpus"}  # gaming GPUs: counts only next to any other term ("Llama on GPUs")
_A = r"(?:gemini|claude|llama|grok|copilot)"
AMBIG_CTX = re.compile(
    rf"(?i:(?:google|anthropic|meta|xai|microsoft|github)(?:['’]s)?\W+{_A}(?![\w])"
    rf"|(?<![\w]){_A}\W+(?:\d|(?:pro|flash|ultra|nano|opus|sonnet|haiku|code|advanced|models?|api|cli|enterprise)"
    rf"(?![\w])))")
# two model names, or a model maker, in one headline: "via Claude, Gemini Prompts"
MODEL_TALK = re.compile(rf"(?i)(?<![\w])(?:google|anthropic|openai|xai|chatgpt|gpt-?\d[\w.]*)(?![\w])"
                        rf"|(?<![\w]){_A}(?![\w]).*(?<![\w]){_A}(?![\w])")


def ai_terms(title: str, *names: str | None) -> list[str]:
    """Names (search name, label) are removed first, with a trailing "AI":
    a competitor found as "Mistral" still strips "Mistral AI". Ambiguous terms
    count only with a non-ambiguous AI term or in model context (AMBIG_CTX);
    a GPU mention only with another term.
    ponytail: context list; a bare "Gemini tops charts" is missed"""
    ctx = AMBIG_CTX.search(title)  # before stripping: "Google Gemini" for Google itself
    # A company that shares its name with an AI model (Gemini the crypto exchange):
    # a headline naming the model's maker, a version, or other models is about the model.
    if any(n and n.lower() in AMBIG for n in names) and (ctx or MODEL_TALK.search(title)):
        return []
    for n in filter(None, names):
        # not "Apple Intelligence": there the name is part of an AI product's name
        title = re.sub(rf"(?<![\w]){re.escape(n)}(?!\s+Intelligence)(?:\s+AI)?(?![\w])", " ",
                       title, flags=re.I)
    terms = [m.lower() for m in AI.findall(title)]
    kinds = set(terms)
    return terms if ctx or kinds - AMBIG - GPU or (kinds & GPU and kinds & AMBIG) else []


def capped(i: dict) -> bool:
    return i["source"] in CAPPED or bool(i.get("note"))  # note = "RSS: latest 15 only"


def quarter_end(start: date) -> date:
    m = start.month + 2  # 0-based month index three months on
    return date(start.year + m // 12, m % 12 + 1, 1)


def outlook(last: int, to_date: int, last_all: int, cur_all: int,
            q_start: date, today: date) -> dict:
    """AI items so far, scaled to last quarter's headline volume, vs last quarter's
    AI items: has AI's share of the company's headlines moved? Scaled by volume,
    not by days: news search returns more recent headlines than old ones, so a
    day-based run-rate called nearly every company "up". A stated rule, not a forecast."""
    total = (quarter_end(q_start) - q_start).days
    elapsed = min((today - q_start).days + 1, total)
    proj = round(to_date * last_all / cur_all) if cur_all and last_all else to_date
    frac = elapsed / total
    conf = ("high" if frac >= .5 and to_date >= 20 else
            "medium" if frac >= .25 and to_date >= 5 else "low")
    trend = ("new" if proj else "none") if last == 0 else \
        "up" if proj > last * 1.2 else "down" if proj < last * .8 else "flat"
    return {"projection": proj, "trend": trend, "confidence": conf,
            "share_last": round(100 * last / last_all) if last_all else 0,
            "share_cur": round(100 * to_date / cur_all) if cur_all else 0,
            "elapsed_days": elapsed, "quarter_days": total}


def _share(o: dict) -> str:
    """What the trend is read from: '26% of headlines, was 40%'."""
    return f"{o['share_cur']}% of headlines, was {o['share_last']}%"


def releases(e: dict, last: str, cur: str) -> dict[str, list[dict]]:
    """source -> its items in the two quarters shown, the ones worth naming first:
    most liked / starred, newest filings. Only sources the company has an id for."""
    out = {}
    for s in ACT:
        if (e.get("links") or {}).get(s):
            its = [i for i in e["items"] if i["source"] == s and i["quarter"] in (last, cur)]
            out[s] = sorted(its, reverse=True, key=lambda i: (
                i.get("likes", i.get("stars", 0)), i["published"] or ""))
    return out


def repo_ai(i: dict, e: dict) -> bool:
    """'microsoft/copilot-chat-kit' + description, through the headline keyword rule."""
    text = re.sub(r"[-_]", " ", i["title"].split("/")[-1]) + " " + (i.get("description") or "")
    return bool(ai_terms(text, e.get("searched_as"), e.get("label")))


def analyse(e: dict, labels: list[str], cur_start: date, today: date) -> dict:
    ai = []
    site = urlparse((e.get("links") or {}).get("website") or "").netloc.removeprefix("www.")
    e = {**e, "items": [i for i in e["items"] if i["source"] not in ACT]}  # headlines only
    for it in e["items"]:
        terms = ai_terms(headline(it), e.get("searched_as"), e.get("label"))
        if terms:
            # a Hacker News post is often about the company, not by it: medium only
            # when it links to the company's own site
            about = it["source"] == "hacker_news" and not (site and site in (it.get("link") or ""))
            ai.append({**it, "terms": terms, "confidence": "high" if it["source"] in FIRST_PARTY
                       else "low" if about else "medium"})
    counts = {lb: sum(i["quarter"] == lb for i in ai) for lb in labels}
    full = {lb: sum(i["quarter"] == lb and not capped(i) for i in ai) for lb in labels}
    allf = {lb: sum(i["quarter"] == lb and not capped(i) for i in e["items"]) for lb in labels}
    return {"ai": ai, "counts": counts, "total": {lb: sum(i["quarter"] == lb for i in e["items"])
                                                  for lb in labels},
            "by_source": {s: [sum(i["quarter"] == lb and i["source"] == s for i in ai)
                              for lb in labels] for s in SRC},
            "themes": Counter(t for i in ai for t in set(i["terms"])).most_common(6),
            "themes_q": {lb: [t for t, _ in Counter(t for i in ai if i["quarter"] == lb
                                                    for t in set(i["terms"]) - BASE).most_common(5)]
                         for lb in labels},
            "outlook": {**outlook(full[labels[1]], full[labels[2]], allf[labels[1]],
                                  allf[labels[2]], cur_start, today),
                        "last": full[labels[1]], "to_date": full[labels[2]],
                        "last_all": allf[labels[1]], "cur_all": allf[labels[2]]}}


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


KIND = {"hacker_news": "hn", "google_news": "news"}  # timeline marks; else first-party


def _b(word: str) -> str:
    """Badge token: the PDF colours {{high}}, {{up}}... (B&W: words + symbols)."""
    return "{{" + word + "}}"


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


def build(raw: dict, out_dir: Path, use_model: bool = False, bw: bool = False) -> dict:
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
    rels = [releases(e, labels[1], labels[2]) for e in ents]

    urls: list[str] = []
    for e, m, p, r in zip(ents, metas, picks, rels):
        urls += [i["url"] for its in r.values() for i in its[:3]]
        urls += [v for v in (e.get("links") or {}).values() if v]
        urls += [x["url"] for x in (m.get("evidence") or [])[:3]]
        urls += [i[k] for its in p.values() for i in its for k in ("url", "link") if i.get(k)]
    # once each, and not the ones that made the main list through another category
    main = {m.get("qid") for m in raw["competitors"]["overall"]}
    low = list({x["qid"]: x for c in raw["competitors"].get("per_category") or []
                for x in c["appendix"] if x.get("evidence") and x["qid"] not in main}.values())
    urls += [x["evidence"][0]["url"] for x in low]
    checks = linkcheck.check_all(urls)
    alive = {u for u, c in checks.items() if c["status"] != "dead"}

    last, cur = labels[1], labels[2]
    nxt = cur.replace(" (to date)", "")
    company = raw["company"]
    # numbered sources: the top half cites [n], the bottom half lists them
    srcs: dict[str, tuple[int, str, str]] = {}
    nums: dict[tuple[str, str], int] = {}

    def cite(url: str, title: str, kind: str) -> str:
        if url not in srcs:  # the same article under a second URL keeps its number
            srcs[url] = (nums.setdefault((title, kind), len(nums) + 1), title, kind)
        return f"[{srcs[url][0]}]"

    def did(i: dict) -> str:
        """One vertical-timeline entry: '~ date|mark|sentence' (the PDF draws the dot)."""
        pts = f", {i['points']} points" if i.get("points") else ""
        refs = cite(i["url"], _t(i["title"]), SRC[i["source"]])
        if i.get("link") in alive:
            refs += cite(i["link"], _t(i["title"]), "linked article")
        return (f"~ {_d(i['published'])}|{KIND.get(i['source'], 'first')}|{_t(i['title'])} · "
                f"*{SRC[i['source']]}{pts}* {_b(i['confidence'])} {refs}")

    def n_q(its: list[dict]) -> str:
        return f"{sum(i['quarter'] == last for i in its)}/{sum(i['quarter'] == cur for i in its)}"

    def released(e: dict, r: dict[str, list[dict]]) -> str:
        """'Hugging Face models 22/5: microsoft/Mage-VL [7], ... · ...' (last/cur quarter)."""
        parts = []
        for s, its in r.items():
            what, kind = ACT[s]
            txt = f"{what} {n_q(its)}"
            if s == "github":
                txt += f" ({n_q([i for i in its if repo_ai(i, e)])} AI by keyword)"
            shown = [i for i in its[:3] if i["url"] in alive][:2 if s != "sec" else 3]
            refs = dict.fromkeys(f"{_t(i['title'])} {cite(i['url'], _t(i['title']), kind)}"
                                 for i in shown)  # two 6-Ks on one day share a title
            parts.append(txt + (": " + ", ".join(refs) if refs else ""))
        return " · ".join(parts)

    rows = list(zip(ents, metas, an, picks))
    own = {company.get("label"), company.get("search_name"),
           wd.base_name(company.get("label") or "")}
    want = raw["competitors"].get("top") or 10  # --top
    listed = 0  # AI headlines shown, all companies
    secs, facts = [], []  # per-company sections (assigns [n]) and the model's fact sheet
    for n, (e, m, a, p) in enumerate(rows):
        o = a["outlook"]
        # rivals only: the company's own "categories" are Wikidata dicts, not names
        cats = ["direct rival" if c in own else c
                for c in (m.get("categories") or [] if n else [])]
        head = [f"## {_t(e['label'])} (the company)" if n == 0 else f"## {n}. {_t(e['label'])}"]
        if n and not cats:
            head.append("- **Competes in**: named by you (`--competitors`)")
        elif n:
            ev = [x for x in (m.get("evidence") or []) if x["url"] in alive][:3]
            head.append(f"- **Competes in**: {_t(', '.join(cats))}"
                        f" · confidence {_b(m['confidence']) if m.get('confidence') in TAGS else m.get('confidence')}, "
                        f"named as a competitor on {len(m.get('domains') or [])} sites "
                        + "".join(cite(x["url"], _t(x["title"][:120]),
                                       f"competitor evidence via {x['engine']}") for x in ev)
                        + (f" · via {_t(', '.join(m['via']))}" if m.get("via") else "")
                        + (" · different Wikidata class from the category (-1 level)"
                           if m.get("off_class") else "")
                        + (f" · same owner, merged: {_t(', '.join(m['also']))}"
                           if m.get("also") else ""))
        head.append("- **AI items / all items collected**: " + " · ".join(
            f"{lb} {a['counts'][lb]}/{a['total'][lb]}" for lb in labels))
        if rels[n]:
            head.append(f"- **Releases and filings** ({last} / {cur}): {released(e, rels[n])}")
        did_last = [i for i in p[last] if i["url"] in alive][:TOP]
        so_far = [i for i in p[cur] if i["url"] in alive][:3]
        listed += len(did_last + so_far)
        # one vertical timeline, oldest first, a label row where the quarter changes
        body = [f"### What they did in AI: {last} to today"]
        if not did_last:
            body.append(f"- no AI headlines found for {last}")
        for q, its in ((last, did_last), (cur, so_far)):
            if its:
                body += [f"~ # {q}"] + [did(i) for i in sorted(its, key=lambda i: i["published"] or "")]
        rule = [f"- **Activity (rule)**: {_b(o['trend'])}: AI is {o['share_cur']}% of its "
                f"headlines so far this quarter ({o['to_date']} of {o['cur_all']}), "
                f"{o['share_last']}% in {last} ({o['last']} of {o['last_all']}) · "
                f"confidence {_b(o['confidence'])}",
                f"- **Likely focus (rule)**: {focus(a, last, cur)}"]
        secs.append((head, body, rule))
        role = "COMPANY (the subject)" if n == 0 else f"COMPETITOR {n}"
        facts.append(
            f"{role}: {e['label']}"
            + (f"; competes in: {', '.join(cats) or 'named by the user'}; competitor "
               f"confidence: {m.get('confidence')}" if n else "")
            + f"\nAI items {last}: {a['counts'][last]}; {cur}: {a['counts'][cur]}; "
            f"outlook rule for {nxt}: {o['trend']}, AI is {_share(o)} in {last} "
            f"({o['confidence']} confidence); focus rule: {focus(a, last, cur)}\nHeadlines:\n"
            + "\n".join(f"[{srcs[i['url']][0]}] {_d(i['published'])} {SRC[i['source']]}: "
                        f"{_t(i['title'])}" for i in did_last + so_far))

    ana: dict = {}
    asked = use_model
    use_model = use_model and listed > 0  # nothing to analyse: the model only invents
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
    if not listed:
        md.append(f">! No AI headlines were found for {_t(company['label'])}"
                  + (" or its competitors" if len(ents) > 1 else "") + f" in {last} or {cur} ("
                  f"{sum(sum(a['total'].values()) for a in an)} headlines collected in all), so there "
                  "is no analysis. Check the spelling, pin the company with `--url https://...`, "
                  "or name its rivals with `--competitors`.")
    if good and ana["summary"]:
        md += ["## Summary (analysis)", "> " + tag, "> " + prose(ana["summary"])]
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
              f"- **{len(ents) - 1} of {want} competitors** reached medium or high confidence"
              + (" (live search found too few; low-confidence names are listed at the end, "
                 "or name them yourself with `--competitors`)." if len(ents) <= want else "."))
    if len(ents) > 1:
        busiest = max(rows[1:], key=lambda r: r[2]["counts"][last])
        md.append(f"- **Most AI news in {last}**: {_t(busiest[0]['label'])} "
                  f"({busiest[2]['counts'][last]} AI items).")
    for word, says in (("up", "AI's share of headlines is rising"),
                       ("down", "AI's share of headlines is falling"),
                       ("new", "First AI headlines this quarter")):
        xs = [_t(e["label"]) for e, _, a, _ in rows if a["outlook"]["trend"] == word]
        if xs:  # rstrip: "Alphabet Inc." ends the list
            md.append(f"- **{says}** ({cur} vs {last}): " + ", ".join(xs).rstrip(".") + ".")

    md.append("## Scorecard")
    md.append(f"| # | Company | Competes in | Confidence | AI items {last} | {cur} | "
              "AI share of headlines |")
    md.append("|---|---|---|---|---|---|---|")
    for n, (e, m, a, _) in enumerate(rows):
        o = a["outlook"]
        # rivals only: the company's own "categories" are Wikidata dicts, not names
        cats = ["direct rival" if c in own else c
                for c in (m.get("categories") or [] if n else [])]
        where = "(the company)" if n == 0 else _t(", ".join(cats) or "named by you")
        md.append(f"| {n or '-'} | {_t(e['label'])} | {where} | "
                  f"{_b(m['confidence']) if n and m.get('confidence') in TAGS else '-'} | "
                  f"{a['counts'][last]} | {a['counts'][cur]} | "
                  f"{_b(o['trend'])} {o['share_cur']}%, was {o['share_last']}% "
                  f"({_b(o['confidence'])}) |")
    if any(rels):  # counted apart from headlines: see "Releases and filings" in How to read this
        md += [f"### Releases and filings ({last} / {cur})",
               "| Company | " + " | ".join(what for what, _ in ACT.values()) + " |",
               "|---|" + "---|" * len(ACT)]
        md += [f"| {_t(e['label'])} | " + " | ".join(n_q(r[s]) if s in r else "-" for s in ACT)
               + " |" for e, r in zip(ents, rels)]
    if good and ana["compare"]:
        md += [f"### {_t(company['label'])} vs its competitors (analysis)",
               "> " + prose(ana["compare"])]

    # caveats: facts the code knows, written as sentences (not model output)
    cav = []
    if user:
        cav.append("The competitors were named by you, so this report does not show whether "
                   "the web agrees they are the closest rivals.")
    elif len(ents) <= want:
        cav.append(f"Only {len(ents) - 1} of the {want} requested competitors were confirmed by "
                   "two or more independent sites, so the comparison is narrower than it looks.")
    if not user and not any(x.get("engine") == "brave" for m in metas[1:]
                            for x in m.get("evidence") or []):
        cav.append("Competitors were found with DuckDuckGo alone, which returns few results; "
                   "adding a Brave Search key usually finds more.")
    if company.get("website_guessed"):
        cav.append(f"The company's website ({_t(company['links']['website'])}) is not on "
                   "Wikidata: it is a guess from a web search, and the blog feed and vision "
                   "notes come from it. Pin the right one with `--url https://...`.")
    cav.append("The trend compares AI's share of each company's headlines in the quarter so far "
               f"with {last}. It counts headlines, not deals or revenue, so a quarter with a lot "
               "of other news (a big contract, a lawsuit) lowers the AI share without any less "
               "AI work. It is not a forecast.")
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
    if asked and not listed:
        cav.append("The model analysis was skipped: there were no AI headlines to analyse.")
    md += ["## Caveats", ">! " + " ".join(cav)]

    for (head, body, rule), e in zip(secs, ents):
        c = (ana.get("companies") or {}).get(e["label"], {}) if good else {}
        md += head + body
        if c.get("means"):
            md += ["### What this means (analysis)", "> " + prose(c["means"])]
        md.append(f"### Outlook for {nxt}")
        if c.get("next"):
            md.append("> " + prose(c["next"]))
        md += rule

    # ---------------- everything below is sources and method ----------------
    md += ["---", "# Sources and method", "## How to read this",
          "- **AI item**: a headline/post whose title matches the AI keyword list (word "
          "boundary; the entity's own name is removed first, so 'Mistral AI' alone is not AI). "
          "The names of AI labs and products count (OpenAI, Anthropic, watsonx). "
          "Ambiguous terms (Gemini, Claude, Llama, Grok, Copilot, inference) count only "
          "next to another AI term or in model context ('Google Gemini', 'Claude 4'); "
          "a GPU mention only next to another term.",
          "- **Item confidence**: high = first-party (own blog, Medium, YouTube); "
          "medium = a news headline naming the company, or a Hacker News post linking to "
          "the company's own site; low = other Hacker News posts, which are often about "
          "the company rather than by it.",
          "- **Competitor confidence**: distinct web domains naming it as a competitor "
          "(3+ high, 2 medium). A name that Wikidata does not list as a company, brand or "
          "product stays low; a different Wikidata class from the category's drops one "
          "level. A product is listed under the company that makes it ('via ChatGPT'); "
          "products with the same owner are merged.",
          f"- **Outlook for {nxt}**: AI's share of the company's headlines so far this "
          f"quarter vs its share in {last} (more than 20% change = up/down). A share, not "
          "a count per day, because news search returns more recent headlines than old "
          "ones, which made a per-day count rise for nearly every company. Only sources "
          "with full history are counted (blog/Medium feeds and YouTube RSS return just "
          "the latest posts). A stated rule, **not a forecast**.",
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
          "- **Releases and filings**: Hugging Face model uploads, new public GitHub repos "
          "(forks left out) and SEC current reports (8-K; 6-K for foreign issuers), counted "
          "per quarter as last/this. Dated and first-party, but not headlines, so they are "
          "never part of the AI items, the AI share or the outlook. Every Hugging Face model "
          "is AI work; a repo is AI only if its name or description matches the AI keyword "
          "list; a filing is never AI news (its 8-K item codes name what was reported). The "
          "Hugging Face organisation is the one on Wikidata, or a Hub organisation named "
          "like the company's GitHub account or its name that Hugging Face has verified "
          "and whose display name names the company. GitHub and SEC ids come from Wikidata "
          "only. '-' means no id was found.",
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
        md.append(f"- **Outlook working**: {o['to_date']} AI of {o['cur_all']} headlines so far "
                  f"(day {o['elapsed_days']} of {o['quarter_days']}) vs {o['last']} of "
                  f"{o['last_all']} in {last}, full-history sources only")
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
        md.append("## Appendix: low-confidence names (one site only, or not confirmed "
                  "as a company on Wikidata)")
        md += [f"- {_t(x['label'])}: [{_t(x['evidence'][0]['title'][:120])}]"
               f"({x['evidence'][0]['url']}) ({_st(x['evidence'][0]['url'], checks)})"
               for x in low]
    md.append("## Sources")
    first = {n: (url, title, kind) for url, (n, title, kind) in reversed(srcs.items())}
    md += [f"- [{n}] [{title}]({url}) · {kind} ({_st(url, checks)})"
           for n, (url, title, kind) in sorted(first.items())]
    stats = Counter(c["status"] for c in checks.values())
    md.append("## Link check")
    md.append(f"- {len(checks)} links fetched: " + ", ".join(f"{k} {v}" for k, v in stats.items())
              + ". Google News links are Google redirect pages, so live means the redirect "
              "resolved, not that the publisher page did.")

    text = "\n".join(md)
    (out_dir / "report.md").write_text(text, encoding="utf-8")
    pdf = render_markdown_pdf(text, out_dir / "report.pdf",
                              f"AI competitor report: {company['label']}", bw=bw)
    return {"pdf": pdf, "checks": checks, "link_stats": dict(stats)}
