import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { motion, AnimatePresence, color } from "framer-motion";
import "./styles.css";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";
const LH = 26; // code line height in px (must match --lh in styles.css)
const MAX_CHARS = 20000;

const SAMPLE = `import math

def circle_area(r):
    area = math.pi * r ** 2
    return round(area, 2)

total = 0
for r in [1, 2, 3]:
    total += circle_area(r)
print("total area =", total)
`;

/* ---------- spider + web artwork ---------- */
function Spider({ size = 24 }) {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} aria-hidden="true" fill="none"
      stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
      <ellipse cx="16" cy="19" rx="5" ry="6.5" fill="currentColor" stroke="none" />
      <circle cx="16" cy="11" r="3.2" fill="currentColor" stroke="none" />
      <path d="M12 16 L4 11 L2 5 M12 19 L3 19 L1 25 M12 22 L5 28 L4 31 M13 13 L7 6 L8 2 M20 16 L28 11 L30 5 M20 19 L29 19 L31 25 M20 22 L27 28 L28 31 M19 13 L25 6 L24 2" />
      <path d="M14.5 17h3l-1.5 2 1.5 2h-3l1.5-2z" fill="var(--venom)" stroke="none" />
    </svg>
  );
}

function Web() {
  const spokes = 12, rings = 8, R = 300;
  const pts = (r) =>
    Array.from({ length: spokes }, (_, k) => {
      const a = (k / spokes) * Math.PI * 2;
      return [r * Math.cos(a), r * Math.sin(a)];
    });
  const ringPath = (r) => {
    const p = pts(r);
    let d = `M${p[0][0].toFixed(1)},${p[0][1].toFixed(1)}`;
    for (let k = 0; k < spokes; k++) {
      const [x, y] = p[k];
      const [nx, ny] = p[(k + 1) % spokes];
      const cx = ((x + nx) / 2) * 0.9, cy = ((y + ny) / 2) * 0.9; // silk sags toward the centre
      d += ` Q${cx.toFixed(1)},${cy.toFixed(1)} ${nx.toFixed(1)},${ny.toFixed(1)}`;
    }
    return d;
  };
  return (
    <svg className="web" viewBox="-300 -300 600 600" aria-hidden="true">
      {pts(R).map(([x, y], k) => <line key={k} x1="0" y1="0" x2={x} y2={y} />)}
      {Array.from({ length: rings }, (_, j) => <path key={j} d={ringPath((R * (j + 1)) / rings)} />)}
    </svg>
  );
}

