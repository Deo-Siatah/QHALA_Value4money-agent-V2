# Value4money Agent

**Hasibu** turns a public tender and its bids into a sourced evidence file for the committee that
has to decide on it — in minutes, not half a morning.

## The problem

Deciding on a tender does not start with the decision. It starts with the paperwork around it.

Someone pulls the bid totals out of the PDFs. Someone else digs through past awards looking for work
that is actually comparable  same kind of job, same kind of scale, not just the same keyword.
Somebody checks whether the line items add up to the stated total. Then it gets written up, presented,
and discussed. That is more than half an hour of committee time per report, spent rebuilding facts
that already exist in public records. Done the way it is done now, across the country's procurement,
it adds up to about **26 days of committee work every day**.

The expensive part is not the arithmetic. It is the comparison nobody had time to do properly: *"is
this total actually out of line for work like this, or does it just look big?"*

## How Hasibu helps the decision

- **Every bid, set beside real history.** Not a hunch — the middle of years of comparable public
  awards, with the records listed.
- **The comparison the committee did not have time for.** Each bid is placed against comparable work
  of a similar size, and the ones that sit far from the middle are named as questions to ask.
- **The arithmetic checked.** Bids whose line items do not add up to their stated total are flagged
  before anyone argues about the price.
- **The gaps named.** Where there is not enough evidence, it says so instead of guessing.
- **The time back.** The committee reads evidence instead of assembling it, and every session gets
  the same treatment regardless of who did the work.

## What it will not do

It does not pick a winner, recommend one, or call anyone overpriced. It has no authority to. Findings
are evidence, written as findings: *"this total sits 204% above the middle of 17 similar awards
(records listed)"*. **A named human approves before anything is filed**, and their name is recorded
with it.

Hasibu prepares the evidence. The committee decides.

## Built on

LangGraph, with the tender and award records in Postgres and the model doing only the language work.
All the arithmetic is done in code, so the numbers do not depend on a model having a good day.

Built for the Agentic AI for Africa Innovation Challenge, Governance track, "Value for money" theme.

---

## Quick start

Two commands, from a clean clone:

```powershell
uv run python -m loader.setup     # schema, indexes and demo data; safe to repeat
uv run python main.py             # run the agent and approve the report
```

**Sample bids are included**, so you can run this before supplying anything of your own. `main.py`
reads `sample_bids/` by default — three synthetic chainlink fence bids.

The second command watches the work: a banner, then one line per node as it finishes with the fact
that matters, the model's words as it writes them, and then the approval. When it asks, type:

```
a Your Name      # approve
r Your Name      # decline
```

Nothing is filed without a name, and a placeholder like `agent` is refused.

---

## Setup

You need three things:

| Requirement | Notes |
|---|---|
| **`uv`** and Python 3.12+ | `pip install uv`, then `uv sync` |
| **A Postgres database you control** | Neon or Supabase free tiers work; a local Postgres or `docker run postgres:16` work too. Postgres specifically — the schema uses `JSONB`, `TEXT[]`, trigram indexes, and the LangGraph checkpointer |
| **An LLM key** | Groq (`LLM_PROVIDER=groq`, free tier is enough) or Ollama locally (`LLM_PROVIDER=ollama`) |

Then:

```powershell
copy .env.example .env      # Windows PowerShell: Copy-Item .env.example .env
```

Fill in three values: `DATABASE_URL`, `LLM_MODEL`, and `GROQ_API_KEY` if you are using Groq.

`loader.setup` does the rest. It connects, applies the schema and indexes, loads `sql/demo_data.sql`
if the database is empty, and prints what the agent will be able to search. Example output on a fresh
database:

```
connected to PostgreSQL 17.2
creating the schema
creating the indexes
loading the demo slice, the tender and award records for fence, road, classroom and borehole work
loaded 3960 awards from sql/demo_data.sql

what the agent will search when you run it:
  awards with a value: 3960
  median award value: KES 493,127.50
  chainlink 36 awards
  fence      96 awards
  road       2682 awards
  classroom  445 awards
  borehole   726 awards
```

