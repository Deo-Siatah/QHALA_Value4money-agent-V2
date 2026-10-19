# EVALS.md — what was checked, and what it proved

Each row below is one task with a command you can run yourself and the result that command gave.
Nothing here is asserted from memory: every result was observed by running the command on the
repository as it stands.

Results are recorded per file, not across the whole suite. A full-suite run was deliberately avoided
during this phase; the point of each row is to prove one specific behaviour, so the file that owns
that behaviour is the file that is run.

**Date of these results:** 2026-10-15
**Environment:** Windows, `uv run`, Groq (`openai/gpt-oss-120b`), live Postgres on Neon, Opik tracing on.

---

## Task 01 — the scale guard (behaviour correction)

**What it checks.** That a candidate award whose value is more than 10x above *or* below the median
stated total of the bids is dropped before the model is asked anything, and counted under its own
`scale_mismatch` reason. That the rule is symmetric, that exactly 10x is kept, that the bid median is
taken across all bids rather than the first, and that the rule stays silent when no bid carries a
usable figure.

**Why it exists.** The first live run of the roads bids filed report id 7, which claimed every bid
was "far above the median" — one of them by 87,314.5%. The cause was not arithmetic: the research
node had matched the words "road rehabilitation" and returned awards in the millions, while the bids
were in the billions. The benchmark was a fence-sized median applied to road contracts, and the
report presented the resulting nonsense as evidence. Words match; size did not. This task is the fix.

**Command.**
```
uv run pytest tests/test_quality_check.py -q -p no:warnings
```

**Result: PASS — 29 passed, 0 failed.**

**What it proves.** A 20,000 KES award beside 4,000,000 KES bids no longer reaches the median, so the
review file cannot repeat the mistake that produced report id 7.

---

## Task 02 — a roads run reports "not enough evidence" instead of inventing a benchmark

**What it checks.** That the six synthetic road bids end the run honestly when no comparable pool
survives, rather than printing a percentage against a median that does not apply to them.

**Command.**
```
uv run python main.py --bids-dir pdfs
```

**Result: PASS (correct behaviour, no false figures).** Exit code 4, nothing filed.

Observed: `quality_check` reported `pool 0 · excluded 30` on each of its three attempts, `score`
reported `not enough comparable awards`, and the approval panel showed `n/a` for every percentage.
Before this fix the same command filed report id 7 with six invented percentages.

**What it proves.** The agent now refuses to benchmark against a pool it does not have, which is the
behaviour AGENTS.md asks for when evidence is thin.

---

## Task 03 — a thin pool reports "insufficient" rather than guessing

**What it checks.** That `benchmark_price` returns `confidence: "insufficient"` with no median when
fewer than `min_comparables` awards survive.

**Command.**
```
uv run pytest tests/test_score.py -q -p no:warnings
```

**Result: PASS — 9 passed, 0 failed.**

---

## Task 04 — intake catches a bid that does not add up

**What it checks.** That `parse_bid` reads a bid PDF, checks each line total and the stated total,
and refuses malformed rows. Tugen's road bid states 1,800,000,000 against line items of 1,769,850,000
on purpose.

**Command.**
```
uv run pytest tests/test_parse_bid_pdf.py -q -p no:warnings
```

**Result: PASS — 16 passed, 0 failed.**

**Confirmed live.** The roads run printed `stated total does not match its line items` against
Tugen Civil Engineering Ltd in the tender panel.

---

## Task 05 — nothing is filed without a named human

**What it checks.** That `submit_for_committee` refuses without an approver, that the CLI never
approves on the user's behalf, and that a placeholder name is refused before anything is written.

**Command.**
```
uv run pytest tests/test_file.py -q -p no:warnings
uv run pytest tests/test_cli.py -q -p no:warnings
```

**Result: PASS — 8 passed and 31 passed, 0 failed.**

---

## Task 06 — every tool call is logged

**What it checks.** That the audit wrapper writes one row per call with its inputs, outputs and
`run_id`, and that a failed audit write blocks the tool.

**Command.**
```
uv run pytest tests/test_audit.py -q -p no:warnings
```

**Result: PASS — 11 passed, 0 failed.**

---

## Task 07 — no finding without a source

**What it checks.** That every finding in a draft cites records that exist in the pool, and that a
draft failing its checks is redrafted rather than passed on.

**Command.**
```
uv run pytest tests/test_verify_sources.py -q -p no:warnings
```

**Result: FAIL — 21 passed, 1 failed.**

`test_a_whole_run_from_candidates_to_a_verified_report` asserts `pool_size == 12` and gets 11.

The cause is this task's own guard, not a flaw in the test. That test's fixture uses bids of 6,000,000
and 12,000,000 KES, so the bid median is 9,000,000 and the guard's floor is 900,000. The fixture's
candidate `a01` is worth 500,000, so the guard correctly drops it as a `scale_mismatch`.

