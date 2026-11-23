"""Job pipeline: collect → analyze (vision) → synthesize → document.

Collectors are blocking functions (sync Playwright, one Chromium each), so they
run in worker threads, settings.collect_concurrency at a time. Vision and synthesis
run one call at a time: the GPU holds one model. Any exception ends the job in
status "error"; a collector that raises only costs its own source."""

import asyncio

from fastapi.concurrency import run_in_threadpool

from . import jobs, synthesis, vision
from .collectors import news
from .collectors.base import NEWS, SourceArtifact, classify_url, get_collector
from .config import settings


async def _collect(job_id: str, inputs: dict) -> list[dict]:
    jobs.update(job_id, status=jobs.COLLECTING)
    links = inputs.get("links", [])
    entity = (inputs.get("entity_name") or "").strip()
    jdir = jobs.job_dir(job_id)
    sem = asyncio.Semaphore(settings.collect_concurrency)

    async def one(collector, arg: str, platform: str) -> dict:
        async with sem:
            try:
                return (await run_in_threadpool(collector, arg, jdir)).to_dict()
            except Exception as exc:  # one bad source must not sink the others
                return SourceArtifact(url=arg, platform=platform, errors=[
                    f"collector: {type(exc).__name__}: {exc}"]).to_dict()

    tasks = [one(get_collector(p), url, p) for url in links
             for p in [classify_url(url)]]
    if settings.enable_news and entity:
        tasks.append(one(news.collect_for_entity, entity, NEWS))
    return list(await asyncio.gather(*tasks))  # same order as the links


async def _analyze(job_id: str, artifacts: list[dict]) -> list[dict]:
    jobs.update(job_id, status=jobs.ANALYZING)
    return await run_in_threadpool(vision.analyze, artifacts)


async def _synthesize(job_id: str, inputs: dict, artifacts: list[dict]) -> str:
    jobs.update(job_id, status=jobs.SYNTHESIZING)
    md = await run_in_threadpool(synthesis.build_dossier,
                                 inputs.get("entity_name"), artifacts)
    dossier = jobs.job_dir(job_id) / "dossier.md"
    dossier.write_text(md, encoding="utf-8")
    return str(dossier)


async def run_job(job_id: str) -> None:
    job = jobs.get(job_id)
    if job is None:
        return
    inputs = job["inputs"]
    try:
        artifacts = await _collect(job_id, inputs)
        artifacts = await _analyze(job_id, artifacts)
        jobs.update(job_id, result={"artifacts": artifacts})
        dossier_path = await _synthesize(job_id, inputs, artifacts)
        jobs.update(job_id, status=jobs.DONE, dossier_path=dossier_path,
                    result={"artifacts": artifacts})
    except Exception as exc:  # a job failure must never take down the worker
        jobs.update(job_id, status=jobs.ERROR, error=f"{type(exc).__name__}: {exc}")