The slice is **3,894 tenders and 3,960 awards** — about 9% of the full dataset, chosen because it
covers both sample bid sets. It carries no audit rows, no reports and no approver names.

---

## Want more data?

The full Kenya OCDS exports are ~55 MB of CSV. Put them in `data/raw/` (the folder expects
`main.csv`, `awards.csv` and `awards_suppliers.csv` per year — `contracts.csv` is not used) and run:

```powershell
uv run python loader/load.py --years 2024 2025 2026
```

That **updates rows in place and never duplicates**: the loader upserts on `tenders.ocid` and
`awards.award_id`, so you can run it as often as you like, over the demo slice or over itself.
Add `--dry-run` first to see what it would read.

---

## Your own bids

Drop PDFs into a folder and point the CLI at it:

```powershell
uv run python main.py --bids-dir path\to\my_bids
uv run python main.py --pdfs bid-one.pdf bid-two.pdf      # or name them
```

With no `--pdfs`, every `*.pdf` in the folder is read, so nothing is silently skipped.

Two worked examples ship in the repository:

| Folder | Bids | Regenerate with |
|---|---|---|
| `sample_bids/` | 3 chainlink fence contracts | `uv run python -m loader.make_sample_bids` |
| `pdfs/` | 6 road rehabilitation contracts | `uv run python -m loader.make_road_bids` |

> **What to expect from the roads example.** The bids are in the billions and most Kenyan road awards
> are not, so the scale guard drops the mismatched comparables and the run ends at *"not enough
> comparable awards"* — it declines to file rather than compare against a median that does not apply.
> That is the correct behaviour, and `EVALS.md` records it.

All sample bids are synthetic. Every page says so.

---

## CLI

| Flag | What it does |
|---|---|
| `--bids-dir PATH` | read bid PDFs from this folder (default `sample_bids`) |
| `--pdfs NAME [NAME...]` | read only these PDFs from the folder |
| `--run-id ID` | use your own run id instead of a timestamped one |
| `--no-colour` | plain output, no colour |

**Colour is automatic.** It is on in a real terminal and off when output is piped or redirected, so
`main.py > run.log` gives you a clean file with no escape codes in it.

There is also a scripted runner for when nobody is at the keyboard: it resumes a run that is waiting
for a decision and supplies the decision as arguments.

```powershell
uv run python main.py                    # runs, then pauses at the approval, printing its run id
uv run python -m agent.run --resume run-20261015-103346 --decision approved --approved-by "Deo Siatah"
```

`--resume` names the paused run, so the decision lands on the run that asked for it.

---

## Web interface

The same run the terminal drives, in a browser. The web app lives in `web/` and talks to a small
FastAPI server (`api/server.py`) that starts the agent, forwards its events, and serves the evidence
file. Nothing in the interface decides anything: every figure still comes from the agent and its
tools, so the browser and the terminal are looking at one run.

Start the API with a token, then the interface, which uses the same token through a proxy:

```powershell
# in Value4money-agent/
$env:API_TOKEN = "choose-something-for-this-machine"
uv run uvicorn api.server:app --host 127.0.0.1 --port 8000

# in Value4money-agent/web/
Copy-Item .env.example .env      # paste the same API_TOKEN
npm install
npm run dev                      # http://localhost:5173
```

To see the run page without starting a real run, open
`http://localhost:5173/runs/anything?replay=1` — it plays a recorded sequence of real events.

**How it behaves.** Choosing the bid PDFs never starts anything: the files are staged by name, can be
removed, and a run only begins when **Start Analysis** is pressed. While Hasibu works, the run page is
a **single focal slot**, not a stack of cards: a numbered badge names the step that is happening now
(`Step 3 of 8`), the model's words and the step's detail stream live inside it, and on completion the
badge turns into a green tick before the same slot is handed to the next step. Once the pipeline
pauses for a person (or finishes), that slot unfolds into a **vertical overview** — every step with
its tick and one-line summary, each one expandable to inspect its citations, benchmark and pool. State
is always carried by a word and a glyph as well as a colour, so the page reads correctly in greyscale
and in print.

