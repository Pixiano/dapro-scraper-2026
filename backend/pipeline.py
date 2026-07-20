"""Job pipeline: collect → analyze (vision) → synthesize → document.

P2 wires the full state machine with stub collectors and a placeholder dossier;
P3–P7 replace each stage's body without changing this control flow."""

from fastapi.concurrency import run_in_threadpool

from . import jobs, synthesis, vision
from .collectors import news
from .collectors.base import classify_url, get_collector
from .config import settings


async def _collect(job_id: str, inputs: dict) -> list[dict]:
    jobs.update(job_id, status=jobs.COLLECTING)
    links = inputs.get("links", [])
    entity = (inputs.get("entity_name") or "").strip()
    artifacts = []
    jdir = jobs.job_dir(job_id)
    for url in links:
        collector = get_collector(classify_url(url))
        artifact = await run_in_threadpool(collector, url, jdir)
        artifacts.append(artifact.to_dict())
    if settings.enable_news and entity:
        art = await run_in_threadpool(news.collect_for_entity, entity, jdir)
        artifacts.append(art.to_dict())
    return artifacts


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
