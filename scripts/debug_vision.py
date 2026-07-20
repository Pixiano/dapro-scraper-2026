"""Diagnose why large images aren't reaching the model.

Signal: usage.prompt_tokens. Text-only prompt ~59 tokens => image was dropped.
Hundreds+ => image tokens present."""

import base64
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from PIL import Image  # noqa: E402

from backend.config import settings  # noqa: E402
from backend.llm import server  # noqa: E402

SRC = Path(__file__).resolve().parent.parent / "tests" / "image-1784116098412.png"
TMP = Path(__file__).resolve().parent.parent / "data" / "dbg"
TMP.mkdir(parents=True, exist_ok=True)

settings.llm_vision_model = "lmstudio-community/gemma-4-12B-it-QAT-GGUF/gemma-4-12B-it-QAT-Q4_0.gguf"
settings.llm_vision_mmproj = "lmstudio-community/gemma-4-12B-it-QAT-GGUF/mmproj-gemma-4-12B-it-QAT-BF16.gguf"


def probe(path: Path) -> None:
    b = path.read_bytes()
    b64 = base64.b64encode(b).decode()
    payload = {
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "What text do you see? Answer briefly."},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]}],
        "max_tokens": 60, "temperature": 0.1, "stream": False,
    }
    t = time.time()
    try:
        r = httpx.post(server.base_url() + "/v1/chat/completions", json=payload, timeout=300)
    except Exception as exc:
        print(f"  {path.name:22} POST FAILED: {type(exc).__name__}: {exc}")
        return
    dims = Image.open(path).size
    if r.status_code != 200:
        print(f"  {path.name:22} {dims} {len(b)/1024:7.0f}KB -> HTTP {r.status_code}: {r.text[:160]}")
        return
    j = r.json()
    usage = j.get("usage", {})
    reply = j["choices"][0]["message"]["content"].strip().replace("\n", " ")[:70]
    print(f"  {path.name:22} {str(dims):12} {len(b)/1024:7.0f}KB | "
          f"prompt_tokens={usage.get('prompt_tokens'):>5} | {time.time()-t:5.1f}s | {reply}")


img = Image.open(SRC).convert("RGB")
W, H = img.size
print(f"source {W}x{H}, {SRC.stat().st_size/1024:.0f}KB\n")

variants = [SRC]
for w in (1024, 768, 512, 256):
    p = TMP / f"yt_w{w}.png"
    img.resize((w, int(H * w / W))).save(p)
    variants.append(p)

# control: the tiny image the smoke test proved works
ctrl = TMP / "ctrl.png"
c = Image.new("RGB", (400, 120), "white")
from PIL import ImageDraw  # noqa: E402
ImageDraw.Draw(c).text((20, 45), "CONTROL TEXT 42", fill="black")
c.save(ctrl)
variants.append(ctrl)

try:
    server.ensure("vision")
    print("name                   dims          size    | prompt_tokens | time  | reply")
    for v in variants:
        probe(v)
finally:
    server.stop()
