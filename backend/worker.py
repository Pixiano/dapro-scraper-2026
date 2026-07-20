"""Single in-process async worker: processes one job at a time.

One-at-a-time is deliberate — the pipeline swaps GGUF models in and out of the
one GPU, so concurrent jobs would thrash VRAM. Jobs queue up and run in order."""

import asyncio

from . import jobs, pipeline

_queue: asyncio.Queue | None = None
_task: asyncio.Task | None = None


async def _loop() -> None:
    assert _queue is not None
    while True:
        job_id = await _queue.get()
        try:
            await pipeline.run_job(job_id)
        except Exception as exc:  # pipeline handles its own errors; this is a backstop
            jobs.update(job_id, status=jobs.ERROR, error=f"worker: {exc}")
        finally:
            _queue.task_done()


def start() -> None:
    global _queue, _task
    _queue = asyncio.Queue()
    _task = asyncio.create_task(_loop())


async def enqueue(job_id: str) -> None:
    if _queue is None:
        raise RuntimeError("worker not started")
    await _queue.put(job_id)


async def stop() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
