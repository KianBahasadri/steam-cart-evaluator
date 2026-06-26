#!/usr/bin/env -S uv run
"""Interactive cart viewer with selection/deselection support.

Navigate with arrow keys (or j/k), toggle selection with enter/space.
Deselected items are dimmed and excluded from totals.
"""

from __future__ import annotations

import argparse
import curses
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

REVIEW_SHORT = {
    "Overwhelmingly Positive": "+++",
    "Very Positive": "++",
    "Mostly Positive": "+",
}

HIST_LOW_LABELS = {
    "new_low": "NEW LOW",
    "matches_low": "MATCHES",
    "above_low": "NOT LOW",
}

# Color pair IDs
CP_GREEN = 1
CP_YELLOW = 2
CP_RED = 3
CP_BLUE = 4
CP_ORANGE = 5

# Price brackets: (label, lo, hi, color_pair)
PRICE_BRACKETS = [
    ("<$2", 0.0, 2.0, CP_GREEN),
    ("$2–3", 2.0, 3.0, CP_BLUE),
    ("$3–5", 3.0, 5.0, CP_YELLOW),
    ("$5–10", 5.0, 10.0, CP_ORANGE),
    ("≥$10", 10.0, float("inf"), CP_RED),
]

VISIBLE_COLS = 8
HEADERS = ("Game", "Price", "Hist. Low", "Discount", "Linux", "ProtonDB", "Rating", "AI Fun")


def price_key_for_game(game: dict) -> str | None:
    for key in game:
        if key.startswith("price_"):
            return key
    return None


def load_games(path: Path) -> tuple[str, list[dict], dict]:
    data = json.loads(path.read_text())
    currency = (data.get("currency") or "cad").lower()
    games = data.get("games", [])
    return currency, games, data


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


def format_review(review: str | None) -> str:
    if not review:
        return ""
    return REVIEW_SHORT.get(review, review)


def format_hist_low(value: str | None) -> str:
    if not value:
        return "—"
    return HIST_LOW_LABELS.get(value, value)


def format_fun_rating(rating: float | None) -> str:
    if rating is None:
        return "—"
    return f"{rating:.2f}"


