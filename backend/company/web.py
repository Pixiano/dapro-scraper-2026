"""Phase 7: localhost API for the company pipeline. The page is
frontend/company.html; reports are the same files the CLI writes."""

import re
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .pipeline import OUT, run

router = APIRouter(prefix="/api/company")
# One run at a time: DuckDuckGo throttles parallel searches anyway.
# ponytail: in-memory job table, lost on restart; reports themselves persist on disk
_pool = ThreadPoolExecutor(1)
_runs: dict[str, dict] = {}
SAFE = re.compile(r"^[a-z0-9-]+$")
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class RunIn(BaseModel):
    name: str
    url: str | None = None
    competitors: str | None = None
    social: bool = False
    vision: bool = True
    analysis: bool = True
    fresh: bool = False
    context: str | None = None


def _work(rid: str, body: RunIn) -> None:
    _runs[rid]["status"] = "running"
    try:
        names = [n.strip() for n in (body.competitors or "").split(",") if n.strip()]
        r = run(body.name.strip(), body.url or None, names=names, social=body.social,
                vision=body.vision, fresh=body.fresh, context=body.context,
                analysis=body.analysis)
        d = r["out_dir"]
        _runs[rid].update(status="done", report=f"{d.parent.name}/{d.name}",
                          timing=r["timing_s"], links=r["link_stats"],
                          competitors=[c["label"] for c in r["competitors"]["overall"]])
    except Exception as exc:
        traceback.print_exc()
        _runs[rid].update(status="error", error=f"{type(exc).__name__}: {exc}")


@router.post("/runs")
def start(body: RunIn):
    if not body.name.strip():
        return JSONResponse(status_code=400, content={"error": "name is required"})
    rid = uuid.uuid4().hex[:12]
    _runs[rid] = {"id": rid, "name": body.name.strip(), "status": "queued"}
    _pool.submit(_work, rid, body)
    return _runs[rid]


@router.get("/runs/{rid}")
def status(rid: str):
    return _runs.get(rid) or JSONResponse(status_code=404, content={"error": "no such run"})


@router.get("/reports")
def reports():
    return sorted(({"company": p.parent.parent.name, "date": p.parent.name,
                    "report": f"{p.parent.parent.name}/{p.parent.name}"}
                   for p in OUT.glob("*/*/report.pdf")),
                  key=lambda r: r["date"], reverse=True)


@router.get("/reports/{company}/{day}/{fname}")
def report_file(company: str, day: str, fname: str):
    # path parts are validated, so nothing outside data/company is reachable
    if not (SAFE.match(company) and DAY.match(day) and fname in ("report.pdf", "report.md")):
        return JSONResponse(status_code=400, content={"error": "bad path"})
    p = OUT / company / day / fname
    if not p.is_file():
        return JSONResponse(status_code=404, content={"error": "not found"})
    return FileResponse(str(p), media_type="application/pdf" if fname.endswith(".pdf")
                        else "text/markdown; charset=utf-8")
