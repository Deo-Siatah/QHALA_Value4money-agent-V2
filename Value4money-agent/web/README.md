# Hasibu — the web interface

The terminal front end in `agent/cli.py` and this one read the same run. Nothing here decides anything:
the API starts the agent, forwards the events it produces, and serves the evidence file it wrote.

## What the interface does

Three screens, all reading the one run the API is holding:

- **Start** (`/`) — drop or choose the bid PDFs for one tender. Choosing them is not starting them:
  the files are staged by name, any of them can be taken back out, and nothing happens until
  **Start Analysis** is pressed. A run writes audit rows and spends a model call the moment it
  begins, so a mis-drop must cost nothing before a person has looked at it.
- **Runs** (`/runs`) — every run the API is holding, newest first.
- **Run** (`/runs/:id`) — the live view of one run, in two movements.

**While Hasibu works, one step fills the page.** The run page is not a stack of cards. The step
that is happening now is held in a single focal container: a numbered badge at the top names it
(`Step 3 of 8`), the model's words and the step's detail stream live below it, and when the step
finishes the badge turns into a green tick before the same slot is handed to the next step.

**Once the run reaches the human node, the whole job unfolds.** When the pipeline pauses at the
approval gate (or ends), that one container becomes a vertical overview: every step in order, its
tick, its own one-line summary, and a disclosure that reopens the detail — the citations, the
benchmark, the pool — for any step the reader wants to inspect. The approval form and the finished
report live at the bottom of that overview.

State never depends on colour alone: every step is carried by a word and a glyph as well as a tone,
so the page reads correctly in greyscale and in print.

## Running it

Two terminals. First the API, which needs the same environment the terminal runner needs, plus a
token:

```powershell
# in Value4money-agent/
$env:API_TOKEN = "choose-something-for-this-machine"
uv run uvicorn api.server:app --host 127.0.0.1 --port 8000
```

Then the interface, which needs the same token so its dev proxy can forward it:

```powershell
cd web
Copy-Item .env.example .env      # then paste the same value into API_TOKEN
npm install
npm run dev
```

Open http://localhost:5173.

To see the run page without starting an agent run, open
http://localhost:5173/runs/anything?replay=1. That plays a recorded sequence of real events, which is
how this interface was built and the fastest way to check a change.

## Why the token goes through the proxy

`EventSource` cannot set request headers. That is a browser fact, so no token stored in the page could
ever reach the event stream, and a token in a query string would end up in logs. Vite's dev proxy adds
the header on the way past instead, so the browser never holds the token and no code here reads one.

The consequence is deliberate: the interface only ever works through a proxy. In development that is
Vite (`web/vite.config.ts`); in production it is the function in `web/api/[...path].ts`, described
under Deployment below. Neither stores the token anywhere the browser can read it.

## Deployment

The frontend is built for Vercel; the backend runs as a normal, long-lived process on Render. The
backend is not serverless on purpose: a run lives on a thread with its events buffered in memory
(`api/runner.py`, `api/events.py`), and a serverless platform that recycles the process between the
upload and the approval would lose it. Keep the Render backend at **one instance with autoscaling
off**, so a reconnecting event stream always finds the run.

### Backend (Render)

A normal Python web service:

```powershell
uv run uvicorn api.server:app --host 0.0.0.0 --port $PORT
```

Set `DATABASE_URL`, `LLM_PROVIDER=groq`, `GROQ_API_KEY`, and `API_TOKEN` (a long random secret).
Render's proxy idle timeout (~55s) is already covered by the ten-second heartbeat the event stream
sends. Attach a persistent disk for `uploads/` and `outputs/` if you want those to survive a
redeploy.

To trace runs started from the browser, set `OPIK_ENABLED=true` and `OPIK_API_KEY`, plus
`OPIK_WORKSPACE` if your workspace is not the default and `OPIK_PROJECT_NAME` if you want a project
other than `value4money-agent`. These belong only on the backend; the API configures Opik at startup
before it builds the graph, so a web-started run produces the same trace, thread and graph as the CLI.

### Frontend (Vercel)

Point Vercel at the `web/` directory (framework preset **Vite**; `web/vercel.json` sets the build and
the SPA fallback). Add two **server-side** environment variables — no `VITE_` prefix, so Vite never
inlines them into the client bundle:

| Variable | Value |
|---|---|
| `BACKEND_URL` | `https://your-backend.onrender.com` |
| `API_TOKEN` | the same secret as the Render backend |

The browser calls the relative `/api/*` paths it uses in development. `web/api/[...path].ts` forwards
them to `BACKEND_URL`, attaches `X-API-Token`, and streams the response back so the run's event
stream keeps working. The token therefore never reaches the browser on the live site, exactly as it
never does in development.

Two platform limits are worth knowing. Vercel functions stop at five minutes on the Hobby plan; when
a stream is cut, the browser's `EventSource` reconnects on its own and the channel's buffer and the
idempotent reducer make that harmless. And a Render free instance spins down after fifteen minutes
idle, so give it a paid always-on instance if a run might outlast that.

## Layout

```
api/[...path].ts   the production proxy: /api/* to the backend, with the token attached
vercel.json        Vite build, SPA fallback, function duration
src/
  api/types.ts     the shapes the API sends, mirroring api/models.py
  api/events.ts    the reducer: events in, run page out. This is the part with real logic in it
  api/client.ts    fetch and EventSource, both against a relative /api path
  api/replay.ts    a recorded run, for development without a model call
  lib/format.ts    how money, positions and durations are written on screen
  components/      the run page's parts, including the single-slot stepper
  pages/           start, runs list, run
  test/            the reducer against the recorded run, and what the interface is allowed to say
```

## Two rules the tests hold

**The house rules bind this interface too.** `test/copy.test.ts` reads the interface's own source and
fails on `recommend`, `winner`, `should win`, `overpriced`, `gouging`, `rigging`, `rigged`, `fraud` and
`corrupt`, the words `agent/prompts/persona.md` bans from the model's output. An interface that
described a bidder in those words would be making the judgement this project exists to prevent.

**Colour is not allowed to imply a verdict.** There is no green for a bid that looks fine and no red
for one that does not. A position is carried by a word and a glyph as well as a tone, so the page
reads correctly in greyscale and in print — which matters, because a committee prints this.

## Checks

```powershell
npm run typecheck
npm test
npm run build
```
