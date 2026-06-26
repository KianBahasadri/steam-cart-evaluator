#!/usr/bin/env python3
"""Display games.json as a sorted terminal table."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CURRENCY_SYMBOLS = {
    "cad": "C$",
    "usd": "$",
    "eur": "€",
    "gbp": "£",
    "aud": "A$",
    "nzd": "NZ$",
}


def price_key_for_game(game: dict) -> str | None:
    for key in game:
        if key.startswith("price_"):
            return key
    return None


def load_games(path: Path) -> tuple[str, list[dict]]:
    data = json.loads(path.read_text())
    currency = (data.get("currency") or "cad").lower()
    games = data.get("games", [])
    return currency, games


def format_price(amount: float, currency: str) -> str:
    symbol = CURRENCY_SYMBOLS.get(currency, currency.upper())
    return f"{symbol} {amount:.2f}"


def format_linux(value: bool | None) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return "?"


def print_table(games: list[dict], currency: str) -> None:
    pkey = price_key_for_game(games[0]) if games else f"price_{currency}"
    sorted_games = sorted(games, key=lambda g: g.get(pkey, 0) or 0)

    rows: list[tuple[str, str, str, str]] = []
    for game in sorted_games:
        price = game.get(pkey, 0) or 0
        discount = game.get("discount_percentage")
        discount_str = f"-{discount}%" if discount is not None else "—"
        rows.append(
            (
                game.get("name", "(unknown)"),
                format_price(price, currency),
                discount_str,
                format_linux(game.get("linux_native")),
            )
        )

    headers = ("Game", "Price", "Discount", "Linux")
    widths = [len(h) for h in headers]
    for name, price, discount, linux in rows:
        widths[0] = max(widths[0], len(name))
        widths[1] = max(widths[1], len(price))
        widths[2] = max(widths[2], len(discount))
        widths[3] = max(widths[3], len(linux))

    def line(char: str = "─") -> str:
        parts = [char * (w + 2) for w in widths]
        return "├" + "┼".join(parts) + "┤" if char == "─" else "└" + "┴".join(parts) + "┘"

    def row(cells: tuple[str, ...]) -> str:
        parts = [f" {cells[i]:<{widths[i]}} " for i in range(4)]
        return "│" + "│".join(parts) + "│"

    top = "┌" + "┬".join("─" * (w + 2) for w in widths) + "┐"
    print(top)
    print(row(headers))
    print(line())
    for r in rows:
        print(row(r))
    print(line("─").replace("├", "└").replace("┼", "┴").replace("┤", "┘"))

    total = sum(g.get(pkey, 0) or 0 for g in sorted_games)
    linux_count = sum(1 for g in sorted_games if g.get("linux_native") is True)
    print(
        f"\n{len(sorted_games)} games · "
        f"total {format_price(total, currency)} · "
        f"{linux_count} Linux-native"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Show Steam cart data as a table")
    ap.add_argument(
        "-i",
        "--input",
        default="games.json",
        help="Input JSON file (default: games.json)",
    )
    args = ap.parse_args()

    path = Path(args.input)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    currency, games = load_games(path)
    if not games:
        print("No games in file.")
        return 0

    print_table(games, currency)
    return 0


if __name__ == "__main__":
    sys.exit(main())
