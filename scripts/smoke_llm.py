"""P1 smoke test: verify the local llama.cpp text + vision endpoints answer.

Run: .venv\\Scripts\\python scripts\\smoke_llm.py
Generates a tiny test image, asks the vision model to read it, and asks the text
model a one-line question. Prints results and always stops the server at the end.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.llm import client, server  # noqa: E402


def make_test_image(path: Path) -> None:
    # 400x120 white PNG with black text "LLAMA VISION OK", no external deps.
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        raise SystemExit("Pillow needed for the test image: pip install pillow")
    img = Image.new("RGB", (400, 120), "white")
    ImageDraw.Draw(img).text((20, 45), "LLAMA VISION OK 1234", fill="black")
    img.save(path)


def main() -> int:
    try:
        print("[text] loading model + querying...")
        ans = client.complete("Reply with exactly the word: READY", max_tokens=256,
                              reasoning_effort="low")
        print("  text reply:", ans.strip()[:120])

        img = Path(__file__).resolve().parent.parent / "data" / "smoke.png"
        img.parent.mkdir(parents=True, exist_ok=True)
        make_test_image(img)
        print("[vision] swapping to vision model + reading image...")
        desc = client.describe_image(img, "Transcribe any text visible in this image.")
        print("  vision reply:", desc.strip()[:200])

        ok = "READY" in ans.upper() and any(t in desc.upper() for t in ("LLAMA", "VISION", "OK", "1234"))
        print("\nSMOKE:", "PASS" if ok else "CHECK OUTPUT ABOVE")
        return 0 if ok else 2
    finally:
        server.stop()
        print("[cleanup] llama-server stopped.")


if __name__ == "__main__":
    raise SystemExit(main())
