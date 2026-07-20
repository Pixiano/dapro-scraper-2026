"""Empirical tiebreaker: Gemma vs Qwen vision on a real, text-dense screenshot.

Runs both models through the same llama.cpp path on the same image, timing load
and inference separately and printing each transcription for accuracy scoring."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings  # noqa: E402
from backend.llm import client, server  # noqa: E402

IMAGE = Path(__file__).resolve().parent.parent / "tests" / "image-1784116098412.png"

PROMPT = ("Transcribe ALL readable text in this screenshot as accurately as you can — "
          "preserve names, numbers, dates, and lists exactly. Then add a final line "
          "'DESIGN:' with a one-sentence description of the visual style/brand.")

MODELS = {
    "GEMMA-12B-Q4": (
        "lmstudio-community/gemma-4-12B-it-QAT-GGUF/gemma-4-12B-it-QAT-Q4_0.gguf",
        "lmstudio-community/gemma-4-12B-it-QAT-GGUF/mmproj-gemma-4-12B-it-QAT-BF16.gguf",
    ),
    "QWEN-9B-Q8": (
        "lmstudio-community/Qwen3.5-9B-GGUF/Qwen3.5-9B-Q8_0.gguf",
        "lmstudio-community/Qwen3.5-9B-GGUF/mmproj-Qwen3.5-9B-BF16.gguf",
    ),
}


def run(tag: str, model: str, mmproj: str) -> None:
    settings.llm_vision_model = model
    settings.llm_vision_mmproj = mmproj
    server.stop()  # force a fresh load with the new model
    t0 = time.time()
    server.ensure("vision")
    load_s = time.time() - t0
    t1 = time.time()
    out = client.describe_image(IMAGE, PROMPT, max_tokens=1500, temperature=0.1)
    gen_s = time.time() - t1
    print(f"\n{'='*70}\n{tag}  | load {load_s:.1f}s | gen {gen_s:.1f}s | {len(out)} chars\n{'='*70}")
    print(out.strip())


def main() -> int:
    if not IMAGE.exists():
        print("test image missing:", IMAGE)
        return 1
    try:
        for tag, (m, mm) in MODELS.items():
            if not settings.model_path(m).exists():
                print(f"[skip] {tag}: model file missing")
                continue
            run(tag, m, mm)
    finally:
        server.stop()
        print("\n[cleanup] server stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
