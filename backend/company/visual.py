"""Vision notes: screenshot each entity's homepage, have the local vision model
(same Qwen model and llama.cpp server as the creator dossiers) list the AI
products/claims visible on it.

The ONLY model-generated part of a company report. Kept out of every count,
confidence and outlook, and labelled "unverified" in the report. Fail soft: no
GPU / no model / blocked page -> a note saying so, never a crash."""

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .. import vision
from ..llm import client
from . import UA_PLAIN

PROMPT = (
    "This is a screenshot of the homepage of {name}. List, as short bullet lines "
    "starting with '- ', every AI-related product, model, feature or announcement "
    "whose name or headline is visibly written on the page, quoting the visible "
    "text exactly. Skip navigation menus, buttons, cookie banners and customer logos. "
    "If none are visible, write '- none visible'. Then one final line "
    "starting 'POSITIONING:' saying in one sentence how the page presents the company. "
    "Do NOT use outside knowledge and do NOT guess: only what is readable in the image."
)
MAX_TOKENS = 400


def screenshot(url: str, path: Path) -> str | None:
    """Above-the-fold + a bit (1280x2000) of the main page only. -> error or None."""
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            page = b.new_context(user_agent=UA_PLAIN,
                                 viewport={"width": 1280, "height": 2000}).new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            page.wait_for_timeout(2000)  # client-side JS settle
            page.screenshot(path=str(path))
            b.close()
        return None
    except Exception as exc:
        return f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"


def keep(line: str) -> bool:
    """Rule-based filter on the model's lines: the 4B model lists menu items
    ("Research", "Learn more") despite the prompt. Keep headlines (3+ words),
    anything with a number or an AI term, and the POSITIONING line."""
    from .report import AI
    s = line.strip().lstrip("-•* ").strip()
    if re.search(r"cookies?", s, re.I):
        return False
    return bool(s) and (s.upper().startswith("POSITIONING") or len(s.split()) >= 3
                        or bool(re.search(r"\d", s)) or bool(AI.search(s)))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "x"


def notes(entities: list[dict], out_dir: Path) -> list[dict | None]:
    """entities: collected entities (label, links). -> one note per entity, same order."""
    shots = out_dir / "screenshots"
    shots.mkdir(parents=True, exist_ok=True)
    jobs = [(e, (e.get("links") or {}).get("website"), shots / f"{_slug(e['label'])}.png")
            for e in entities]
    # screenshots in parallel (browser-bound), model calls one by one (one GPU)
    with ThreadPoolExecutor(4) as ex:
        errs = list(ex.map(lambda j: screenshot(j[1], j[2]) if j[1] else "no website", jobs))
    out: list[dict | None] = []
    for (e, url, path), err in zip(jobs, errs):
        n: dict = {"url": url, "screenshot": str(path) if not err else None,
                   "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        if not err and vision.is_blank(path):
            err = "blank page (blocked or JS-only)"
        if not err:
            try:
                n["text"] = client.describe_image(
                    path, PROMPT.format(name=e.get("searched_as") or e["label"]),
                    max_tokens=MAX_TOKENS).strip() or None
            except Exception as exc:  # no llama-server / model files / GPU
                err = f"vision model unavailable: {type(exc).__name__}"
        n["error"] = err
        out.append(n)
    return out
