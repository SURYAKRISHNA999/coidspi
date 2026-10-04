"""
Find which NVIDIA chat models work with YOUR key right now, fastest first.
NVIDIA retires models often, so don't guess IDs: run this and copy the result.

Run from the backend folder (venv active):
    python find_models.py
"""
import asyncio
import os
import re
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

KEY = os.getenv("NVIDIA_API_KEY", "")
BASE = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").rstrip("/")
CURRENT = os.getenv("NIM_MODEL", "").strip()
TIMEOUT = float(os.getenv("FIND_TIMEOUT", "30"))  # seconds per model
MAX_TESTS = 30

INCLUDE = re.compile(
    r"instruct|chat|nemotron|llama|qwen|mistral|mixtral|deepseek|glm|gemma|phi|kimi|minimax|gpt-oss|granite|step",
    re.I,
)
EXCLUDE = re.compile(
    r"embed|rerank|reward|guard|safety|ocr|retriev|clip|parse|vision|vlm|image|video|audio|speech|"
    r"translate|calibrat|diffusion|flux|bge|e5",
    re.I,
)
PRIORITY = re.compile(r"nemotron|llama|gpt-oss|qwen|mistral|deepseek|kimi|glm|gemma", re.I)


async def test(client: httpx.AsyncClient, sem: asyncio.Semaphore, mid: str):
    body = {
        "model": mid,
        "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
        "max_tokens": 64,
        "temperature": 0,
    }
    if "kimi" in mid.lower():
        body["reasoning_effort"] = "low"
        body["temperature"] = 1
    async with sem:
        t0 = time.monotonic()
        try:
            r = await client.post(f"{BASE}/chat/completions", json=body)
        except httpx.TimeoutException:
            return mid, "timeout", time.monotonic() - t0
        except httpx.HTTPError as e:
            return mid, f"error ({type(e).__name__})", time.monotonic() - t0
    dt = time.monotonic() - t0
    if r.status_code != 200:
        return mid, f"HTTP {r.status_code}", dt
    try:
        text = (r.json()["choices"][0]["message"].get("content") or "").strip()
    except Exception:
        text = ""
    # reasoning models often spend all 64 tokens thinking and return nothing: not good for chat
    return mid, ("ok" if text else "empty (reasoning?)"), dt


async def main() -> None:
    if not KEY:
        print("NVIDIA_API_KEY not found. Put it in backend/.env first.")
        return
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {KEY}"}, timeout=TIMEOUT) as client:
        r = await client.get(f"{BASE}/models")
        if r.status_code != 200:
            print(f"Couldn't list models: HTTP {r.status_code} {r.text[:200]}")
            print("401/403 means the key is wrong or revoked.")
            return
        ids = sorted({m["id"] for m in r.json().get("data", [])})
        cands = [i for i in ids if INCLUDE.search(i) and not EXCLUDE.search(i)]
        cands.sort(key=lambda i: (0 if PRIORITY.search(i) else 1, i))
        cands = cands[:MAX_TESTS]
        if CURRENT and CURRENT not in cands:
            cands.insert(0, CURRENT)
        print(f"{len(ids)} models listed. Testing {len(cands)} chat candidates "
              f"(up to {TIMEOUT:.0f}s each, 4 at a time)...\n")
        sem = asyncio.Semaphore(4)
        results = await asyncio.gather(*(test(client, sem, m) for m in cands))

    good = sorted((x for x in results if x[1] == "ok"), key=lambda x: x[2])
    bad = [x for x in results if x[1] != "ok"]

    print("WORKING (fastest first):")
    if not good:
        print("  none. See the failures below.")
    for mid, _, dt in good:
        print(f"  {dt:5.1f}s  {mid}")
    print("\nNOT WORKING:")
    for mid, why, dt in sorted(bad, key=lambda x: x[0]):
        note = "  <- retired" if why in ("HTTP 410", "HTTP 404") else ""
        print(f"  {why:<22} {mid}{note}")
    if any(x[1] == "HTTP 429" for x in bad):
        print("\nSome calls hit HTTP 429 (rate limit). Wait a minute and run again.")

    if good:
        fastest = good[0][0]
        k3 = next((x for x in good if "kimi-k3" in x[0] and x[2] < 20), None)
        print("\nPut this in backend/.env, then restart uvicorn:")
        print(f"  NIM_MODEL={k3[0] if k3 else fastest}")
        print(f"  CHAT_MODEL={fastest}")
        if "kimi" in (k3[0] if k3 else fastest):
            print("  REASONING_EFFORT=low")


if __name__ == "__main__":
    asyncio.run(main())