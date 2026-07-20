"""OpenAI-compatible client for the local llama.cpp server.

Callers use `complete()` for text synthesis and `describe_image()` for vision;
both ensure the right model is loaded first (sequential swapping in server.py)."""

import base64
import io
import mimetypes
from pathlib import Path

import httpx

from . import server

# llama.cpp silently drops images beyond a size threshold (observed: a
# 1896x970 screenshot contributed 0 image tokens, while 1024-wide worked).
# Downscale anything wider/taller than this before sending.
MAX_IMAGE_EDGE = 1024


def _post(messages: list[dict], max_tokens: int, temperature: float,
          extra: dict | None = None) -> str:
    url = server.base_url() + "/v1/chat/completions"
    payload = {"messages": messages, "max_tokens": max_tokens,
               "temperature": temperature, "stream": False}
    if extra:
        payload.update(extra)
    r = httpx.post(url, json=payload, timeout=900)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    # Return ONLY the final answer channel. Never fall back to reasoning_content:
    # for gpt-oss synthesis an empty content means the token budget was exhausted
    # mid-thought, and dumping the raw chain-of-thought would leak the "thinking"
    # into the dossier. The vision server runs with --reasoning off, so its answer
    # is always in content anyway.
    return msg.get("content") or ""


def encode_image(path: str | Path) -> tuple[str, str]:
    """→ (mime, base64). Downscales oversized images so they aren't dropped."""
    from PIL import Image

    p = Path(path)
    img = Image.open(p)
    if max(img.size) > MAX_IMAGE_EDGE:
        img = img.convert("RGB")
        scale = MAX_IMAGE_EDGE / max(img.size)
        img = img.resize((max(1, int(img.width * scale)),
                          max(1, int(img.height * scale))))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "image/png", base64.b64encode(buf.getvalue()).decode()
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    return mime, base64.b64encode(p.read_bytes()).decode()


def complete(prompt: str, system: str | None = None, max_tokens: int = 1024,
             temperature: float = 0.3, reasoning_effort: str | None = None) -> str:
    """Text completion via the synthesis model.

    reasoning_effort: gpt-oss reasoning depth ("low"/"medium"/"high"); None uses
    the config default. Passed through as the OpenAI-compatible field, ignored by
    models that don't support it.
    """
    from ..config import settings
    server.ensure("text")
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    effort = reasoning_effort or settings.llm_reasoning_effort
    return _post(messages, max_tokens, temperature, {"reasoning_effort": effort})


def describe_image(image_path: str | Path, prompt: str,
                   max_tokens: int = 512, temperature: float = 0.2) -> str:
    server.ensure("vision")
    mime, b64 = encode_image(image_path)
    messages = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
    ]}]
    return _post(messages, max_tokens, temperature)
