# Code Visualizer

Write Python, see it run step by step: highlighted line, variables, call stack, output.
Optional "Explain with AI" adds plain-language narration using only the real trace values.

## Run (Windows)

Backend:
```
cd backend
python -m venv .venv
.venv\bin\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Frontend:
```
cd frontend
npm install
npm i -D @vitejs/plugin-react@latest
npm run dev
```
Open http://localhost:5173. Keys: Space play/pause, Left/Right step.

## How it works

- `backend/runner.py` runs the code under `sys.settrace` in a subprocess and records
  line, locals and call stack per step (capped at 500 steps, 5 s timeout).
- `backend/main.py` exposes `POST /trace` and `POST /explain` (NVIDIA NIM, JSON-validated).
- `frontend/src/App.jsx` renders `steps[i]`. Play, scrub and step only change the index `i`.

## Sandbox limits

Restricted builtins and an import allowlist, plus timeout and step cap. This is fine for local
use. It is not a hard security boundary. Before hosting publicly, run the runner in Docker/gVisor
or switch to Pyodide in the browser.

## Next

- Tree-sitter AST summary into the `/explain` prompt (better concept naming)
- Highlight array/dict contents as boxes instead of repr strings
- Remotion wrapper around the same step components for mp4 export
