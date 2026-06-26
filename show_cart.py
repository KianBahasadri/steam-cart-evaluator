#!/usr/bin/env -S uv run
"""Display games.json as a sorted terminal table."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

YELLOW = "\033[33m"
RED = "\033[31m"
ORANGE = "\033[38;5;208m"
BLUE = "\033[34m"
GREEN = "\033[32m"
RESET = "\033[0m"

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


def format_proton(tier: str | None, linux_native: bool | None) -> str:
    if linux_native is True:
        return ""
    return tier or ""


REVIEW_SHORT = {
    "Overwhelmingly Positive": "+++",
    "Very Positive": "++",
    "Mostly Positive": "+",
}


def format_review(review: str | None) -> str:
    if not review:
        return ""
    return REVIEW_SHORT.get(review, review)


HIST_LOW_LABELS = {
    "new_low": "NEW LOW",
    "matches_low": "MATCHES",
    "above_low": "NOT LOW",
}


def format_hist_low(value: str | None) -> str:
    if not value:
        return "—"
    return HIST_LOW_LABELS.get(value, value)


def format_fun_rating(rating: float | None) -> str:
    if rating is None:
        return "—"
    return f"{rating:.2f}"


PRICE_BRACKETS = [
    ("<$2", 0.0, 2.0, GREEN),
    ("$2–3", 2.0, 3.0, BLUE),
    ("$3–5", 3.0, 5.0, YELLOW),
    ("$5–10", 5.0, 10.0, ORANGE),
    ("≥$10", 10.0, float("inf"), RED),
]


def _bar_segments(
    counts: list[tuple[str, int, float, str]], weights: list[float], bar_width: int
) -> list[int]:
    """Largest-remainder allocation so segments sum exactly to bar_width."""
    total = sum(weights)
    if total == 0:
        return [bar_width // len(weights)] * len(weights)
    raw = [w / total * bar_width for w in weights]
    floors = [int(r) for r in raw]
    remainders = [r - f for r, f in zip(raw, floors)]
    leftover = bar_width - sum(floors)
    for i in sorted(range(len(counts)), key=lambda j: -remainders[j]):
        if leftover <= 0:
            break
        floors[i] += 1
        leftover -= 1
    return floors


def print_price_bar(
    games: list[dict], pkey: str, currency: str, visible_cols: int, widths: list[int]
) -> None:
    """Print two coloured bars: one weighted by quantity, one by price sum."""
    bar_width = sum(w + 2 for w in widths[:visible_cols]) + visible_cols + 1

    counts: list[tuple[str, int, float, str]] = []
    for label, lo, hi, color in PRICE_BRACKETS:
        bracket_games = [g for g in games if lo <= (g.get(pkey, 0) or 0) < hi]
        n = len(bracket_games)
        bracket_sum = sum((g.get(pkey, 0) or 0) for g in bracket_games)
        counts.append((label, n, bracket_sum, color))

    if sum(c[1] for c in counts) == 0:
        return

    total_qty = sum(c[1] for c in counts)
    total_price = sum(c[2] for c in counts)

    # Bar weighted by quantity
    print("by quantity")
    qty_segs = _bar_segments(counts, [c[1] for c in counts], bar_width)
    by_qty: list[str] = []
    for (_, _, _, color), w in zip(counts, qty_segs):
        if w > 0:
            by_qty.append(f"{color}{'█' * w}{RESET}")
    print("".join(by_qty))
    qty_legend = "  ".join(
        f"{color}{label}: {n/total_qty*100:.0f}% ({n}){RESET}"
        for (label, n, s, color) in counts if n > 0
    )
    print(qty_legend)
    print()

    # Bar weighted by price sum
    print("by price")
    price_segs = _bar_segments(counts, [c[2] for c in counts], bar_width)
    by_price: list[str] = []
    for (_, _, _, color), w in zip(counts, price_segs):
        if w > 0:
            by_price.append(f"{color}{'█' * w}{RESET}")
    print("".join(by_price))
    price_legend = "  ".join(
        f"{color}{label}: {s/total_price*100:.1f}% ({format_price(s, currency)}){RESET}"
        for (label, n, s, color) in counts if n > 0
    )
    print(price_legend)


def print_table(games: list[dict], currency: str) -> None:
    pkey = price_key_for_game(games[0]) if games else f"price_{currency}"
    sorted_games = sorted(games, key=lambda g: g.get(pkey, 0) or 0)

    rows: list[tuple] = []
    for game in sorted_games:
        price = game.get(pkey, 0) or 0
        discount = game.get("discount_percentage")
        discount_str = f"-{discount}%" if discount is not None else "—"
        linux = game.get("linux_native")
        hist_raw = game.get("price_history")
        rows.append(
            (
                game.get("name", "(unknown)"),
                format_price(price, currency),
                format_hist_low(hist_raw),
                discount_str,
                format_linux(linux),
                format_proton(game.get("protondb_tier"), linux),
                format_review(game.get("review_score")),
                format_fun_rating(game.get("ai_fun_rating")),
                discount,
                game.get("ai_fun_rating"),
                hist_raw,
            )
        )

    visible_cols = 8
    headers = ("Game", "Price", "Hist. Low", "Discount", "Linux", "ProtonDB", "Rating", "AI Fun", None)
    widths = [len(str(h)) if h else 0 for h in headers]
    for name, price, hist, discount, linux, proton, review, fun, *_ in rows:
        widths[0] = max(widths[0], len(name))
        widths[1] = max(widths[1], len(price))
        widths[2] = max(widths[2], len(hist))
        widths[3] = max(widths[3], len(discount))
        widths[4] = max(widths[4], len(linux))
        widths[5] = max(widths[5], len(proton))
        widths[6] = max(widths[6], len(review))
        widths[7] = max(widths[7], len(fun))
    widths[8] = 0  # hidden columns (discount raw + fun raw + hist raw)

    def line(char: str = "─") -> str:
        parts = [char * (w + 2) for w in widths[:visible_cols]]
        return "├" + "┼".join(parts) + "┤" if char == "─" else "└" + "┴".join(parts) + "┘"

    def row(cells: tuple) -> str:
        parts = [f" {cells[i]:<{widths[i]}} " for i in range(visible_cols)]
        # Color ProtonDB tier red if not gold/platinum
        tier = cells[5]
        if tier and tier not in ("gold", "platinum", "ProtonDB"):
            parts[5] = f" {RED}{tier:<{widths[5]}}{RESET} "
        # Color discount yellow if <80%, red if <60%
        discount_str = cells[3]
        raw_discount = cells[8] if len(cells) > 8 else None
        if raw_discount is not None and discount_str != "Discount":
            if raw_discount < 60:
                parts[3] = f" {RED}{discount_str:<{widths[3]}}{RESET} "
            elif raw_discount < 80:
                parts[3] = f" {YELLOW}{discount_str:<{widths[3]}}{RESET} "
        # Color AI fun rating: red <0.65, yellow 0.65-0.79, no color >=0.80
        fun_str = cells[7]
        raw_fun = cells[9] if len(cells) > 9 else None
        if raw_fun is not None and fun_str != "AI Fun" and fun_str != "—":
            if raw_fun < 0.65:
                parts[7] = f" {RED}{fun_str:<{widths[7]}}{RESET} "
            elif raw_fun < 0.80:
                parts[7] = f" {YELLOW}{fun_str:<{widths[7]}}{RESET} "
        # Color historical low: green=new low, yellow=matches, dim otherwise
        hist_str = cells[2]
        raw_hist = cells[10] if len(cells) > 10 else None
        if raw_hist and hist_str not in ("Hist. Low",):
            if raw_hist == "new_low":
                parts[2] = f" {GREEN}{hist_str:<{widths[2]}}{RESET} "
            elif raw_hist == "matches_low":
                parts[2] = f" {YELLOW}{hist_str:<{widths[2]}}{RESET} "
        return "│" + "│".join(parts) + "│"

    top = "┌" + "┬".join("─" * (w + 2) for w in widths[:visible_cols]) + "┐"
    print(top)
    print(row(headers))
    print(line())
    for r in rows:
        print(row(r))
    print(line("─").replace("├", "└").replace("┼", "┴").replace("┤", "┘"))

    print()
    print_price_bar(sorted_games, pkey, currency, visible_cols, widths)

    total = sum(g.get(pkey, 0) or 0 for g in sorted_games)
    linux_count = sum(1 for g in sorted_games if g.get("linux_native") is True)
    proton_count = sum(
        1 for g in sorted_games
        if g.get("protondb_tier") in ("platinum", "gold")
    )
    print(
        f"\n{len(sorted_games)} games · "
        f"total {format_price(total, currency)} · "
        f"{linux_count} Linux-native · "
        f"{proton_count} Proton gold+"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Show Steam cart data as a table")
    ap.add_argument(
        "-i",
        "--input",
        default="games.json",
        help="Input JSON file (default: games.json)",
    )
    ap.add_argument(
        "--hide-dropped",
        action="store_true",
        help="Exclude games marked as dropped",
    )
    args = ap.parse_args()

    path = Path(args.input)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    currency, games = load_games(path)
    if args.hide_dropped:
        games = [g for g in games if not g.get("dropped")]
    if not games:
        print("No games in file.")
        return 0

    print_table(games, currency)
    return 0


if __name__ == "__main__":
    sys.exit(main())
