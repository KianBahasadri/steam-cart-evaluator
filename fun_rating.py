#!/usr/bin/env -S uv run
"""Rate games in the cart for fun factor using OpenRouter Fusion.

Fusion queries multiple LLMs in parallel and a judge model synthesises their
answers.  Each game is asked to be rated on how fun it is, returning a
continuous score from 0.0 (not fun) to 1.0 (extremely fun).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_ANALYSIS_MODELS = [
    "~zai/glm-5.2",
    "~google/gemini-3.5-flash",
    "~qwen/qwen3.7-max",
    "~minimax/minimax-m3",
]
DEFAULT_JUDGE_MODEL = "~deepseek/deepseek-v4-pro"


def get_api_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY", "")
    if not key:
        raise SystemExit(
            "OPENROUTER_API_KEY not set. Add it to .env or export it."
        )
    return key


def rate_game(
    game: dict,
    api_key: str,
    analysis_models: list[str],
    judge_model: str,
) -> float | None:
    name = game.get("name", "?")
    review = game.get("review_score")
    context = f"\nSteam review summary: {review}." if review else ""
    prompt = (
        f"You are a video game expert panel. Rate how fun the game "
        f"\"{name}\" is on a continuous scale from 0.0 (not fun at all) "
        f"to 1.0 (extremely fun). Consider gameplay depth, replayability, "
        f"critical reception, and general community consensus."
        f"{context}\n\n"
        f"Respond ONLY with a single number between 0.0 and 1.0, "
        f"no explanation."
    )
    payload = {
        "model": "openrouter/fusion",
        "messages": [{"role": "user", "content": prompt}],
        "reasoning": {"effort": "xhigh"},
        "plugins": [
            {
                "id": "fusion",
                "analysis_models": analysis_models,
                "model": judge_model,
            }
        ],
    }
    r = requests.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60,
    )
    if not r.ok:
        print(
            f"  [openrouter] {r.status_code}: {r.text[:200]}",
            file=sys.stderr,
        )
        return None
    content = r.json()["choices"][0]["message"]["content"].strip()
    try:
        score = float(content)
        return max(0.0, min(1.0, score))
    except ValueError:
        # Try to extract a float from surrounding text
        for token in content.replace(",", " ").split():
            try:
                v = float(token)
                if 0.0 <= v <= 1.0:
                    return v
            except ValueError:
                continue
        print(
            f"  [openrouter] could not parse score from: {content!r}",
            file=sys.stderr,
        )
        return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Rate cart games for fun using OpenRouter Fusion"
    )
    ap.add_argument(
        "-i",
        "--input",
        default="games.json",
        help="Input JSON file from fetch_cart.py (default: games.json)",
    )
    ap.add_argument(
        "--game",
        help="Rate only a specific game (substring match, case-insensitive)",
    )
    ap.add_argument(
        "--models",
        nargs="+",
        default=DEFAULT_ANALYSIS_MODELS,
        help=(
            "Analysis panel models "
            f"(default: {' '.join(DEFAULT_ANALYSIS_MODELS)})"
        ),
    )
    ap.add_argument(
        "--judge",
        default=DEFAULT_JUDGE_MODEL,
        help=f"Judge model (default: {DEFAULT_JUDGE_MODEL})",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-rate games that already have an ai_fun_rating",
    )
    args = ap.parse_args()

    path = Path(args.input)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    data = json.loads(path.read_text())
    games = data.get("games", [])
    if not games:
        print("No games in file.")
        return 0

    api_key = get_api_key()

    targets = games
    if args.game:
        needle = args.game.lower()
        targets = [g for g in games if needle in g.get("name", "").lower()]
        if not targets:
            print(f"No games matching {args.game!r}")
            return 1

    if args.force:
        pending = targets
    else:
        pending = [g for g in targets if g.get("ai_fun_rating") is None]
    skipped = len(targets) - len(pending)

    print(
        f"Rating {len(pending)} game(s) with Fusion panel "
        f"(judge: {args.judge})"
        + (f", skipping {skipped} already rated" if skipped else "")
        + "..."
    )

    for game in pending:
        name = game.get("name", "?")
        print(f"  {name}...", end=" ", flush=True)
        score = rate_game(game, api_key, args.models, args.judge)
        print(f"{score}" if score is not None else "N/A")
        if score is not None:
            game["ai_fun_rating"] = score
            # Persist after each game so partial progress survives a crash.
            path.write_text(json.dumps(data, indent=2) + "\n")
        time.sleep(0.5)

    print("\nFun scores:")
    for game in sorted(targets, key=lambda g: -(g.get("ai_fun_rating") or -1)):
        score = game.get("ai_fun_rating")
        s = f"{score:.2f}" if score is not None else "N/A"
        print(f"  {s:>5}  {game.get('name', '?')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