**The token never reaches the browser.** `EventSource` cannot set request headers, and a token in the
page would ship in the client bundle. In development Vite's proxy adds the `X-API-Token` header on the
way past; in production `web/api/[...path].ts` does the same on Vercel, forwarding `/api/*` to the
backend server-to-server. See [`web/README.md`](web/README.md) for the interface and its deployment in
full.

---

## How it works

```
intake → research → quality_check → score → draft → verify_sources → approval → file
```

| Node | What it does |
|---|---|
| `intake` | parses each bid PDF and checks the line items against the stated total |
| `research` | searches past awards, widening the search if the pool is thin (max 3 tries) |
| `quality_check` | drops unusable awards by rule — no value, wrong currency, duplicate, and **scale mismatch**: more than `MAX_SCALE_RATIO` above or below the median bid total. The model then judges only what rules cannot |
| `score` | benchmarks every bid against the pool, or reports `insufficient` if the pool is too thin |
| `draft` | writes the evidence file; every finding must cite records that exist |
| `verify_sources` | checks those citations, redrafting up to twice if a check fails |
| `approval` | interrupts and asks a human. Nothing is filed without a name |
| `file` | writes the report and logs the approver |

Two design rules hold throughout: **code does the maths, the model does the language**, and **an award
nobody judged is not evidence** — it never reaches the median.

---

## Safety

- **Every tool call is logged** to `audit_log` with its inputs, outputs, `run_id` and timestamp. The
  row is written *before* the tool runs, so a crash still leaves a trace, and a failed audit write
  blocks the tool entirely.
- **Nothing is filed without a named human.** `submit_for_committee` refuses without an `approved_by`.
- **Real data is public closed tenders only.** No personal data about individuals anywhere.
- **Bids are synthetic** and labelled as such on every page.
- **The borrowed research server is read-only**, restricted to `tenders`, `awards` and `award_labels`.
  It cannot reach `reports` or `audit_log`, and it runs under a separate SELECT-only database role.
- **If the evidence is thin, it says so.** Never a guess, never a recommendation.

---

## Layout

```
main.py                 the interactive CLI
api/                     the HTTP API the web interface talks to
web/                     the React interface, and its Vercel deployment config
agent/
  cli.py                the terminal front end: banner, live steps, approval, report
  graph.py              the eight nodes and their edges
  state.py              AgentState and its reducers
  llm.py                model wrapper: streaming, retries, one place to configure
  tools.py              audited wrappers around every tool
  checkpointer.py       Postgres checkpointing, so an interrupted run survives
  tracing.py            Opik tracing, off when unconfigured
  community_mcp.py      the borrowed Postgres MCP server, read-only
  nodes/                one file per node
  prompts/              one .md file per model job
loader/
  setup.py              uv run python -m loader.setup
  make_demo_slice.py    writes sql/demo_data.sql
  load.py               loads the full OCDS exports
  make_sample_bids.py   writes the fence bids
  make_road_bids.py     writes the road bids
mcp_server/             our own MCP server, one file per tool, plus the audit wrapper
sql/                    schema, indexes, and the demo data slice
tests/                  the tests are the spec
EVALS.md                what was checked, what it proved, and what is still broken
ARCHITECTURE.md         the agent shape, built versus borrowed MCP, and why
```

---

## Tests and evals

Tests are the spec. Run one file at a time:

```powershell
uv run pytest tests/test_quality_check.py -q
```

[`EVALS.md`](EVALS.md) records ten eval tasks with the command and the observed result for each,
including the scale-guard correction and the one known failure we have not fixed. It is the honest
record, including what does not work yet.

[`ARCHITECTURE.md`](ARCHITECTURE.md) explains the agent shape, which MCP servers we built and which we
borrowed, and why — including the one judgement call we would expect a reviewer to question.

---

## Licence

MIT. See [LICENSE](LICENSE).

The Kenya OCDS award and tender records in `sql/demo_data.sql` are public procurement data, used as
published.