import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Literal

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ValidationError

load_dotenv(Path(__file__).parent / ".env")

RUNNER = Path(__file__).with_name("runner.py")
NIM_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
NIM_MODEL = os.getenv("NIM_MODEL", "meta/llama-3.3-70b-instruct")
CHAT_MODEL = os.getenv("CHAT_MODEL", "").strip() or None  # optional faster model just for Ask AI
TRACE_TIMEOUT = int(os.getenv("TRACE_TIMEOUT", "30"))  # seconds; library imports (torch) can be slow

app = FastAPI(title="Coidspi")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class TraceReq(BaseModel):
    code: str = Field(max_length=20000)


class ExplainReq(BaseModel):
    code: str = Field(max_length=20000)
    steps: list[dict] = Field(max_length=600)


class Narration(BaseModel):
    narration: dict[str, str]


@app.post("/trace")
def trace(req: TraceReq):
    try:
        p = subprocess.run(
            [sys.executable, "-I", str(RUNNER)],
            input=req.code,
            capture_output=True,
            text=True,
            timeout=TRACE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return {
            "steps": [],
            "error": {"type": "Timeout", "msg": f"Code ran longer than {TRACE_TIMEOUT} seconds (infinite loop, or a slow library import? raise TRACE_TIMEOUT in .env)", "line": None},
            "truncated": False,
            "output": "",
        }
    if p.returncode != 0:
        raise HTTPException(500, p.stderr[-500:] or "runner crashed")
    return json.loads(p.stdout)


# ---- shared LLM helper ----
def extract_json(text: str | None) -> dict:
    """Pull a JSON object out of a model reply: tolerates fences, prose around it, <think> blocks."""
    if not text:
        raise ValueError("empty reply")
    t = text.strip()
    if "</think>" in t:
        t = t.split("</think>")[-1]
    s, e = t.find("{"), t.rfind("}")
    if s == -1 or e <= s:
        raise ValueError("no JSON object found")
    return json.loads(t[s : e + 1])


def is_kimi(model: str | None = None) -> bool:
    return "kimi" in (model or NIM_MODEL).lower()


async def _stream_once(client: httpx.AsyncClient, body: dict, key: str, total: float) -> tuple[int, str]:
    """POST with stream=True and collect only the final answer text (reasoning deltas are ignored,
    but they keep the connection alive, so a long think doesn't hit a read timeout)."""
    parts: list[str] = []
    t0 = time.monotonic()
    headers = {"Authorization": f"Bearer {key}", "Accept": "text/event-stream"}
    async with client.stream("POST", NIM_URL, json=body, headers=headers) as r:
        if r.status_code != 200:
            return r.status_code, (await r.aread()).decode("utf-8", "replace")
        async for line in r.aiter_lines():
            if time.monotonic() - t0 > total:
                raise httpx.ReadTimeout("total deadline exceeded")
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or []
            delta = (choices[0].get("delta") or {}) if choices else {}
            if delta.get("content"):
                parts.append(delta["content"])
    return 200, "".join(parts)


async def llm_text(messages: list[dict], max_tokens: int, timeout: float, temperature: float = 0.2,
                   model: str | None = None) -> str:
    model = model or NIM_MODEL
    key = os.getenv("NVIDIA_API_KEY")
    if not key:
        raise HTTPException(503, "NVIDIA_API_KEY is not set (add it to backend/.env and restart)")
    body = {"model": model, "temperature": temperature, "max_tokens": max_tokens, "messages": messages, "stream": True}
    total = float(os.getenv("LLM_TIMEOUT", 180 if is_kimi(model) else timeout))  # whole-request cap, seconds
    if is_kimi(model):
        # Kimi K3 always reasons, and reasoning tokens count toward max_tokens. Its default effort
        # ("max") can think for a minute+, so default to "low". Set REASONING_EFFORT=high|max for harder work.
        body["temperature"] = 1
        body["max_tokens"] = max(max_tokens, 8000)
        effort = os.getenv("REASONING_EFFORT", "low").strip().lower()
        if effort and effort != "off":
            body["reasoning_effort"] = effort
    async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10)) as client:
        try:
            status, text = await _stream_once(client, body, key, total)
            if status == 400 and "reasoning_effort" in body and "reasoning_effort" in text:
                body.pop("reasoning_effort")  # endpoint doesn't accept it: retry without
                status, text = await _stream_once(client, body, key, total)
        except httpx.TimeoutException:
            raise HTTPException(
                504,
                f"{model} didn't finish within {total:.0f}s. Try REASONING_EFFORT=low, a larger "
                "LLM_TIMEOUT, or NIM_MODEL=meta/llama-3.3-70b-instruct in backend/.env.",
            )
        except httpx.HTTPError as e:
            raise HTTPException(502, f"Can't reach the NVIDIA API ({type(e).__name__})")
    if status != 200:
        raise HTTPException(502, f"LLM error {status}: {text[:200]}")
    text = text.strip()
    if "</think>" in text:
        text = text.split("</think>")[-1].strip()
    if not text:
        raise HTTPException(502, f"LLM ({model}) returned an empty reply (a reasoning model can use all its tokens thinking; try REASONING_EFFORT=low)")
    return text


