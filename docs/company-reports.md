# Company AI competitor reports: the full guide

The short version is in the [README](../README.md#company-ai-competitor-reports). This page has
everything else: every option, step-by-step how-tos, how to read the PDF, exactly how each number
is made, and what still goes wrong.

All commands are for Windows PowerShell, run from the project folder, with the virtual
environment from the README's [Quick start](../README.md#quick-start).

**Contents**

- [What you get](#what-you-get)
- [All options](#all-options)
- [How-tos](#how-tos)
- [How to read the report](#how-to-read-the-report)
- [How it works, step by step](#how-it-works-step-by-step)
- [Speed](#speed)
- [Known limits and wrong results](#known-limits-and-wrong-results)

---

## What you get

One command, one company:

```powershell
.venv\Scripts\python -m backend.company "Capgemini"
```

The tool then:

1. looks the company up on [Wikidata](https://www.wikidata.org) (the free, structured sister of
   Wikipedia) to get its official website, YouTube channel, blog and main products;
2. finds its competitors by web search, or takes the ones you name;
3. collects what each company did in AI over the last two quarters and this quarter so far, from
   Google News, Hacker News, the company's own blog and its YouTube channel; and, counted apart
   from those headlines, its releases and filings: Hugging Face models, new public GitHub repos
   and SEC current reports (8-K/6-K);
4. optionally screenshots each homepage and has a **local** vision model list the AI products on
   it;
5. checks the links the report will show, optionally has a **local** text model write short
   analysis paragraphs (checked sentence by sentence by code), and writes a PDF.

Everything lands in `data/company/<company>/<date>/`:

| File | What it is |
|---|---|
| `report.pdf` | The report. [Example: Capgemini, 13 pages](examples/capgemini-report.pdf). |
| `report.md` | The same report as Markdown. |
| `raw.json` | Every item, link, HTTP status and check time, for auditing. Written once before the report step and again after it, so if the link check, the model or the PDF fails, the collected data (and the searches it cost) is still there. |
| `analysis_raw.txt` | The text model's raw reply, before the code checks (only when analysis ran). |
| `screenshots/` | The homepage screenshots the vision model looked at (only when vision ran). |

A repeat run on the same day writes into the same folder.

---

## All options

This is the real `--help` output:

```text
usage: python -m backend.company [-h] [--url URL] [--context WORD]
                                 [--competitors "A,B"] [--categories N]
                                 [--per-category N] [--top N] [--no-vision]
                                 [--no-analysis] [--social] [--bw] [--open]
                                 [--fresh] [--debug]
                                 name

Build a PDF report on a company's AI activity vs its competitors.

positional arguments:
  name                 company name, e.g. Alphabet

options:
  -h, --help           show this help message and exit

which company and rivals:
  --url URL            official website; pins the company (startups, ambiguous
                       names)
  --context WORD       extra word required in news/search for a shared name,
                       e.g. "Gemini" --context crypto
  --competitors "A,B"  skip the competitor search: "OpenAI,Anthropic"
  --categories N       top N product categories to search (default 5)
  --per-category N     competitors per category (default 3)
  --top N              competitors overall (default 10)

what goes in the report:
  --no-vision          skip the homepage screenshots + local vision model
                       notes
  --no-analysis        skip the local-model analysis paragraphs (summary,
                       meaning, next quarter)
  --social             add Instagram/Facebook profile summaries (public page,
                       no login)

output and run:
  --bw                 black-and-white PDF (printing)
  --open               open the PDF when done
  --fresh              ignore cached search/Wikidata results (24 h / 30 d) for
                       this run
  --debug              show full tracebacks on errors

examples:
  python -m backend.company "Alphabet"
  python -m backend.company "Adaption Labs" --url https://adaptionlabs.ai
  python -m backend.company "OpenAI" --competitors "Anthropic,Mistral AI" --no-vision
  python -m backend.company "Gemini" --url https://www.gemini.com --context crypto --open

A full run takes 2-7 minutes; --competitors, --no-vision and --no-analysis make it faster.
Output: data/company/<company>/<date>/report.pdf (+ report.md, raw.json).

which option when:
  know the rivals?         --competitors "A,B"   (the most reliable list)
  startup or shared name?  --url https://...   (and --context WORD)
  slow, or low on memory?  --no-vision --no-analysis
  few rivals found?        put BRAVE_API_KEY=... in the .env file (without it, search is DuckDuckGo only)
```

Started with no arguments, the command prints this help instead of an error. At the start of a
run it prints the "which option when" tips for the options you did not use.

---

## How-tos

### Run a company report

```powershell
.venv\Scripts\python -m backend.company "Alphabet" --open
```

You will see one line per stage, with the elapsed time:

```text
Building the AI report for 'Alphabet' (usually 2-7 min; Ctrl+C to stop)
[0:00] 1/5 resolve      looking up 'Alphabet' on Wikidata
[0:00]                found Alphabet Inc. (Q20800404), 5 product categories
[0:00] 2/5 competitors  web search per category (~15 s each)
[0:02] 3/5 collect      news, blogs, YouTube for 7 companies
[0:20] 4/5 vision       homepage screenshots + local vision model
[0:46] 5/5 report       link checks, local-model analysis, PDF
```

(The competitor step took 2 seconds here because the search results were cached from an
earlier run.) When it finishes it prints a summary; the full example is in the
[README](../README.md#example-output). `--open` opens the PDF on Windows; without it, the last
line prints a PowerShell command (`ii "<path>"`) that opens it. Ctrl+C stops the run cleanly. An
error prints as one line; add `--debug` to see the full traceback.

### Name the competitors yourself

Web search finds fewer rivals than you would expect (see [Speed](#speed) and
[limits](#known-limits-and-wrong-results)). If you already know who the rivals are, say so. This
skips the search entirely and is the most reliable list you can get:

```powershell
.venv\Scripts\python -m backend.company "OpenAI" --competitors "Anthropic,Mistral AI,Meta"
```

Separate the names with commas. Each name is still looked up on Wikidata to find its website,
blog, YouTube channel and the other ids, and it resolves to the business the way searched names
do ("Meta" becomes Meta Platforms). A name that matches no business is still used as you typed
it. Names with punctuation work: `--competitors "AT&T,T-Mobile US"`.

### Report on a startup, or a name several companies share

A startup is often not on Wikidata, and a name like "Gemini" means a crypto exchange, a Google
model and a constellation. Pin the company by its website with `--url`:

```powershell
.venv\Scripts\python -m backend.company "Adaption Labs" --url https://adaptionlabs.ai
```

With `--url`, a Wikidata match is only trusted if its listed website matches. If the company
is not on Wikidata at all, it is searched as a startup, by its name.

If the name still pulls in other people's news, add `--context` with one word that the right
company's headlines contain. That word is then required in news and search results:

```powershell
.venv\Scripts\python -m backend.company "Gemini" --url https://www.gemini.com --context crypto
```

If the plain name means something else on Wikidata, the report says so in its caveats.

### Run faster, or on a PC low on memory

The two optional steps that load local models are the slow and memory-hungry ones. Skip both:

```powershell
.venv\Scripts\python -m backend.company "Capgemini" --no-vision --no-analysis
```

That takes about 10-60 seconds instead of 2-7 minutes. The report keeps every count, timeline,
confidence badge and rule-based outlook; it only loses the homepage notes and the written
analysis paragraphs. Adding `--competitors` also saves the search time.

### Get more competitors: set the Brave key

Without a key, competitor search uses DuckDuckGo only (through the `ddgs` package), which returns
few results. For Capgemini it found 2 competitors; with the Brave key the same search found 5 of
the 10 requested (Accenture, IBM, Deloitte, Tata Consultancy Services and Wipro, all high
confidence). When a key is set, Brave and DuckDuckGo are both searched and the results merged.

1. Sign up for the [Brave Search API](https://brave.com/search/api/). There is no free plan: a
   card is required, and the plan includes about $5 of searches a month.
2. Create an API key.
3. Add it to the `.env` file in the project folder (create the file if it does not exist; it is
   git-ignored):

   ```text
   BRAVE_API_KEY=your-key-here
   ```

Use one key only: several accounts break Brave's terms. While a Brave key is set, search results
are deliberately **not cached** (Brave's terms forbid storing results), so every run searches
again and costs searches.

### Print in black and white

```powershell
.venv\Scripts\python -m backend.company "Capgemini" --bw
```

Same report, in greys. Words and shapes carry the meaning that colour carried: the badges say
"high", "medium", "low", "up", "down" in text, and the timeline still uses filled dots, hollow
dots and squares.

### Use the browser page instead

1. Start the server: `.venv\Scripts\python -m uvicorn backend.main:app --port 8123`
2. Open `http://localhost:8123/company.html`.
3. Type the company. Optionally fill in the website, a context word, or your own comma-separated
   competitors. The checkboxes match the CLI flags (vision notes, analysis, black-and-white,
   Instagram/Facebook, fresh search).
4. Click **Build report**. The PDF opens when it is done.

Past reports are listed under the form with PDF and Markdown links. The page runs one report at
a time and always uses the default limits (5 categories, 3 per category, 10 overall). It shares
the local model server with creator dossiers, so a report waits for a running dossier job (and
the other way round), and the model server is stopped when the report ends, to free the GPU.
The website field must be an `http(s)` link to a public address. The command line is a separate
process that doesn't wait for the web app, so don't run both with the models at the same time.

![The company report page in the browser](screenshots/06-company-page.png)

### Add Instagram and Facebook numbers

```powershell
.venv\Scripts\python -m backend.company "OpenAI" --competitors "Anthropic" --social
```

Reads the follower and post numbers that Instagram and Facebook show on the public profile
page: one request per profile, no login, no posts. These numbers are shown per company and are
never part of the quarter counts. Expect them to be missing sometimes: both sites resist
anonymous visitors.

### Ignore the cache for one run

Search results are cached for 24 hours and Wikidata answers for 30 days (in `data/app.db`), so a
repeat run skips the slow lookups and does not trip DuckDuckGo's throttle. `--fresh` ignores the
cache for one run. News, feeds, releases and filings, and link checks are never cached.

---

## How to read the report

![Page 1 of the Capgemini report: summary, key findings and scorecard](screenshots/07-company-report-scorecard.png)

The report runs from short to detailed:

1. **Summary (analysis)**: a few sentences from the local text model, each with its source
   numbers and a confidence badge. Only shown when analysis ran.
2. **How to use this report** and **Key findings**: how many competitors were confirmed, who
   had the most AI news last quarter, and whose AI share of headlines is rising or falling.
3. **Scorecard**: one row per company (yours first, marked "-"). The columns:
   - **Competes in**: the product category where the rival was found (for Alphabet, "Gmail,
     Gemini, direct rival"). "direct rival" means a search for the company itself named it.
   - **Confidence**: how sure the tool is that this really is a competitor (see below).
   - **AI items Q3 2026** and **Q4 2026 (to date)**: how many headlines and posts about AI were
     found for each period.
   - **AI share of headlines**: the trend, for example "▼ down 27%, was 40% (medium)". Read it as:
     so far this quarter, 27% of this company's headlines were about AI; last quarter it was 40%;
     that is a fall of more than 20%, so "down". The badge in brackets is how much to trust that
     trend (see below).
4. **Releases and filings**: a second table under the scorecard, one row per company, with
   "last quarter / this quarter" counts for Hugging Face models, new public GitHub repos and SEC
   current reports (8-K/6-K). A "-" means the company has no id for that source (see below).
   Only shown when at least one company has one.
5. **Company vs its competitors (analysis)** and **Caveats** (the amber box): what the report
   cannot tell you this time, for example "Only 5 of the 10 requested competitors were
   confirmed".
6. **One section per company**: where it competes, item counts per quarter, a **Releases and
   filings** line (the same counts, how many of the GitHub repos are AI by keyword, and up to 2
   most-liked models, 2 most-starred repos and 3 newest filings, each cited), then a timeline.

![Page 2 of the Capgemini report: caveats and the company's AI timeline](screenshots/08-company-report-timeline.png)

The **timeline** lists the company's AI items oldest first, with a label where the quarter
changes. Each line has the date on the left ("17 Sep 2026", recent ones add "(3 days ago)"), a
mark on the line, then the headline, its source, a confidence badge and its source number:

- filled dot: the company's own blog or YouTube channel;
- hollow dot: a news headline;
- square: a Hacker News post.

After the timeline come **What this means (analysis)** and the **Outlook** for the current
quarter: the rule-based trend in words, and **Likely focus**, which compares the AI terms in the
headlines of both quarters ("continuing: agentic, generative; newer: ai agents").

At the back: **Sources and method** (the rules, restated in the report itself), each company's
profile links, the **Vision notes** (labelled unverified), the **Appendix** of low-confidence
names, the numbered **Sources**, and a link-check summary.

### The confidence badges

There are three different confidences, and they mean different things:

| Badge on | high | medium | low |
|---|---|---|---|
| **A competitor** | 3 or more different websites named it as a competitor | 2 websites | 1 website, or Wikidata does not list it as a company, brand or product. Low names go to the **Appendix**, not the main list. A name of a different Wikidata kind from the category drops one level. |
| **An item** (headline or post) | The company's own blog, Medium or YouTube | A news headline naming the company, or a Hacker News post linking to the company's own site | Any other Hacker News post (often *about* the company, not *by* it) |
| **A trend** | At least half the quarter has passed and 20+ AI items so far | At least a quarter of the quarter has passed and 5+ AI items | Less than that |
| **An analysis sentence** | Stated directly by several sources; code lowers a "high" with only one cited source to medium | One source, or a clear pattern | A reasonable guess from thin evidence. Predictions are never above medium. |

A product counts under its maker: if a site names ChatGPT as a rival, the competitor is OpenAI,
shown with "via ChatGPT". Products with the same owner are merged.

### The trend: AI's share of headlines

For each company: AI items so far this quarter as a share of all its headlines so far, against
the same share last quarter. More than 20% higher is **up**, more than 20% lower is **down**,
otherwise **flat**; **new** if there were no AI items last quarter, **none** if there are none
in either.

This is a stated rule, **not a forecast**. It counts headlines, not deals, products or revenue:
a quarter with a lot of other news (a big contract, a lawsuit) lowers the AI share even if the
company is doing just as much AI. It uses a share, not a count per day, because news search
returns more recent headlines than old ones, which made a per-day count say "up" for nearly
everyone. Only sources with full history count towards it: blog and Medium feeds and YouTube
RSS only return the latest posts.

### Releases and filings

These are dated and first-party, but they are not headlines, so they are counted on their own
and **never** feed the AI items, the AI share, the outlook or the likely-focus terms.

- **Hugging Face models**: every model counts as AI work. The organisation is the one on
  Wikidata; without one, the tool tries a Hub organisation named like the company's GitHub
  account or like the company, and accepts it only if Hugging Face has verified it and its
  display name names the company (an unverified lookalike, such as the one for Infosys, is
  refused). At most 500 models per organisation, newest first; no key needed.
- **GitHub repos**: new public repos of the company's account, forks left out, at most 400 per
  company per run. A repo is AI only if its name or description matches the same keyword list
  as the headlines. Without a token GitHub allows 60 requests an hour; `GITHUB_TOKEN` in `.env`
  raises that to 5,000.
- **SEC filings**: current reports, form 8-K, plus 6-K for foreign issuers such as Wipro. The
  8-K item codes are shown in words ("quarterly results", "officer or director change"); the
  exhibits item (9.01), which is on almost every filing, is left out. A filing never counts as
  AI.

The GitHub account and the SEC CIK come only from Wikidata. Hitting the Hugging Face or GitHub
cap is noted in the company's collection errors. The profile links (`huggingface`, `github`,
`sec`) are link-checked like the rest.

### The analysis paragraphs

Written by a local text model (gpt-oss-20b) that sees only the report's own headlines and
counts. Code then removes any sentence that has no valid `[n]` citation, no confidence, a number
that does not appear in the facts it was given, or names something (a product, a company) that
does not appear in the headlines it cites. For the number check, the bookkeeping in the facts
does not count as a number the model may use: a headline's source number `[n]`, its date, and
the "COMPETITOR 3" numbering are left out. The caveats say how many sentences were removed.

What the number check does **not** do: it doesn't check that a number belongs to the sources a
sentence cites. A number from any headline or count in the facts passes in any sentence, so "52%"
from one company's headline could be attached to another company and survive. A sentence that
passes still only means its sources say so: a word like "agentic" can still be the model's own
gloss, so open the sources before relying on a claim.

### The vision notes

A screenshot of each homepage, read by a local vision model that lists the AI products it sees.
Labelled **unverified**, kept in their own section at the back, and never used in any count,
confidence or trend.

---

## How it works, step by step

```mermaid
flowchart TD
    A["Company name<br/>(+ --url, --context)"] --> B["1. Resolve on Wikidata<br/>website, YouTube, blog,<br/>product categories"]
    B --> C{"--competitors given?"}
    C -- yes --> E["Look up each named rival<br/>on Wikidata"]
    C -- no --> D["2. Web search per category<br/>DuckDuckGo (+ Brave if key set)<br/>names checked on Wikidata"]
    D --> E
    E --> F["3. Collect AI news per quarter<br/>Google News, Hacker News,<br/>blogs, YouTube<br/>+ releases and filings:<br/>Hugging Face, GitHub, SEC"]
    F --> G{"vision on?"}
    G -- yes --> H["4. Homepage screenshots<br/>+ local vision model"]
    G -- no --> I
    H --> I["5. Live link check"]
    I --> J{"analysis on?"}
    J -- yes --> K["Local text model writes<br/>paragraphs; code checks<br/>every sentence"]
    J -- no --> L
    K --> L["PDF + Markdown + raw.json"]
```

| Step | Source | What happens |
|---|---|---|
| 1. Resolve | Wikidata, Wikipedia pageviews | Finds the company, its official links, and its products and subsidiaries. Categories with under 5% of the top category's Wikipedia pageviews are skipped (Netflix: no Millarworld). A company with subsidiaries but no products listed (Capgemini) is searched as itself, like a startup. Not on Wikidata: searched by name, website guessed from a web search, and the report's caveats say the website is a guess. |
| 2. Competitors | DuckDuckGo, plus the Brave Search API when `BRAVE_API_KEY` is set | Searches `<category> vs`, `vs <category>` and `<category> alternatives` for each category, and `"<company>" vs / competitors / alternatives` for the company itself, then reads "X vs Y" and "the best alternatives are A, B and C" from result titles and snippets. Every name is checked on Wikidata, four lookups at a time: a business with an English Wikipedia page is preferred, and a short name may match the start or end of a longer one ("Meta" finds Meta Platforms, "Bing" finds Microsoft Bing, "Apple" finds the company, not the fruit). Works described as an album, song, video game, film, college, university or ship don't count as businesses; on 468 cached names that removed 17 junk matches without losing a real company. The company's own products are dropped (Waze is Google's). Confidence = distinct websites naming it. A failing search engine is skipped, not fatal; a Wikidata error reply is reported and never cached. |
| 3. Collect | Google News RSS, Hacker News (Algolia API), the company's blog or Medium feed, YouTube (Data API if `YOUTUBE_API_KEY` is set, otherwise RSS) | Google News is asked month by month. Its feed returns at most about 100 items per query, so a month that comes back full is split in half and each half asked again, down to 3-day windows; a burst that gets HTTP 429 is paused and retried once. A news headline counts for a company only if it names the company; the " - Publisher" ending Google News adds doesn't count, so "... - Yahoo Finance" is not news about Yahoo. The blog feed is the one Wikidata lists, else the feed the blog or website page advertises in its HTML, else a few common feed paths. Items are put into last two quarters + this quarter to date. An item is "AI" if its headline matches a keyword list (see below). |
| 3. Collect (releases and filings) | Hugging Face API, GitHub REST API, SEC EDGAR; ids from Wikidata (Hugging Face P12201, GitHub P2037, SEC CIK P5531) | Model uploads, new public repos (forks left out, at most 400) and 8-K/6-K filings in the same quarters, counted apart from the headlines ([details](#releases-and-filings)). Hugging Face may also be found without a Wikidata id, if the Hub has verified the organisation. Many companies have no GitHub account or CIK on Wikidata, and then that column is "-". |
| 4. Vision | Playwright (headless Chromium) + local Qwen3.5-4B | Optional. Unverified notes only. |
| 5. Report | Live link check, local gpt-oss-20b | Links: *live*, *unverified* (paywall, bot wall, rate limit, timeout) or *dead* (404/410/no connection: removed). Optional analysis, checked by code. ReportLab writes the PDF. |

**The AI filter** is a keyword list matched on headline words, not a classifier. Besides the
general terms (AI, machine learning, LLM, agentic, self-driving and so on) it includes the names
of AI labs and products (OpenAI, Anthropic, DeepMind, watsonx, Apple Intelligence...), so
"Microsoft invests in Anthropic" counts as AI news for Microsoft. Ambiguous words (Gemini,
Claude, Llama, Grok, Copilot, inference) count only next to another AI term or in model context
("Google Gemini", "Claude 4"), so the Gemini crypto exchange and Claude Monet stay out; a GPU
mention counts only next to another term, so a gaming graphics card review stays out. The
company's own name is removed first, so "Mistral AI" alone is not AI news.

How well it works, measured on 200 stored headlines I labelled by hand (118 of them about AI):
precision 0.99 (before the list was widened: 1.00) and recall 0.87 (before: 0.85). That sample
had more AI headlines than real runs do; weighted to the real mix of headlines, recall is about
0.64 (before: 0.57). So roughly one AI headline in three is still missed, most often because the
AI product *is* the company (its own name is removed) or the headline uses a word the list
doesn't have.

**The models run locally** through llama.cpp's `llama-server`, the same setup as the creator
dossiers (see the README's [setup](../README.md#full-setup)). No AI API key is needed. If the
model server or the model files are missing, the vision section and the caveats say the models
were unavailable, and the rest of the report is built as normal. The server is stopped when the
run ends, to free the GPU.

**Wikimedia** (Wikidata and Wikipedia) and **SEC EDGAR** require a contact in the User-Agent of
every request. Put a URL or email in `.env` as `CONTACT_URL=`; it is sent only to those two,
every other site gets a plain User-Agent.

---

## Speed

| Run | Time |
|---|---|
| Full run (search, vision, analysis) | about 2-7 minutes |
| `--no-vision --no-analysis` | about 10-60 seconds |
| Competitor search, first run of the day | about 13-15 seconds per category with DuckDuckGo |
| Repeat run (search cached, no Brave key) | the search step takes seconds |

In the Alphabet example, the times were: resolve 0 s, competitors 2 s (cached), collect 18 s,
vision 26 s, report with analysis 113 s.

---

## Known limits and wrong results

- **Search finds few rivals.** DuckDuckGo alone returns about 8-20 results per query, so smaller
  categories often end with no medium-confidence competitor. Even with Brave, Capgemini got 5 of
  the 10 requested. Name the rivals with `--competitors` when you know them.
- **Some rivals are wrong.** Known cases from the test runs: Lectra appears as a Nestlé rival
  (it is a rival of a different company called Gerber); map apps with no AI news (OpenStreetMap,
  Organic Maps, OsmAnd) appear as Alphabet rivals, through Google Maps and Waze; the
  low-confidence appendix contains some junk names. Check the competitor list before trusting
  the comparisons.
- **The trend counts headlines.** Not deals, products, spending or revenue. It is a rule, not a
  forecast.
- **The AI filter misses about a third of AI headlines** (recall about 0.64 on the real mix, see
  [the AI filter](#how-it-works-step-by-step)), so counts are lower than the truth. The misses
  hit every company, but not evenly.
- **The loudest names are still sampled.** Even a 3-day window of Google News can be full for a
  company like OpenAI. Counts for those periods are a sample; this is recorded in the company's
  collection errors in `raw.json` and usually shown on its *Collection errors* line in the
  report (which lists the first five errors).
- **The analysis number check is loose.** A number from any headline is allowed in any analysis
  sentence, not only in one that cites that headline (see
  [the analysis paragraphs](#the-analysis-paragraphs)).
- **Feeds are short.** Medium feeds give the latest 10 posts, YouTube RSS the latest 15 (set
  `YOUTUBE_API_KEY` for full history). These are shown but not used for the trend.
- **Releases and filings depend on Wikidata ids.** SEC filings only appear for companies with a
  CIK on Wikidata, mostly US-listed ones, and some are missing (Infosys files with the SEC but
  has no CIK on Wikidata). Many consultancies (Infosys, Capgemini, TCS, Accenture, Wipro) have
  no GitHub account on Wikidata, so their GitHub column is "-", which means "not looked up",
  not "no repos".
- **GitHub without a token** allows 60 requests an hour, which a run with several busy
  companies can use up; the failure lands in the collection errors. Set `GITHUB_TOKEN`.
- **Google News links are redirect pages**, so "live" means the redirect worked, not that the
  publisher page did.
- **DuckDuckGo throttles** repeated runs. The cache helps; a throttled search is never cached.
- **Instagram and Facebook** often hide their numbers from anonymous visitors.
- **`--open` works on Windows only.** Elsewhere, open the printed path yourself.
