import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi.errors import RateLimitExceeded

import re

from . import cache, jobs, pdf, quota, worker
from .config import settings
from .instagram import service as instagram_service
from .llm import server as llm_server
from .quota import QuotaExhausted
from .ratelimit import limiter, rate_limit_handler
from .youtube import client, resolver, service
from .youtube.client import YouTubeError

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    cache.init_db()
    quota.init_db()
    jobs.init_db()
    worker.start()
    yield
    await worker.stop()
    llm_server.stop()
    await client.aclose()


app = FastAPI(title="Entity Research Aggregator", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)


@app.exception_handler(YouTubeError)
async def youtube_error_handler(request: Request, exc: YouTubeError):
    return JSONResponse(
        status_code=exc.status,
        content={"error": {"reason": exc.reason, "message": exc.message},
                 "quota": quota.status()},
    )


@app.exception_handler(QuotaExhausted)
async def quota_error_handler(request: Request, exc: QuotaExhausted):
    return JSONResponse(
        status_code=429,
        content={"error": {"reason": "quotaBudgetExhausted", "message": str(exc)},
                 "quota": quota.status()},
    )


def envelope(data, cached: bool, fetched_at: str) -> dict:
    return {"data": data, "cached": cached, "fetched_at": fetched_at,
            "quota": quota.status()}


def cost_gate(resource: str) -> JSONResponse:
    cost = quota.cost_of(resource)
    return JSONResponse(
        status_code=400,
        content={"error": {
            "reason": "confirmationRequired",
            "message": f"{resource}.list costs {cost} quota units. "
                       f"Repeat the request with confirm=true to proceed."},
            "cost": cost, "quota": quota.status()},
    )


# ---------------------------------------------------------------- resolution

@app.get("/api/resolve")
@limiter.limit(settings.rate_fresh)
async def api_resolve(request: Request, q: str, refresh: bool = False):
    data, cached, fetched = await resolver.resolve(q, force=refresh)
    return envelope(data, cached, fetched)


# ------------------------------------------------------------------- channel

@app.get("/api/channel/{channel_id}")
@limiter.limit(settings.rate_fresh)
async def api_channel(request: Request, channel_id: str, refresh: bool = False):
    data, cached, fetched = await service.channel_snapshot(channel_id, force=refresh)
    return envelope(data, cached, fetched)


@app.get("/api/channel/{channel_id}/videos")
@limiter.limit(settings.rate_cached)
async def api_channel_videos(request: Request, channel_id: str,
                             page_token: str | None = None, refresh: bool = False):
    data, cached, fetched = await service.channel_videos(channel_id, page_token, force=refresh)
    return envelope(data, cached, fetched)


@app.get("/api/channel/{channel_id}/activities")
@limiter.limit(settings.rate_cached)
async def api_activities(request: Request, channel_id: str, refresh: bool = False):
    data, cached, fetched = await service.activities(channel_id, force=refresh)
    return envelope(data, cached, fetched)


@app.get("/api/channel/{channel_id}/subscriptions")
@limiter.limit(settings.rate_cached)
async def api_subscriptions(request: Request, channel_id: str, refresh: bool = False):
    data, cached, fetched = await service.subscriptions(channel_id, force=refresh)
    return envelope(data, cached, fetched)


# --------------------------------------------------------------------- video

@app.get("/api/video/{video_id}")
@limiter.limit(settings.rate_fresh)
async def api_video(request: Request, video_id: str, refresh: bool = False):
    data, cached, fetched = await service.video_detail(video_id, force=refresh)
    return envelope(data, cached, fetched)


@app.get("/api/video/{video_id}/comments")
@limiter.limit(settings.rate_cached)
async def api_comments(request: Request, video_id: str, page_token: str | None = None,
                       order: str = "relevance", refresh: bool = False):
    data, cached, fetched = await service.comments(video_id, page_token, order, force=refresh)
    return envelope(data, cached, fetched)


@app.get("/api/comments/{parent_id}/replies")
@limiter.limit(settings.rate_cached)
async def api_replies(request: Request, parent_id: str, page_token: str | None = None,
                      refresh: bool = False):
    data, cached, fetched = await service.comment_replies(parent_id, page_token, force=refresh)
    return envelope(data, cached, fetched)


@app.get("/api/video/{video_id}/captions")
@limiter.limit(settings.rate_expensive)
async def api_captions(request: Request, video_id: str, confirm: bool = False,
                       refresh: bool = False):
    if not confirm:
        return cost_gate("captions")
    data, cached, fetched = await service.captions(video_id, force=refresh)
    return envelope(data, cached, fetched)


# -------------------------------------------------------------------- extras

@app.get("/api/search")
@limiter.limit(settings.rate_expensive)
async def api_search(request: Request, q: str, type: str = "channel",
                     confirm: bool = False, refresh: bool = False):
    if not confirm:
        return cost_gate("search")
    data, cached, fetched = await service.search(q, type, force=refresh)
    return envelope(data, cached, fetched)


@app.get("/api/quota")
async def api_quota():
    return {"quota": quota.status()}


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
    job_id = jobs.create({"links": links, "entity_name": body.entity_name})
    await worker.enqueue(job_id)
    return {"job_id": job_id, "status": jobs.QUEUED}


@app.get("/api/jobs")
@limiter.limit(settings.rate_cached)
async def api_list_jobs(request: Request):
    return {"jobs": [_job_view(j) for j in jobs.recent()]}


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


@app.get("/api/instagram/{username}")
@limiter.limit(settings.rate_fresh)
async def api_instagram(request: Request, username: str, refresh: bool = False):
    if not settings.enable_instagram:
        return JSONResponse(
            status_code=503,
            content={"error": {
                "reason": "instagramDisabled",
                "message": "Instagram tier is off. Set ENABLE_INSTAGRAM=true in .env "
                           "and install instaloader."}},
        )
    key = f"ig:{username.strip().lower()}"
    if not refresh:
        hit = cache.get(key)
        if hit is not None:
            payload, fetched_at = hit
            return envelope(payload, True, fetched_at)
    data = await run_in_threadpool(instagram_service.fetch_profile, username.strip())
    cache.set(key, "list" if data.get("available") else "ig_fail", data)
    return envelope(data, False, cache._iso(time.time()))


# Frontend (mounted last so /api/* wins).
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
