import json
import os
from typing import Literal

import asgi
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from js import Object
from pydantic import BaseModel, Field, ValidationError
from pyodide.ffi import to_js
from workers import WorkerEntrypoint

# No API key: models run through the Workers AI binding (see wrangler.jsonc -> "ai").
DEFAULT_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
ENV_KEYS = ("AI_MODEL", "CHAT_MODEL")
AI = None  # Workers AI binding, set on each request in the entrypoint below


def ai_model() -> str:
    return os.getenv("AI_MODEL", DEFAULT_MODEL)


def chat_model() -> str | None:
    return os.getenv("CHAT_MODEL", "").strip() or None


app = FastAPI(title="Coidspi")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "https://YOUR-APP.pages.dev",  # <-- put your real frontend URL here
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ExplainReq(BaseModel):
    code: str = Field(max_length=20000)
    steps: list[dict] = Field(max_length=600)


class Narration(BaseModel):
    narration: dict[str, str]


# ---- shared LLM helper ----
def extract_json(text: str | None) -> dict:
    """Pull a JSON object out of a model reply: tolerates fences and prose around it."""
    if not text:
        raise ValueError("empty reply")
    t = text.strip()
    if "</think>" in t:
        t = t.split("</think>")[-1]
    s, e = t.find("{"), t.rfind("}")
    if s == -1 or e <= s:
        raise ValueError("no JSON object found")
    return json.loads(t[s : e + 1])


async def llm_text(messages: list[dict], max_tokens: int, timeout: float, temperature: float = 0.2,
                   model: str | None = None) -> str:
    model = model or ai_model()
    if AI is None:
        raise HTTPException(503, 'Workers AI binding missing: add "ai": {"binding": "AI"} to wrangler.jsonc')
    payload = to_js(
        {"messages": messages, "max_tokens": max_tokens, "temperature": temperature},
        dict_converter=Object.fromEntries,
    )
    try:
        res = await AI.run(model, payload)
    except Exception as e:
        msg = str(e)
        if "3036" in msg or "daily free allocation" in msg:
            raise HTTPException(429, "Daily free AI limit reached. Try again tomorrow.")
        raise HTTPException(502, f"Workers AI error: {msg[:200]}")
    data = res.to_py() if hasattr(res, "to_py") else res
    text = ""
    if isinstance(data, dict):
        text = data.get("response") or ""
        if not text:  # some models return OpenAI-style output
            choices = data.get("choices") or []
            if choices:
                text = (choices[0].get("message") or {}).get("content") or ""
    elif isinstance(data, str):
        text = data
    text = text.strip()
    if not text:
        raise HTTPException(502, f"LLM ({model}) returned an empty reply")
    return text


async def llm_json(system: str, user: str, max_tokens: int, timeout: float) -> dict:
    text = await llm_text(
        [{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens, timeout
    )
    try:
        return extract_json(text)
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(502, f"LLM ({ai_model()}) returned invalid JSON. Reply started: {text[:120]!r}")


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


# ---- Explain-only mode: no code is executed ----
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


# ---- Built-in explain-only page: open /explain-page ----
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
    msgs = [{"role": "system", "content": system}] + [m.model_dump() for m in req.messages]
    return {"reply": await llm_text(msgs, 900, 90, temperature=0.3, model=chat_model())}


# ---- Cloudflare entrypoint (this replaces `uvicorn main:app`) ----
class Default(WorkerEntrypoint):
    async def fetch(self, request):
        global AI
        AI = self.env.AI
        for k in ENV_KEYS:
            v = getattr(self.env, k, None)
            if v:
                os.environ[k] = str(v)
        return await asgi.fetch(app, request.js_object, self.env)