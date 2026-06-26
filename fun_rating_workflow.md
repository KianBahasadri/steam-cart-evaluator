# Fun Rating Workflow

Rate games for fun factor on a **0.0–1.0** scale by consulting three independent
panel agents (Gemini, GLM, Codex), then consolidating their scores into `ai_fun_rating`
in `games.json`.

## Prerequisites

- `games.json` exists (from `./fetch_cart.py`)
- CLIs on PATH: `cursor-agent`, `claude`, `codex`
- `PIONEER_API_KEY` set (for GLM via Pioneer gateway)
- Repo root: `/home/kian/steam_cart_evaluator`

## Overview

**Parallelize aggressively.** At every level, launch the maximum number of independent
workers in a single batch — do not serialize work that can run concurrently.

For each game that needs a rating:

1. **Orchestrator** (you) spawns **one rating subagent per pending game**, launching
   **all of them in parallel** in a single Task-tool batch (not one-at-a-time).
2. Each **rating subagent** launches Gemini, GLM, and Codex **in parallel** (read-only).
3. Each panel agent receives the **rating prompt**, **uses its web search tool** to research
   the game, then returns a single score.
4. Each **rating subagent** reads all three outputs, consolidates to one score, and
   **returns** the score to the orchestrator (never writes `games.json` itself).
5. The **orchestrator** waits for the full batch to finish, **persists all scores
   to `games.json` in a single write**, then prints the summary.

Skip games that already have `ai_fun_rating` unless re-rating is requested.

---

## Step 1 — Orchestrator: spawn rating subagents (max parallelism)

Use the Task tool (`generalPurpose` subagent) **once per pending game**. **Launch every
pending game in one parallel batch** — send all Task calls in a single message. Do not
wait for one game to finish before starting the next. The only limit is distinct games:
never spawn two subagents for the same `appid`.

Give each subagent this brief, substituting the game fields:

```
Rate one game for fun factor following fun_rating_workflow.md in
/home/kian/steam_cart_evaluator.

Game:
  name: <GAME_NAME>
  appid: <APPID>
  review_score: <REVIEW_SCORE or "unknown">

Do all work yourself: spawn the three panel CLIs in parallel, ensure each prompt
instructs the panel agent to use its web search tool before scoring, read their
outputs, and consolidate the score. Do NOT write to games.json — return the
results and let the orchestrator persist them.

Return: the appid, final ai_fun_rating (float), ai_fun_rating_panel (per-agent
scores as a JSON object), and one sentence explaining the consolidation.
```

**Default: all games at once.** If the cart has 12 unrated games, spawn 12 subagents in
one batch (36 panel CLIs total, all running concurrently). Only throttle if you hit a
hard platform limit (e.g. Task tool cap); otherwise prefer the largest batch possible.

Subagents do NOT write to `games.json`. The orchestrator batch-writes all scores
after the full parallel batch completes (see Orchestrator loop below), avoiding
write races entirely.

---

## Step 2 — Rating subagent: spawn the three panel sessions (parallel)

Run all three commands **in parallel** from `/home/kian/steam_cart_evaluator`. Launch
Gemini, GLM, and Codex in a single shell batch (background all three, then `wait`) — never
run them sequentially.
Use per-game temp files so concurrent ratings do not collide:

```
SLUG="<appid>"   # e.g. 292030
PROMPT_FILE="/tmp/fun-rating-${SLUG}-prompt.md"
```

Write the **rating prompt** (Step 3) to `$PROMPT_FILE`. The prompt must tell each
panel agent to **use its web search tool** to look up current reception and community
sentiment before assigning a score.

Then launch:

### Gemini (cursor-agent, read-only)

```
timeout 120 cursor-agent --print --output-format text --model gemini-3.5-flash \
  --mode ask --trust \
  "$(cat /tmp/fun-rating-${SLUG}-prompt.md)" \
  > /tmp/fun-rating-${SLUG}-gemini.md 2>/tmp/fun-rating-${SLUG}-gemini.err
```

### GLM 5.2 (Pioneer claude, read-only)

```
PROMPT="$(cat /tmp/fun-rating-${SLUG}-prompt.md)"
echo "$PROMPT" | timeout 120 env -u ANTHROPIC_AUTH_TOKEN -u CLAUDE_CODE_OAUTH_TOKEN \
  ANTHROPIC_API_KEY="$PIONEER_API_KEY" ANTHROPIC_BASE_URL="https://api.pioneer.ai" \
  claude -p --model zai-org/GLM-5.2 \
  --disallowed-tools Edit Write NotebookEdit Bash \
  --allowed-tools WebSearch WebFetch \
  > /tmp/fun-rating-${SLUG}-glm.md 2>/tmp/fun-rating-${SLUG}-glm.err
```

Keep `WebSearch` and `WebFetch` allowed so the agent can research online. Ignore the
stderr banner: `connectors are disabled`.

### Codex (read-only deliberation)

```
timeout 120 codex --search exec -m gpt-5.5 --ephemeral \
  -c model_reasoning_effort=xhigh -s read-only \
  -C /home/kian/steam_cart_evaluator \
  -o /tmp/fun-rating-${SLUG}-codex.md \
  - < /tmp/fun-rating-${SLUG}-prompt.md 2>/tmp/fun-rating-${SLUG}-codex.err
```

