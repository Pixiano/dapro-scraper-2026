import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi.errors import RateLimitExceeded
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import cache, jobs, pdf, quota, worker
from .company import web as company_web
from .config import settings
from .llm import server as llm_server
from .ratelimit import limiter, rate_limit_handler

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    cache.init_db()
    quota.init_db()
    jobs.init_db()
    jobs.fail_unfinished()  # the queue is in memory: nothing will ever resume them
    worker.start()
    yield
    await worker.stop()
    llm_server.stop()


app = FastAPI(title="Entity Research Aggregator", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
# Localhost-only tool: refusing other Host headers closes DNS rebinding.
# (Tests: TestClient(app, base_url="http://localhost").)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])


# ---------------------------------------------------------------------- jobs

class JobCreate(BaseModel):
    links: list[str]
    entity_name: str | None = None


def _rel(base: Path, p: str) -> str:
    """Absolute artifact path → path relative to the job dir (for /file URLs).

    Keeps the user's filesystem layout out of API responses.
    """
    try:
        return Path(p).resolve().relative_to(base).as_posix()
    except (ValueError, OSError):
        return p


def _view_artifact(base: Path, a: dict) -> dict:
    a = dict(a)
    a["screenshots"] = [_rel(base, s) for s in a.get("screenshots") or []]
    a["images"] = [{**im, "local_path": _rel(base, im.get("local_path") or "")}
                   for im in a.get("images") or []]
    a["vision_notes"] = [
        {**n, "ref": _rel(base, n["ref"]) if n.get("kind") == "screenshot" else n.get("ref")}
        for n in a.get("vision_notes") or []
    ]
    return a


def _job_view(job: dict) -> dict:
    base = jobs.job_dir(job["id"]).resolve()
    artifacts = [_view_artifact(base, a)
                 for a in (job["result"] or {}).get("artifacts", [])]
    return {
        "id": job["id"], "status": job["status"], "inputs": job["inputs"],
        "error": job["error"], "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "artifacts": artifacts,
        "dossier_available": bool(job["dossier_path"]),
    }


@app.post("/api/jobs")
@limiter.limit(settings.rate_fresh)
async def api_create_job(request: Request, body: JobCreate = Body(...)):
    links = [ln.strip() for ln in body.links if ln and ln.strip()]
    if not links:
        return JSONResponse(status_code=400, content={"error": {
            "reason": "noLinks", "message": "Provide at least one link."}})
    for ln in links:
        if err := await run_in_threadpool(company_web.url_error, ln):
            return JSONResponse(status_code=400, content={"error": {
                "reason": "badLink", "message": f"{ln}: {err}"}})
    job_id = jobs.create({"links": links, "entity_name": body.entity_name})
    await worker.enqueue(job_id)
    return {"job_id": job_id, "status": jobs.QUEUED}


@app.get("/api/jobs")
@limiter.limit(settings.rate_cached)
async def api_list_jobs(request: Request):
    # only what frontend/app.js loadRecent() reads; artifacts come from /api/jobs/{id}
    return {"jobs": [{"id": j["id"], "status": j["status"], "inputs": j["inputs"],
                      "created_at": j["created_at"],
                      "dossier_available": bool(j["dossier_path"])}
                     for j in jobs.recent()]}


@app.get("/api/jobs/{job_id}")
@limiter.limit(settings.rate_cached)
async def api_get_job(request: Request, job_id: str):
    job = jobs.get(job_id)
    if job is None:
        return JSONResponse(status_code=404, content={"error": {
            "reason": "jobNotFound", "message": f"No job {job_id}."}})
    return _job_view(job)


@app.get("/api/jobs/{job_id}/dossier")
@limiter.limit(settings.rate_cached)
async def api_get_dossier(request: Request, job_id: str):
    job = jobs.get(job_id)
    if job is None or not job["dossier_path"]:
        return JSONResponse(status_code=404, content={"error": {
            "reason": "dossierNotReady", "message": "Dossier not available yet."}})
    return {"markdown": Path(job["dossier_path"]).read_text(encoding="utf-8")}


@app.get("/api/jobs/{job_id}/pdf")
@limiter.limit(settings.rate_cached)
async def api_get_pdf(request: Request, job_id: str):
    job = jobs.get(job_id)
    if job is None or not job["dossier_path"]:
        return JSONResponse(status_code=404, content={"error": {
            "reason": "dossierNotReady", "message": "Dossier not available yet."}})
    md = Path(job["dossier_path"]).read_text(encoding="utf-8")
    name = job["inputs"].get("entity_name") or "dossier"
    out = jobs.job_dir(job_id) / "dossier.pdf"
    await run_in_threadpool(pdf.render_markdown_pdf, md, out, name)
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "dossier"
    return FileResponse(str(out), media_type="application/pdf", filename=f"{slug}.pdf")


@app.get("/api/jobs/{job_id}/file")
async def api_job_file(job_id: str, path: str):
    """Serve a screenshot/image from a job folder. Path is relative to that
    folder and validated, so it can't escape via traversal."""
    # The id must be a real job (checked BEFORE job_dir, which creates the folder):
    # "..", made-up ids and absolute/UNC paths all get the same 404.
    if jobs.get(job_id) is None or Path(path).anchor:
        return JSONResponse(status_code=404, content={"error": {
            "reason": "fileNotFound", "message": "No such artifact file."}})
    base = jobs.job_dir(job_id).resolve()
    try:
        target = (base / path).resolve()
    except OSError:
        return JSONResponse(status_code=400, content={"error": {
            "reason": "badPath", "message": "Invalid path."}})
    if base not in target.parents or not target.is_file():
        return JSONResponse(status_code=404, content={"error": {
            "reason": "fileNotFound", "message": "No such artifact file."}})
    return FileResponse(str(target))


app.include_router(company_web.router)  # company reports: /company.html

# Frontend (mounted last so /api/* wins).
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
