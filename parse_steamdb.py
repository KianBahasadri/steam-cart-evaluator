#!/usr/bin/env -S uv run
"""Parse the saved SteamDB wishlist HTML and write price_history into games.json.

cat-new-low  -> "new_low"   (current price beats all previous lows)
cat-red-low  -> "above_low" (current price is above a known all-time low — i.e. neither)
no badge     -> null        (no low information available from SteamDB for this row)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def _hist_from_row(body: str) -> str | None:
    """Infer historical-low status from SteamDB row badges and discount colors."""
    cat_m = re.search(r'class="cat cat-([^"]+)"', body)
    if cat_m:
        cat = cat_m.group(1)
        if cat == "new-low":
            return "new_low"
        if cat == "red-low":
            return "above_low"

    discount_m = re.search(r'<td class="([^"]*price-discount[^"]*)"', body)
    if discount_m:
        classes = discount_m.group(1).split()
        if "price-discount-major" in classes or "price-discount-rare" in classes:
            return "new_low"
        if "price-discount-minor" in classes:
            return "matches_low"
        if "price-discount" in classes:
            return "matches_low"

    if re.search(r'class="cats">Highlighted Deal', body):
        return "matches_low"
    return None


def parse_steamdb_rows(html_text: str) -> dict[int, str]:
    result: dict[int, str] = {}
    for block_start in (m.start() for m in re.finditer(r'<tr class="app[^"]*" data-appid="(\d+)"', html_text)):
        appid = int(re.match(r'<tr class="app[^"]*" data-appid="(\d+)"', html_text[block_start:block_start+80]).group(1))
        block_end = html_text.find("</tr>", block_start)
        if block_end == -1:
            continue
        body = html_text[block_start:block_end]
        hist = _hist_from_row(body)
        if hist is not None:
            result[appid] = hist
    return result


def main() -> int:
    candidates = [
        Path("Your wishlist · CA · SteamDB.html"),
        Path("steamdb_network_grab"),
    ]
    html_path = next((p for p in candidates if p.exists()), None)
    if html_path is None:
        print("No SteamDB HTML file found.", file=sys.stderr)
        return 1

    games_path = Path("games.json")
    if not games_path.exists():
        print(f"Not found: {games_path}", file=sys.stderr)
        return 1

    hist_data = parse_steamdb_rows(html_path.read_text())
    print(f"Parsed {len(hist_data)} rows from {html_path}")

    data = json.loads(games_path.read_text())
    updated, cleared = 0, 0
    for game in data.get("games", []):
        appid = game.get("appid")
        if appid is not None and appid in hist_data:
            game["price_history"] = hist_data[appid]
            updated += 1
        elif "price_history" in game:
            del game["price_history"]
            cleared += 1

    games_path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    new_lows = sum(1 for g in data["games"] if g.get("price_history") == "new_low")
    above = sum(1 for g in data["games"] if g.get("price_history") == "above_low")
    no_data = sum(1 for g in data["games"] if "price_history" not in g)
    print(
        f"Updated {updated} game(s): {new_lows} new low, {above} above all-time low, "
        f"{no_data} not on page (cleared {cleared})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