def _bar_segments(
    counts: list[tuple], weights: list[float], bar_width: int
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


def setup_colors() -> None:
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(CP_GREEN, curses.COLOR_GREEN, -1)
    curses.init_pair(CP_YELLOW, curses.COLOR_YELLOW, -1)
    curses.init_pair(CP_RED, curses.COLOR_RED, -1)
    curses.init_pair(CP_BLUE, curses.COLOR_BLUE, -1)
    if curses.can_change_color():
        curses.init_color(8, 1000, 500, 0)  # orange
        curses.init_pair(CP_ORANGE, 8, -1)
    else:
        curses.init_pair(CP_ORANGE, curses.COLOR_MAGENTA, -1)


def build_row_cells(game: dict, pkey: str, currency: str) -> list[str]:
    price = game.get(pkey, 0) or 0
    discount = game.get("discount_percentage")
    linux = game.get("linux_native")
    return [
        game.get("name", "(unknown)"),
        format_price(price, currency),
        format_hist_low(game.get("price_history")),
        f"-{discount}%" if discount is not None else "—",
        format_linux(linux),
        format_proton(game.get("protondb_tier"), linux),
        format_review(game.get("review_score")),
        format_fun_rating(game.get("ai_fun_rating")),
    ]


def calc_widths(games: list[dict], pkey: str, currency: str, max_x: int) -> list[int]:
    widths = [len(h) for h in HEADERS]
    for game in games:
        cells = build_row_cells(game, pkey, currency)
        for i in range(VISIBLE_COLS):
            widths[i] = max(widths[i], len(cells[i]))
    total = 1 + sum(w + 3 for w in widths[:VISIBLE_COLS])
    if total > max_x:
        overflow = total - max_x
        widths[0] = max(10, widths[0] - overflow)
    return widths


def cell_color(col_idx: int, game: dict, selected: bool) -> int:
    """Return curses color pair attribute for a cell based on column and game data."""
    if not selected:
        return 0

    if col_idx == 2:  # Hist. Low
        hist = game.get("price_history")
        if hist == "new_low":
            return curses.color_pair(CP_GREEN)
        elif hist == "matches_low":
            return curses.color_pair(CP_YELLOW)
    elif col_idx == 3:  # Discount
        discount = game.get("discount_percentage")
        if discount is not None:
            if discount < 60:
                return curses.color_pair(CP_RED)
            elif discount < 80:
                return curses.color_pair(CP_YELLOW)
    elif col_idx == 5:  # ProtonDB
        tier = game.get("protondb_tier")
        linux = game.get("linux_native")
        if tier and tier not in ("gold", "platinum") and linux is not True:
            return curses.color_pair(CP_RED)
    elif col_idx == 7:  # AI Fun
        fun = game.get("ai_fun_rating")
        if fun is not None:
            if fun < 0.65:
                return curses.color_pair(CP_RED)
            elif fun < 0.80:
                return curses.color_pair(CP_YELLOW)
    return 0


def safe_addstr(stdscr, y: int, x: int, text: str, attr: int, max_x: int) -> int:
    """Add string without exceeding window bounds. Returns new x position."""
    if y < 0 or y >= stdscr.getmaxyx()[0]:
        return x
    truncated = text[:max(0, max_x - x)]
    if truncated:
        try:
            stdscr.addstr(y, x, truncated, attr)
        except curses.error:
            pass
    return x + len(text)


def draw_border(
    stdscr, y: int, widths: list[int], left: str, mid: str, right: str, max_x: int
) -> None:
    parts = ["─" * (w + 2) for w in widths[:VISIBLE_COLS]]
    line = left + mid.join(parts) + right
    safe_addstr(stdscr, y, 0, line, 0, max_x)


def draw_header(stdscr, y: int, widths: list[int], max_x: int) -> None:
    parts = [f" {HEADERS[i]:<{widths[i]}} " for i in range(VISIBLE_COLS)]
    line = "│" + "│".join(parts) + "│"
    safe_addstr(stdscr, y, 0, line, 0, max_x)


def draw_data_row(
    stdscr,
    y: int,
    cells: list[str],
    widths: list[int],
    game: dict,
    selected: bool,
    is_current: bool,
    max_x: int,
) -> None:
    if not selected:
        base_attr = curses.A_DIM
    elif is_current:
        base_attr = curses.A_BOLD
    else:
        base_attr = 0

    x = 0
    x = safe_addstr(stdscr, y, x, "│", base_attr, max_x)

    for i in range(VISIBLE_COLS):
        w = widths[i]
        content = f" {cells[i]:<{w}} "
        c_color = cell_color(i, game, selected)
        x = safe_addstr(stdscr, y, x, content, c_color | base_attr, max_x)
        x = safe_addstr(stdscr, y, x, "│", base_attr, max_x)


def draw_bars(
    stdscr,
    y_start: int,
    games: list[dict],
    pkey: str,
    currency: str,
    widths: list[int],
    max_x: int,
) -> int:
    """Draw the two coloured distribution bars with legends. Returns next y."""
    bar_width = sum(w + 2 for w in widths[:VISIBLE_COLS]) + VISIBLE_COLS + 1
    bar_width = min(bar_width, max_x - 1)
    if bar_width < 1:
        return y_start

    counts: list[tuple[str, int, float, int]] = []
    for label, lo, hi, cp in PRICE_BRACKETS:
        bracket_games = [g for g in games if lo <= (g.get(pkey, 0) or 0) < hi]
        n = len(bracket_games)
        bracket_sum = sum((g.get(pkey, 0) or 0) for g in bracket_games)
        counts.append((label, n, bracket_sum, cp))

    total_qty = sum(c[1] for c in counts)
    total_price = sum(c[2] for c in counts)

    if total_qty == 0:
        return y_start

    y = y_start

    # --- Quantity bar ---
    safe_addstr(stdscr, y, 0, "by quantity", curses.A_BOLD, max_x)
    y += 1

    qty_segs = _bar_segments(counts, [c[1] for c in counts], bar_width)
    x = 0
    for (_, _, _, cp), w in zip(counts, qty_segs):
        if w > 0:
            x = safe_addstr(stdscr, y, x, "█" * w, curses.color_pair(cp), max_x)
    y += 1

    # Quantity legend
    x = 0
    for label, n, s, cp in counts:
        if n > 0:
            seg = f"{label}: {n / total_qty * 100:.0f}% ({n})"
            x = safe_addstr(stdscr, y, x, seg, curses.color_pair(cp), max_x)
            x = safe_addstr(stdscr, y, x, "  ", 0, max_x)
    y += 1
    y += 1  # blank line

    # --- Price bar ---
    if total_price == 0:
        return y

    safe_addstr(stdscr, y, 0, "by price", curses.A_BOLD, max_x)
    y += 1

    price_segs = _bar_segments(counts, [c[2] for c in counts], bar_width)
    x = 0
    for (_, _, _, cp), w in zip(counts, price_segs):
        if w > 0:
            x = safe_addstr(stdscr, y, x, "█" * w, curses.color_pair(cp), max_x)
    y += 1

    # Price legend
    x = 0
    for label, n, s, cp in counts:
        if s > 0:
            seg = f"{label}: {s / total_price * 100:.1f}% ({format_price(s, currency)})"
            x = safe_addstr(stdscr, y, x, seg, curses.color_pair(cp), max_x)
            x = safe_addstr(stdscr, y, x, "  ", 0, max_x)
    y += 1

    return y


def main(stdscr) -> None:
    curses.curs_set(0)
    setup_colors()

    ap = argparse.ArgumentParser(description="Interactive Steam cart viewer")
    ap.add_argument("-i", "--input", default="games.json")
    args = ap.parse_args()

    path = Path(args.input)
    if not path.exists():
        stdscr.addstr(0, 0, f"File not found: {path}")
        stdscr.refresh()
        stdscr.getch()
        return

    currency, games, data = load_games(path)
    if not games:
        stdscr.addstr(0, 0, "No games in file.")
        stdscr.refresh()
        stdscr.getch()
        return

    pkey = price_key_for_game(games[0])
    sorted_games = sorted(games, key=lambda g: g.get(pkey, 0) or 0)

    # Initialize selection from the persisted "dropped" field
    selected = [not g.get("dropped", False) for g in sorted_games]
    current_row = 0
    scroll_offset = 0

    def save_selection() -> None:
        for g, sel in zip(sorted_games, selected):
            g["dropped"] = not sel
        data["games"] = games
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))

    while True:
        stdscr.clear()
        max_y, max_x = stdscr.getmaxyx()

        widths = calc_widths(sorted_games, pkey, currency, max_x)

        # Layout: title(1) + count(1) + blank(1) + top(1) + header(1) + sep(1) = 6
        # Bottom: bottom_border(1) + blank(1) + qty_label(1) + qty_bar(1) + qty_legend(1) + blank(1) + price_label(1) + price_bar(1) + price_legend(1) + blank(1) + summary(1) = 11
        top_reserved = 6
        bottom_reserved = 11
        max_data_rows = max(1, max_y - top_reserved - bottom_reserved)
        visible_rows = min(len(sorted_games), max_data_rows)

        if current_row < scroll_offset:
            scroll_offset = current_row
        elif current_row >= scroll_offset + visible_rows:
            scroll_offset = current_row - visible_rows + 1

        y = 0

        # Title
        safe_addstr(
            stdscr, y, 0,
            "Steam Cart (↑↓/jk navigate, enter/space toggle, q/Esc quit)",
            curses.A_BOLD, max_x,
        )
        y += 1

        # Selection count
        sel_count = sum(selected)
        safe_addstr(
            stdscr, y, 0,
            f"Selected: {sel_count}/{len(sorted_games)} games",
            curses.A_BOLD, max_x,
        )
        y += 1
        y += 1  # blank

        # Top border
        draw_border(stdscr, y, widths, "┌", "┬", "┐", max_x)
        y += 1

        # Header
        draw_header(stdscr, y, widths, max_x)
        y += 1

        # Separator
        draw_border(stdscr, y, widths, "├", "┼", "┤", max_x)
        y += 1

        # Data rows
        for i in range(visible_rows):
            row_idx = scroll_offset + i
            if row_idx >= len(sorted_games):
                break
            game = sorted_games[row_idx]
            cells = build_row_cells(game, pkey, currency)
            draw_data_row(
                stdscr, y, cells, widths, game,
                selected[row_idx], row_idx == current_row, max_x,
            )
            y += 1

        # Bottom border
        draw_border(stdscr, y, widths, "└", "┴", "┘", max_x)
        y += 1
        y += 1  # blank

        # Distribution bars (for selected games only)
        selected_games = [g for g, s in zip(sorted_games, selected) if s]
        y = draw_bars(stdscr, y, selected_games, pkey, currency, widths, max_x)
        y += 1  # blank

        # Summary line
        total = sum(g.get(pkey, 0) or 0 for g in selected_games)
        linux_count = sum(1 for g in selected_games if g.get("linux_native") is True)
        proton_count = sum(
            1 for g in selected_games
            if g.get("protondb_tier") in ("platinum", "gold")
        )
        summary = (
            f"{len(selected_games)} games · "
            f"total {format_price(total, currency)} · "
            f"{linux_count} Linux-native · "
            f"{proton_count} Proton gold+"
        )
        safe_addstr(stdscr, y, 0, summary, curses.A_BOLD, max_x)

        stdscr.refresh()

        # Handle input
        key = stdscr.getch()

        if key == ord("q") or key == ord("Q") or key == 27:  # q, Q, or Esc
            break
        elif key == curses.KEY_UP or key == ord("k"):
            current_row = max(0, current_row - 1)
        elif key == curses.KEY_DOWN or key == ord("j"):
            current_row = min(len(sorted_games) - 1, current_row + 1)
        elif key == ord(" ") or key == 10:  # space or enter
            selected[current_row] = not selected[current_row]
            save_selection()


if __name__ == "__main__":
    curses.wrapper(main)