async def llm_json(system: str, user: str, max_tokens: int, timeout: float) -> dict:
    text = await llm_text(
        [{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens, timeout
    )
    try:
        return extract_json(text)
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(502, f"LLM ({NIM_MODEL}) returned invalid JSON. Reply started: {text[:120]!r}")


# ---- Step-by-step narration from the real trace ----
SYSTEM = (
    "You explain Python code to beginners, one step at a time. "
    "Use ONLY the values in the trace. Never invent or recompute values. "
    "Reply with JSON only, no markdown: "
    '{"narration": {"<step index>": "one or two plain sentences"}}. '
    "Narrate at most 30 key steps: the first step, function calls, loop starts, "
    "meaningful value changes, returns, and the last step."
)


def compress(steps: list[dict]) -> str:
    rows, prev = [], {}
    for i, s in enumerate(steps):
        cur = {k: o["v"] for k, o in s.get("locals", {}).items()}
        diff = {k: v for k, v in cur.items() if prev.get(k) != v}
        prev = cur
        rows.append(f"{i}|line {s.get('line')}|{s.get('event')}|{s.get('func')}|{json.dumps(diff)}")
    return "\n".join(rows)


@app.post("/explain")
async def explain(req: ExplainReq):
    user = f"CODE:\n{req.code}\n\nTRACE (step|line|event|function|changed variables):\n{compress(req.steps)}"
    data = await llm_json(SYSTEM, user, max_tokens=1800, timeout=60)
    try:
        return Narration.model_validate(data).model_dump()
    except ValidationError:
        raise HTTPException(502, "LLM JSON had the wrong shape, try again")


# ---- Explain-only mode: no code is executed, so ANY import (torch, numpy...) is fine ----
STATIC_SYSTEM = (
    "You explain Python code to beginners. You are given code with line numbers. "
    "Do not run it. Reply with JSON only, no markdown: "
    '{"summary": "2-3 sentences on what the program does", '
    '"lines": {"<line number>": "one or two plain sentences"}}. '
    "Explain every meaningful line (skip blanks and pure comments). Mention what imported "
    "libraries are used for. Do not invent output values you cannot know from the code."
)


class StaticNarration(BaseModel):
    summary: str
    lines: dict[str, str]


class ExplainCodeReq(BaseModel):
    code: str = Field(max_length=20000)


@app.post("/explain-code")
async def explain_code(req: ExplainCodeReq):
    numbered = "\n".join(f"{i}: {l}" for i, l in enumerate(req.code.splitlines(), 1))
    data = await llm_json(STATIC_SYSTEM, f"CODE:\n{numbered}", max_tokens=3000, timeout=90)
    try:
        return StaticNarration.model_validate(data).model_dump()
    except ValidationError:
        raise HTTPException(502, "LLM JSON had the wrong shape, try again")


# ---- Built-in explain-only page: open http://localhost:8000/explain-page ----
PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Explain my code</title>
<style>
body{margin:0;background:#0e1013;color:#e8eaee;font:15px/1.5 system-ui,sans-serif}
main{max-width:1000px;margin:0 auto;padding:24px}
textarea{width:100%;min-height:220px;background:#171a20;color:#e8eaee;border:1px solid #272c35;border-radius:8px;padding:12px;font:14px/1.5 ui-monospace,Consolas,monospace}
button{margin-top:10px;padding:10px 18px;border:0;border-radius:8px;background:#76b900;color:#111;font-weight:600;cursor:pointer}
button:disabled{opacity:.5}
#sum{margin:18px 0;padding:12px;background:#171a20;border-radius:8px;display:none}
.row{display:grid;grid-template-columns:36px 1fr 1fr;gap:12px;padding:6px 0;border-bottom:1px solid #20242b}
.n{color:#6b7380;text-align:right}
code{font:13px/1.5 ui-monospace,Consolas,monospace;white-space:pre-wrap;word-break:break-all}
.e{color:#b9e27a}.err{color:#ff7b7b}
@media(max-width:700px){.row{grid-template-columns:30px 1fr}.row .e{grid-column:2}}
</style></head><body><main>
<h1>Explain my code</h1>
<textarea id="code" placeholder="Paste any Python code here. Nothing is run or installed."></textarea><br>
<button id="go">Explain each line</button>
<div id="sum"></div><div id="out"></div>
</main><script>
const $=id=>document.getElementById(id);
$("go").onclick=async()=>{
  const code=$("code").value; if(!code.trim())return;
  $("go").disabled=true;$("go").textContent="Explaining...";$("out").innerHTML="";$("sum").style.display="none";
  try{
    const r=await fetch("/explain-code",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({code})});
    const d=await r.json(); if(!r.ok) throw new Error(d.detail||"Failed");
    $("sum").textContent=d.summary;$("sum").style.display="block";
    code.split("\\n").forEach((line,i)=>{
      const row=document.createElement("div");row.className="row";
      const n=document.createElement("div");n.className="n";n.textContent=i+1;
      const c=document.createElement("code");c.textContent=line||" ";
      const e=document.createElement("div");e.className="e";e.textContent=d.lines[String(i+1)]||"";
      row.append(n,c,e);$("out").appendChild(row);
    });
  }catch(err){const x=document.createElement("div");x.className="err";x.textContent=err.message;$("out").appendChild(x);}
  $("go").disabled=false;$("go").textContent="Explain each line";
};
</script></body></html>"""


@app.get("/explain-page", response_class=HTMLResponse)
def explain_page():
    return PAGE


# ---- Chat: ask questions about the code (and the current step) ----
class ChatMsg(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatReq(BaseModel):
    code: str = Field(max_length=20000)
    context: str = Field(default="", max_length=3000)
    messages: list[ChatMsg] = Field(min_length=1, max_length=14)


CHAT_SYSTEM = (
    "You are a friendly programming tutor inside a code visualizer. The learner's code is below. "
    "Answer their questions about it clearly and briefly (under 150 words unless they ask for more). "
    "Use plain language, short paragraphs, and `inline code` or fenced code blocks when helpful. "
    "If a CURRENT STATE section is present, use it for questions about what is happening right now. "
    "Never invent variable values that are not in the code or the current state. "
    "If asked something unrelated to the code or programming, politely steer back."
)


@app.post("/chat")
async def chat(req: ChatReq):
    numbered = "\n".join(f"{i}: {l}" for i, l in enumerate(req.code.splitlines(), 1))
    system = f"{CHAT_SYSTEM}\n\nCODE:\n{numbered}\n"
    if req.context:
        system += f"\nCURRENT STATE:\n{req.context}\n"
    if "kimi-k3" in (CHAT_MODEL or NIM_MODEL).lower() and len(req.messages) > 1:
        # K3 wants its own reasoning_content passed back on assistant turns, which we don't keep.
        # So send the earlier conversation as one transcript instead of separate assistant messages.
        transcript = "\n".join(
            f"{'Learner' if m.role == 'user' else 'Tutor'}: {m.content}" for m in req.messages[:-1]
        )
        msgs = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Conversation so far:\n{transcript}\n\nLearner's new question: {req.messages[-1].content}"},
        ]
    else:
        msgs = [{"role": "system", "content": system}] + [m.model_dump() for m in req.messages]
    return {"reply": await llm_text(msgs, 900, 90, temperature=0.3, model=CHAT_MODEL)}