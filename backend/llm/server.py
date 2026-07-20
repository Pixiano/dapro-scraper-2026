"""Manage a single llama.cpp `llama-server` subprocess.

VRAM is 16 GB, so only one model is resident at a time (settings.models_sequential).
`ensure(role)` starts the server for the requested role, swapping models if a
different one is loaded. Roles: "text" and "vision" (vision adds --mmproj)."""

import subprocess
import time

import httpx

from ..config import settings

_proc: subprocess.Popen | None = None
_role: str | None = None


class LlamaServerError(RuntimeError):
    pass


def base_url() -> str:
    return f"http://{settings.llm_host}:{settings.llm_port}"


def _healthy() -> bool:
    try:
        r = httpx.get(base_url() + "/health", timeout=2)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


def _build_cmd(role: str) -> list[str]:
    if not settings.llama_server_exe.exists():
        raise LlamaServerError(f"llama-server not found at {settings.llama_server_exe}")
    model = settings.model_path(settings.llm_text_model if role == "text"
                                else settings.llm_vision_model)
    if not model.exists():
        raise LlamaServerError(f"Model file missing: {model}")
    ctx = settings.llm_ctx_text if role == "text" else settings.llm_ctx
    cmd = [
        str(settings.llama_server_exe),
        "-m", str(model),
        "--host", settings.llm_host, "--port", str(settings.llm_port),
        "-c", str(ctx), "-ngl", str(settings.llm_ngl),
        "--no-webui",
    ]
    if role == "vision":
        mmproj = settings.model_path(settings.llm_vision_mmproj)
        if not mmproj.exists():
            raise LlamaServerError(f"Vision projector missing: {mmproj}")
        # Vision models here (gemma-4, Qwen3.5) are reasoning models: llama.cpp
        # routes their thinking into `reasoning_content` and, on a token budget,
        # they can burn the whole allowance thinking and emit empty `content`.
        # The vision stage wants direct transcription, so switch reasoning off.
        cmd += ["--mmproj", str(mmproj), "--reasoning", "off"]
        # Qwen-VL warns it wants >=1024 image tokens for grounding tasks.
        if settings.vision_min_image_tokens:
            cmd += ["--image-min-tokens", str(settings.vision_min_image_tokens)]
    return cmd


def _kill_orphans() -> None:
    """Kill an untracked llama-server holding our port.

    A server we didn't spawn (e.g. left by a crashed run) may have been started
    with a different model or flags. Reusing it would silently serve the wrong
    config, so never trust one we don't own.
    """
    if not _healthy():
        return
    subprocess.run(["taskkill", "/IM", "llama-server.exe", "/F"],
                   capture_output=True, check=False)
    for _ in range(10):
        if not _healthy():
            return
        time.sleep(0.5)


def ensure(role: str) -> str:
    """Guarantee a healthy server for `role` is running; return its base URL."""
    global _proc, _role
    if role not in ("text", "vision"):
        raise ValueError(role)
    if _proc is not None and _proc.poll() is None and _role == role and _healthy():
        return base_url()
    stop()
    _kill_orphans()
    log_path = settings.db_path.parent / "llama-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _log = open(log_path, "w", encoding="utf-8", errors="replace")
    _proc = subprocess.Popen(_build_cmd(role), stdout=_log, stderr=subprocess.STDOUT)
    _role = role
    deadline = time.time() + settings.llm_start_timeout_s
    while time.time() < deadline:
        if _proc.poll() is not None:
            raise LlamaServerError(f"llama-server exited early (code {_proc.returncode})")
        if _healthy():
            return base_url()
        time.sleep(1)
    stop()
    raise LlamaServerError(f"llama-server for '{role}' did not become healthy in time")


def stop() -> None:
    global _proc, _role
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            _proc.kill()
    _proc, _role = None, None