/* ---------- tiny syntax colouring ---------- */
const TOKEN =
  /(#.*$|"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|\b[A-Za-z_]\w*\b|\b\d+(?:\.\d+)?\b)/;
const KW = new Set(
  "def return for while if elif else in import from class and or not break continue pass try except raise with as lambda is".split(" ")
);
const CONST = new Set(["True", "False", "None"]);
const BUILTIN = new Set(
  "print range len int str float list dict set tuple sum min max sorted enumerate zip abs round".split(" ")
);

function colorize(text) {
  return text.split(TOKEN).map((p, i) => {
    if (i % 2 === 0) return p;
    let c = null;
    if (p[0] === "#") c = "t-com";
    else if (p[0] === '"' || p[0] === "'") c = "t-str";
    else if (/^\d/.test(p)) c = "t-num";
    else if (KW.has(p)) c = "t-kw";
    else if (CONST.has(p)) c = "t-const";
    else if (BUILTIN.has(p)) c = "t-fn";
    return c ? <span key={i} className={c}>{p}</span> : p;
  });
}

/* ---------- plain-language description of a step (no LLM needed) ---------- */
function describe(step, changed) {
  if (!step) return "";
  switch (step.event) {
    case "call": {
      const args = Object.entries(step.locals).map(([k, o]) => `${k}=${o.v}`).join(", ");
      return `Calling ${step.func}(${args})`;
    }
    case "return":
      return step.func === "main" ? "Reached the end of the file." : `${step.func} returns ${step.ret}`;
    case "exception":
      return `Error raised: ${step.exc}`;
    case "end":
      return "Program finished.";
    default:
      return changed.length
        ? `Line ${step.line} runs next. Just updated: ${changed.join(", ")}.`
        : `Line ${step.line} runs next.`;
  }
}

/* ---------- code view with a moving marker ---------- */
function CodeView({ code, line }) {
  const lines = code.replace(/\n$/, "").split("\n");
  return (
    <div className="code" style={{ height: lines.length * LH }}>
      <motion.div
        className="marker"
        initial={false}
        animate={{ y: ((line ?? 1) - 1) * LH, opacity: line ? 1 : 0 }}
        transition={{ type: "spring", stiffness: 520, damping: 42 }}
      />
      {lines.map((t, n) => (
        <div key={n} className="ln">
          <span className="num">{n + 1}</span>
          <pre>{t ? colorize(t) : " "}</pre>
        </div>
      ))}
    </div>
  );
}

/* ---------- panels ---------- */
function Variables({ locals, changed }) {
  const entries = Object.entries(locals);
  return (
    <section className="panel">
      <h2>Variables</h2>
      {entries.length === 0 && <p className="muted">Nothing defined yet.</p>}
      <div className="vars">
        <AnimatePresence initial={false}>
          {entries.map(([k, o]) => (
            <motion.div
              layout
              key={k}
              className={"var" + (changed.includes(k) ? " changed" : "")}
              initial={{ opacity: 0, scale: 0.85 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.85 }}
              transition={{ type: "spring", stiffness: 420, damping: 32 }}
            >
              <span className="vn">{k}</span>
              <motion.span
                key={o.v}
                className="vv"
                initial={{ y: -12, opacity: 0 }}
                animate={{ y: 0, opacity: 1 }}
                transition={{ duration: 0.22 }}
              >
                {o.v}
              </motion.span>
              <span className="vt">{o.t}</span>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </section>
  );
}

function CallStack({ stack }) {
  const frames = [...stack].reverse(); // top frame first
  return (
    <section className="panel">
      <h2>Call stack</h2>
      {frames.length === 0 && <p className="muted">Empty.</p>}
      <div className="stack">
        <AnimatePresence initial={false}>
          {frames.map((f, idx) => (
            <motion.div
              layout
              key={`${stack.length - idx}-${f.name}`}
              className={"frame" + (idx === 0 ? " top" : "")}
              initial={{ opacity: 0, y: -18 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 18 }}
              transition={{ type: "spring", stiffness: 420, damping: 34 }}
            >
              <span>{f.name}()</span>
              <span className="muted">line {f.line}</span>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </section>
  );
}

/* ---------- chat ---------- */
function Rich({ text }) {
  return text.split(/```(?:\w+)?\n?([\s\S]*?)```/).map((part, i) => {
    if (i % 2 === 1) return <pre key={i} className="cb">{part.trimEnd()}</pre>;
    if (!part.trim()) return null;
    return (
      <p key={i}>
        {part.trim().split(/`([^`]+)`/).map((s, j) => (j % 2 === 1 ? <code key={j}>{s}</code> : s))}
      </p>
    );
  });
}

function Chat({ code, context, open, onClose, lifted }) {
  const [msgs, setMsgs] = useState([]); // {role, content, error?}
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [msgs, busy, open]);

  async function send(q) {
    const content = (q ?? text).trim();
    if (!content || busy) return;
    const next = [...msgs, { role: "user", content }];
    setMsgs(next);
    setText("");
    setBusy(true);
    try {
      const history = next.filter((m) => !m.error).slice(-12).map(({ role, content }) => ({ role, content }));
      const r = await fetch(`${API}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, context, messages: history }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : `server returned ${r.status}`);
      setMsgs([...next, { role: "assistant", content: d.reply }]);
    } catch (e) {
      setMsgs([...next, { role: "assistant", content: `Couldn't answer: ${e.message}`, error: true }]);
    } finally {
      setBusy(false);
    }
  }

  if (!open) return null;

  const chips = [
    "What does this code do?",
    context ? "Explain what's happening right now" : "Explain it step by step",
    "Any bugs or improvements?",
  ];

  return (
    <motion.section
      className={"chat" + (lifted ? " lifted" : "")}
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18 }}
      aria-label="Ask about your code"
    >
      <div className="chat-head">
        <Spider size={18} />
        <span className="grow">Ask about your code</span>
        {msgs.length > 0 && <button onClick={() => setMsgs([])}>Clear</button>}
        <button onClick={onClose} aria-label="Close chat">✕</button>
      </div>
      <div className="chat-msgs">
        {msgs.length === 0 && (
          <>
            <p className="muted">
              Ask anything about your code. While a run or explanation is playing, I can see the step you are on.
            </p>
            <div className="chips">
              {chips.map((c) => (
                <button key={c} className="chip" onClick={() => send(c)} disabled={busy}>{c}</button>
              ))}
            </div>
          </>
        )}
        {msgs.map((m, k) => (
          <div key={k} className={"msg " + (m.role === "user" ? "user" : "bot") + (m.error ? " err" : "")}>
            <Rich text={m.content} />
          </div>
        ))}
        {busy && (
          <div className="typing">
            <motion.span animate={{ opacity: [0.3, 1, 0.3] }} transition={{ duration: 1.2, repeat: Infinity }}>
              Thinking…
            </motion.span>
          </div>
        )}
        <div ref={endRef} />
      </div>
      <div className="chat-in">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
          }}
          placeholder="Ask a question…  (Enter to send)"
          aria-label="Your question"
        />
        <button className="primary" onClick={() => send()} disabled={busy || !text.trim()}>Send</button>
      </div>
    </motion.section>
  );
}

/* ---------- app ---------- */
export default function App() {
  const [code, setCode] = useState(SAMPLE);
  const [trace, setTrace] = useState(null); // run mode: {steps, error, truncated, output}
  const [exp, setExp] = useState(null);     // explain-only mode: {summary, lines, order}
  const [i, setI] = useState(0);            // current step (run mode) or explained line index (explain mode)
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [busy, setBusy] = useState("");     // "" | "trace" | "explain" | "ai"
  const [err, setErr] = useState("");
  const [narr, setNarr] = useState({});
  const [fileName, setFileName] = useState("");
  const [dragging, setDragging] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const fileRef = useRef(null);

  const mode = trace ? "trace" : exp ? "explain" : "edit";
  const steps = trace?.steps ?? [];
  const step = steps[i];
  const prev = steps[i - 1];
  const count = mode === "explain" ? exp.order.length : steps.length;
  const last = count - 1;
  const pct = last > 0 ? (i / last) * 100 : 0;
  const eLine = mode === "explain" ? exp.order[i] : null;
  const eText = mode === "explain" ? exp.lines[String(eLine)] : "";

  const changed = useMemo(() => {
    if (!step) return [];
    return Object.keys(step.locals).filter((k) => !prev || prev.locals[k]?.v !== step.locals[k].v);
  }, [step, prev]);

  const ai = useMemo(() => {
    for (let k = i; k >= 0; k--) if (narr[k]) return { text: narr[k], exact: k === i };
    return null;
  }, [narr, i]);

  // what the chat can "see": the current step (run mode) or line (explain mode)
  const chatContext = useMemo(() => {
    const codeLines = code.split("\n");
    if (mode === "trace" && step) {
      const vars = Object.entries(step.locals).map(([k, o]) => `${k}=${o.v}`).join(", ") || "none";
      const stack = step.stack.map((f) => f.name).join(" > ") || "none";
      const lineText = step.line ? (codeLines[step.line - 1] || "").trim() : "";
      return `Run mode, step ${i + 1} of ${count}. About to run line ${step.line ?? "none (finished)"}: ${lineText}\nEvent: ${step.event} in ${step.func}\nVariables: ${vars}\nCall stack: ${stack}\nOutput so far: ${(step.out || "").slice(-300) || "none"}`.slice(0, 2900);
    }
    if (mode === "explain" && eLine) {
      return `Explain mode, looking at line ${eLine}: ${(codeLines[eLine - 1] || "").trim()}\nExplanation shown: ${eText}`.slice(0, 2900);
    }
    return "";
  }, [mode, step, i, count, code, eLine, eText]);

  const go = useCallback(
    (n) => setI((x) => Math.max(0, Math.min(last, typeof n === "function" ? n(x) : n))),
    [last]
  );

  function backToEdit() {
    setTrace(null);
    setExp(null);
    setPlaying(false);
    setNarr({});
  }

  async function importFile(file) {
    if (!file) return;
    if (file.size > MAX_CHARS) {
      setErr(`${file.name} is too large (limit ${MAX_CHARS / 1000} KB).`);
      return;
    }
    const text = await file.text();
    if (text.includes("\u0000")) {
      setErr(`${file.name} doesn't look like a text file.`);
      return;
    }
    setCode(text.replace(/\r\n/g, "\n"));
    setFileName(file.name);
    backToEdit();
    setErr("");
  }

  async function run() {
    setBusy("trace");
    setErr("");
    setPlaying(false);
    setNarr({});
    setExp(null);
    try {
      const r = await fetch(`${API}/trace`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      if (!r.ok) throw new Error(`server returned ${r.status}`);
      const data = await r.json();
      if (data.error && data.steps.length === 0) {
        const e = data.error;
        setErr(`${e.type}${e.line ? ` on line ${e.line}` : ""}: ${e.msg}`);
        setTrace(null);
        return;
      }
      setTrace(data);
      setI(0);
      setPlaying(data.steps.length > 1);
    } catch (e) {
      setErr(`Can't reach the backend (${e.message}). Is uvicorn running on port 8000?`);
    } finally {
      setBusy("");
    }
  }

  // Explain-only: nothing is executed, so any import (torch, numpy, ...) is fine.
  async function explainOnly() {
    setBusy("explain");
    setErr("");
    setPlaying(false);
    setNarr({});
    try {
      const r = await fetch(`${API}/explain-code`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || `server returned ${r.status}`);
      const total = code.replace(/\n$/, "").split("\n").length;
      const order = Object.keys(d.lines || {})
        .map(Number)
        .filter((n) => n >= 1 && n <= total && d.lines[String(n)])
        .sort((a, b) => a - b);
      if (!order.length) throw new Error("the model returned no line explanations, try again");
      setTrace(null);
      setExp({ summary: d.summary, lines: d.lines, order });
      setI(0);
      setPlaying(order.length > 1);
    } catch (e) {
      setErr(`Explain failed: ${e.message}`);
    } finally {
      setBusy("");
    }
  }

  async function explainTrace() {
    setBusy("ai");
    setErr("");
    try {
      const r = await fetch(`${API}/explain`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, steps }),
      });
      if (!r.ok) {
        const t = await r.json().catch(() => ({}));
        throw new Error(t.detail || r.status);
      }
      setNarr((await r.json()).narration);
    } catch (e) {
      setErr(`AI explanation failed: ${e.message}`);
    } finally {
      setBusy("");
    }
  }

  // autoplay (explain mode lingers longer so the text can be read)
  useEffect(() => {
    if (!playing) return;
    if (i >= last) {
      setPlaying(false);
      return;
    }
    const t = setTimeout(() => go((x) => x + 1), (mode === "explain" ? 2200 : 900) / speed);
    return () => clearTimeout(t);
  }, [playing, i, last, speed, go, mode]);

  // keyboard: space, left, right
  useEffect(() => {
    if (mode === "edit") return;
    const onKey = (e) => {
      if (["TEXTAREA", "INPUT", "SELECT"].includes(document.activeElement?.tagName)) return;
      if (e.key === "ArrowRight") { setPlaying(false); go((x) => x + 1); }
      else if (e.key === "ArrowLeft") { setPlaying(false); go((x) => x - 1); }
      else if (e.key === " ") { e.preventDefault(); setPlaying((p) => !p); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode, go]);

  function onTab(e) {
    if (e.key !== "Tab") return;
    e.preventDefault();
    const el = e.target;
    const s = el.selectionStart;
    setCode(code.slice(0, s) + "    " + code.slice(el.selectionEnd));
    requestAnimationFrame(() => { el.selectionStart = el.selectionEnd = s + 4; });
  }

function detail() {
  return (
    <div className="details">
      <h5>Instagram</h5>
      <h5>Email</h5>
      <h5>About</h5>
      <h5>Author: Peter Parker</h5>
    </div>
  );
}




  return (
    <>
      <Web />
      <div className="app">
        <header>
          <h1><Spider size={26} />Coidspi</h1>
          <div className="actions">
            <input
              ref={fileRef}
              type="file"
              accept=".py,.txt,text/x-python,text/plain"
              hidden
              onChange={(e) => { importFile(e.target.files?.[0]); e.target.value = ""; }}
            />
            <button onClick={() => fileRef.current?.click()}>Import .py</button>
          {/* <button className={chatOpen ? "primary" : ""} onClick={() => setChatOpen((o) => !o)}>Ask AI</button>*/}
            {mode === "edit" && (
              <>
               
               {/* 
                    <button onClick={explainOnly} disabled={!!busy || !code.trim()}>
                      {busy === "explain" ? "Explaining…" : "Explain only"}
                       </button>
                        */}
              
                <button className="primary" onClick={run} disabled={!!busy || !code.trim()}>
                  {busy === "trace" ? "Running…" : "Visualize"}
                </button>
              </>
            )}
            {mode === "trace" && (
              <>
                {/* <button onClick={explainTrace} disabled={!!busy}>{busy === "ai" ? "Explaining…" : "Explain with AI"}
</button> */}
                <button onClick={backToEdit}>Edit code</button>
              </>
            )}
            {mode === "explain" && <button onClick={backToEdit}>Edit code</button>}
          </div>
        </header>

        {err && <div className="banner error">{err}</div>}
        {trace?.truncated && <div className="banner">Trace stopped at 500 steps. Shorten loops to see the whole run.</div>}
        {trace?.error && steps.length > 0 && (
          <div className="banner error">
            {trace.error.type}{trace.error.line ? ` on line ${trace.error.line}` : ""}: {trace.error.msg}
          </div>
        )}

        <main>
          <section
            className={"panel code-panel" + (dragging ? " drop" : "")}
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => { e.preventDefault(); setDragging(false); importFile(e.dataTransfer.files?.[0]); }}
          >
            {fileName && <div className="file">{fileName}</div>}
            {mode !== "edit" ? (
              <CodeView code={code} line={mode === "trace" ? step?.line : eLine} />
            ) : (
              <textarea
                value={code}
                onChange={(e) => setCode(e.target.value)}
                onKeyDown={onTab}
                spellCheck={false}
                placeholder="Write Python here, or drop a .py file"
                aria-label="Python code"
              />
            )}
          </section>

          <aside>
            
            {mode === "trace" && (
              <>
                <section className="panel say">
                  <h2>What's happening</h2>
                  <AnimatePresence mode="wait" initial={false}>
                    <motion.p
                      key={i}
                      className="auto"
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.15 }}
                    >
                      {describe(step, changed)}
                    </motion.p>
                  </AnimatePresence>
                  {ai && <p className={"ai" + (ai.exact ? "" : " stale")}>{ai.text}</p>}
                </section>
                <Variables locals={step?.locals ?? {}} changed={changed} />
                <CallStack stack={step?.stack ?? []} />
                <section className="panel">
                  <h2>Output</h2>
                  <pre className="out">{step?.out || ""}</pre>
                </section>
                

              </>
            )}

            {mode === "explain" && (
              <>
                <section className="panel">
                  <h2>Summary</h2>
                  <p className="summary">{exp.summary}</p>
                </section>
                <section className="panel say">
                  <h2>Line {eLine}</h2>
                  <AnimatePresence mode="wait" initial={false}>
                    <motion.p
                      key={i}
                      className="auto"
                      initial={{ opacity: 0, y: 6 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.15 }}
                    >
                      {eText}
                    </motion.p>
                  </AnimatePresence>
                </section>
                <section className="panel">
                  <h2>All lines</h2>
                  <div className="elist">
                    {exp.order.map((ln, idx) => (
                      <button
                        key={ln}
                        className={"eline" + (idx === i ? " on" : "")}
                        onClick={() => { setPlaying(false); go(idx); }}
                      >
                        <span className="en">{ln}</span>
                        <span>{exp.lines[String(ln)]}</span>
                      </button>
                    ))}
                  </div>
                </section>
              </>
            )}

            {mode === "edit" && (
              <section className="panel empty">
                <h2>Write code or import a file</h2>
                <p className="muted">
                  <b>Visualize</b> runs your code in a sandbox and turns every line, variable change and
                  function call into a step you can play, pause and scrub. It supports standard-library
                  imports (math, random, collections, itertools.etc).
                </p>
                <p className="muted" style={{ marginTop: 10 }}>
                  <b>Explain only</b> explains the code in a beginner manner
                </p>
              </section>
            )}
            <section>
              <h2>Learn Python with visualized coding.Track each steps and learn how an algorithm runs throught visuals</h2>
            </section>
          </aside>
        </main>

        {mode !== "edit" && (
          <footer>
            <button onClick={() => { setPlaying(false); go(0); }} aria-label="First">⏮</button>
            <button onClick={() => { setPlaying(false); go((x) => x - 1); }} aria-label="Previous">◀</button>
            <button className="primary" onClick={() => { if (i >= last) go(0); setPlaying((p) => !p); }}>
              {playing ? "Pause" : i >= last ? "Replay" : "Play"}
            </button>
            <button onClick={() => { setPlaying(false); go((x) => x + 1); }} aria-label="Next">▶</button>
            <button onClick={() => { setPlaying(false); go(last); }} aria-label="Last">⏭</button>
            <input
              className="thread"
              type="range"
              min={0}
              max={Math.max(last, 0)}
              value={i}
              style={{ "--p": `${pct}%` }}
              onChange={(e) => { setPlaying(false); go(Number(e.target.value)); }}
              aria-label="Scrub"
            />
            <span className="count">{i + 1} / {count}</span>
            <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} aria-label="Speed">
              <option value={0.5}>0.5×</option>
              <option value={1}>1×</option>
              <option value={2}>2×</option>
              <option value={4}>4×</option>
            </select>
          </footer>
        )}
        <Chat code={code} context={chatContext} open={chatOpen} onClose={() => setChatOpen(false)} lifted={mode !== "edit"} />
      </div>
    </>
  );
}