Wait for all three to finish (max ~2 min each). `timeout` exits 124 on kill —
treat that as a failed agent. Proceed with whatever responses you have (minimum 1).

---

## Step 3 — Rating prompt (sent to each panel agent)

Write this to `/tmp/fun-rating-${SLUG}-prompt.md`, filling in the game fields:

```
You are one judge on a video game fun-factor panel.

Game: "<GAME_NAME>" (Steam appid <APPID>)
Steam review summary: <REVIEW_SCORE>

Before rating, you MUST use your web search tool to research this game. Search for
recent reviews, Metacritic/OpenCritic scores, player sentiment, and how people describe
the fun factor. Do not rely on memory alone.

After researching, rate how fun the game is on a continuous scale from 0.0 (not fun
at all) to 1.0 (extremely fun). Consider gameplay depth, replayability, critical
reception, and general community consensus.

Your final message must be ONLY a single decimal number between 0.0 and 1.0. No words,
no explanation, no markdown — just the number.
```

Example for The Witcher 3:

```
You are one judge on a video game fun-factor panel.

Game: "The Witcher 3: Wild Hunt" (Steam appid 292030)
Steam review summary: Overwhelmingly Positive

Before rating, you MUST use your web search tool to research this game. Search for
recent reviews, Metacritic/OpenCritic scores, player sentiment, and how people describe
the fun factor. Do not rely on memory alone.

After researching, rate how fun the game is on a continuous scale from 0.0 (not fun
at all) to 1.0 (extremely fun). Consider gameplay depth, replayability, critical
reception, and general community consensus.

Your final message must be ONLY a single decimal number between 0.0 and 1.0. No words,
no explanation, no markdown — just the number.
```

---

## Step 4 — Rating subagent: read, consolidate, persist

### Parse panel scores

Read:

- `/tmp/fun-rating-${SLUG}-gemini.md`
- `/tmp/fun-rating-${SLUG}-glm.md`
- `/tmp/fun-rating-${SLUG}-codex.md`

From each file, extract the first float in `[0.0, 1.0]`. Strip whitespace; if the
model added prose, scan tokens for a valid float (same fallback as the old script).
Record which agents succeeded:

```json
"ai_fun_rating_panel": {
  "gemini": 0.92,
  "glm": 0.88,
  "codex": 0.90
}
```

Omit keys for agents that failed to produce a parseable score.

### Consolidate

- **3 scores**: median (preferred — robust to one outlier).
- **2 scores**: arithmetic mean.
- **1 score**: use it directly.
- **0 scores**: do not write `ai_fun_rating`; report failure.

Clamp the final value to `[0.0, 1.0]` and round to **two decimal places**.

### Return to orchestrator

Return the scores — do NOT write `games.json`. The orchestrator persists all
results in a single batch write after all subagents finish.

```
appid: <APPID>
ai_fun_rating: <score>
ai_fun_rating_panel: {"gemini": <g>, "glm": <l>, "codex": <c>}
note: <one sentence explaining consolidation>
```

---

## Orchestrator loop

1. Load `games.json`.
2. Build the pending list: games where `ai_fun_rating` is missing (or all games if
   re-rating).
3. **Spawn one rating subagent per pending game, all in parallel** (Step 1). One Task
   tool message with N calls, not N sequential rounds.
4. After the full batch completes, **persist all scores to `games.json` in a single
   read-update-write pass**: for each completed subagent result, find the game by
   `appid` (fallback: case-insensitive name match) and set:
   - `ai_fun_rating` — consolidated float
   - `ai_fun_rating_panel` — per-agent scores object
   - `ai_fun_rating_at` — ISO-8601 UTC timestamp of this run
5. Write `games.json` back with 2-space indent and trailing newline.
6. Print a summary table sorted by `ai_fun_rating`
   descending:

```
Fun scores:
  0.92  The Witcher 3: Wild Hunt
  0.85  Hollow Knight
   N/A  Some Game (failed)
```

---

## Parallelism checklist

| Level | Workers | Rule |
|-------|---------|------|
| Orchestrator → subagents | 1 per pending game | Launch **all** in one Task batch |
| Subagent → panel CLIs | 3 per game (Gemini, GLM, Codex) | Launch **all three** in one shell batch |
| Temp files | Per `appid` | `${SLUG}` prevents cross-game collisions |

Total concurrent panel sessions = `3 × <number of pending games>`. Aim for that maximum.

---

## Tool reference (quick copy)

| Agent | Mode | Key flags | Web search |
|-------|------|-----------|------------|
| Gemini | deliberation | `--mode ask --trust` (never `--force` / `--yolo`) | Built-in; prompt must instruct agent to search |
| GLM | deliberation | `--disallowed-tools Edit Write NotebookEdit Bash`; `--allowed-tools WebSearch WebFetch` | Explicitly allow `WebSearch` / `WebFetch` |
| Codex | deliberation | `--search exec` and `--ephemeral -s read-only` | Enabled via `--search` flag |

Follow-up on the same Codex session (not needed for rating, but available):

```
codex exec resume --last -m gpt-5.5 -c model_reasoning_effort=xhigh \
  -s read-only -o /tmp/out.md - < /tmp/prompt.md
```