Read plainly: the fixture describes twelve classroom awards of 500,000 to 6,000,000 as all comparable
to bids of 6,000,000 and 12,000,000. A 500,000 block is not a cheaper version of a 6,000,000 block.
The fixture is internally inconsistent and the new rule is right to object.

**Not fixed, awaiting a decision.** Two ways to close it, and both change a test rather than the rule:
scale the fixture's candidates up so they are consistent with its bids, or scale its bids down. I did
not do either on my own, because under this repository's testing rules a failing test is reported and
its owner decides. See "Needs a decision" below.

---

## Task 08 — the borrowed server cannot write or reach past its allowlist

**What it checks.** That the community MCP client runs in restricted mode, refuses SQL outside the
permitted shape, refuses tables outside `tenders`, `awards` and `award_labels`, and caps row counts.

**Command.**
```
uv run pytest tests/test_community_mcp.py -q -p no:warnings
```

**Result: PASS — 29 passed, 0 failed.**

---

## Task 09 — the demo slice is SQL that actually applies

**What it checks.** That `sql/demo_data.sql` is valid SQL against the real schema: right column
names, right types, tenders written before the awards that reference them, quotes escaped, `NULL`
left as `NULL`, and `ON CONFLICT DO NOTHING` so it is harmless on a database that already holds the
full exports.

**Command.**
```
uv run python -m loader.make_demo_slice
uv run pytest tests/test_make_demo_slice.py tests/test_schema_sql.py -q -p no:warnings
```

**Result: PASS — 13 + 5 passed, 0 failed.** The generated file holds **3,894 tenders and 3,960
awards**, 1.98 MB.

**Verified against a live database.** The file was executed inside a transaction on the real
database and rolled back: it ran clean, which is the proof that matters for someone applying it to an
empty one.

---

## Task 10 — setup is repeatable, and says so when it cannot go on

**What it checks.** That `loader.setup` applies the schema before the indexes, loads the demo slice
only into an empty database, reports the pool it created, names `DATABASE_URL` when it is missing,
and refuses a database that is not Postgres before creating anything.

**Command.**
```
uv run python -m loader.setup
uv run pytest tests/test_setup.py -q -p no:warnings
```

**Result: PASS — 8 passed, 0 failed.**

**Observed live**, on a database that already held the full dataset, which is the idempotent case:

```
connected to PostgreSQL 18.6 (c021049)
creating the schema
creating the indexes
this database already holds awards, so the demo slice was not loaded
  awards with a value: 44369
  median award value: KES 493,127.50
  road       2682 awards
  ...
```

**Two bugs this task caught**, both of which would have broken a stranger's first run:

- Postgres reports its version as `PostgreSQL`, capital S. The first version check compared against
  `"Postgres"` and rejected every genuine Postgres database. Now compared case-insensitively.
- The keyword report stripped the `%` wildcards before searching, so `ILIKE 'fence'` matched nothing
  and the report said "0 awards" for every keyword.

---

## Task 11 — the same bids produce the same answer

**What it checks.** Opik's own analysis of 151 traces found that nine runs over the *same* three fence
bids produced medians from KES 1.94M to 2.84M, and one bid came out `within_range` in some runs and
`far_above` in others. The cause: the opening search query was written by the model, so each run drew
a different pool, and the pool decides the median.

**The fix.** The first search is now built in code from the tender description intake already wrote,
and begins at `loose` rather than `strict` (strict returned nothing in 13 of 15 first attempts). The
model is asked only how to *widen* a search that has already run too thin, and a proposal that is no
wider than the previous one is overridden in code.

**Commands.**
```
uv run pytest tests/test_research.py tests/test_stability.py -q -p no:warnings
uv run python -m evals.stability --runs 5        # live, needs the model and the database
```

**Result: PASS — 29 + 11 passed, 0 failed.**

`evals/stability.py` judges a run set by two things: the medians within 10% of each other, and no
bid changing position between runs. The tolerance is 10% deliberately: the observed failure was about
19% either side of the middle, so a wider band would have passed the exact bug it exists to catch.
An empty result is reported as *not steady* rather than vacuously steady.

**What it proves.** The first query no longer moves between runs, so a median can no longer move
because the model phrased the opening search differently.

---

## Task 12 — no report is filed without a benchmark, unless a person says so

**What it checks.** Three of the road runs were approved and filed with no benchmark at all. The
approval gate now refuses a plain approval when `confidence` is `insufficient`, and requires a
different word, `p Name`, to publish a document that holds no evidence. `verify_sources` separately
refuses any `far_above`/`far_below` label or percentage when the pool was too thin to produce one.

**Command.**
```
uv run pytest tests/test_approval.py tests/test_verify_sources.py tests/test_cli.py -q -p no:warnings
```

**Result: PASS — 21 + 25 + 43 passed, 0 failed** (one pre-existing failure in
`test_verify_sources.py` described under Task 07).

**Confirmed live.** The roads run now stops at a panel reading *"this report has no comparable awards,
so it holds no evidence"* and offers `p Your Name`. The filed report states plainly that the pool is
empty, that confidence is insufficient, and that 33 candidates were excluded as `scale_mismatch`.

---

## Task 13 — traces show what was asked and what came back

**What it checks.** Opik held 151 node spans but no tool spans, no model spans and no prompts. The
reason: `@traced(...)` was applied as a module-level decorator, so it ran at import time, long before
`init_tracing()` configured Opik, and returned the function untouched.

**The fix.** `traced()` now decides at call time, so every tool call and every model call produces a
span. `research_next_move` records its full prompt text, payload and answer, because that prompt
decides what the pool becomes; other prompts record their name and payload keys only, so tender and
bid content is not shipped to a third-party trace store by default. `main.py` opens one root
`agent_run` span and threads the trace by `run_id`.

**Approval pauses are no longer logged as errors.** `GraphInterrupt` is caught, the span closes with
`outcome: paused_for_approval` and `error_info: None`, and the original exception is re-raised with its
payload intact so the run still pauses correctly.

**Command.**
```
uv run pytest tests/test_tracing.py -q -p no:warnings
```

**Result: PASS — 28 passed, 0 failed.**

**Threads, and a mistake worth recording.** Two further faults kept the Threads tab empty. The run
id was being written by `init_tracing()`, which runs *before* `opik.track()` opens the root trace, so
the thread id was set on nothing; `run.py` repeated this by writing it in a `finally` block, after the
trace had already closed. It is now written from inside the tracked function, where a trace exists.
The thread is the run, so a run and its later `--resume` are one conversation; the tender reference
is a **tag**, because two runs of the same tender are two threads and threading them together would
split a run away from its own approval.

**And a mistake of my own.** Parallel parsing reported 27 stray `parse_bid_pdf` traces because each
worker copied the context *inside* itself, where it is empty — a fresh thread starts with no context
vars. The context is now captured in the intake node's own thread and each worker runs inside its own
copy of it (one `Context` object cannot be entered by two threads at once). Those parse calls are spans
under `intake` again.

---

## Task 14 — bid PDFs are read at the same time

**What it checks.** Opik measured intake at roughly 5.5 seconds per PDF, because each was parsed in
turn. Six bids took about 33 seconds.

**Command.**
```
uv run pytest tests/test_intake.py -q -p no:warnings
```

**Result: PASS — 17 passed, 0 failed.** The concurrency test uses a barrier of two, so it fails if the
files are read one after another.

**Observed live.** Intake for the six road bids: **10.7 to 16.3 seconds**, down from about 33.

**One bug this exposed.** Parsing in parallel meant concurrent audit writes, and every audit write
opened its own connection. That exhausted what the database allows and the run died with *"server
closed the connection unexpectedly"*, which named nothing useful. Audit writes now share a small pool
(`AUDIT_POOL_SIZE`, default 4) and the parse pool is capped to match.

---

## The failure I did not fix

**The search cannot be told to find awards of a comparable size.**

Task 01 and Task 02 stopped the agent from comparing a 3,000,000,000 KES bid against a 3,696,868 KES
median. But they did not give it the ability to *find* the right comparables. The research node asks
Hasibu for the next search move in words, and `find_comparable_awards` filters by category, text and
year. Nothing in that path carries a value band, so when the model asked for "road rehabilitation" it
received 30 awards in the millions, all of which the new guard then dropped. The run ended at "not
enough comparable awards", which is honest and correct, but it is a thin answer to a question the
database could answer well: it holds road awards of 3,388,523,876 and 1,309,001,218 KES that would
have served as genuine comparables.

**What I would try next.** Pass the bid median and the scale ratio down from `quality_check` into the
search, so the third research attempt carries a value band, for example `value_kes between 300,000,000
and 30,000,000,000`. It would go in two places: the payload of `research_search_next` and the fallback
SQL prompt in `agent/prompts/research_fallback_query.md`, which currently has no value filter at
all. The guard stays as it is, because the band narrows the search and the guard proves it. Tests
first, in `tests/test_research.py` and `tests/test_community_mcp.py`, then the two payloads.

---

## Needs a decision

1. **Task 07's fixture** — scale the candidates up to match the fixture's bids, or the bids down to
   match the candidates? I recommend scaling the candidates up, because the bids are the realistic
   figures.
2. **Report id 7** stays in the database as the evidence of the bug Task 01 fixed. Keeping it means
   the audit trail shows the false figures before the correction and the honest run after. Say the
   word if you would rather it were removed.

---

## Evals still to be added

These rows are placeholders for the code-based evals to come. Each will be written as a runnable
command with an observed result, in the same format as the rows above.

- Output equivalence: the same bids produce the same evidence file across two runs
- Prompt robustness: a model that answers in the wrong shape stops the run instead of guessing
- Latency and cost per node, measured over one real run
- Database refusal: a write attempted through the community MCP reader role is refused by Postgres