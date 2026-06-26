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
BOLD = "\033[1m"
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


def generate_pdf(
    games: list[dict],
    currency: str,
    output_path: Path,
    dropped_games: list[dict] | None = None,
) -> None:
    """Generate a PDF report of the cart data."""
    from fpdf import FPDF

    def sanitize(text: str) -> str:
        """Replace characters not supported by built-in fonts."""
        replacements = {
            "\u2122": "(TM)",   # ™
            "\u00ae": "(R)",    # ®
            "\u00a9": "(C)",    # ©
            "\u2013": "-",      # en-dash
            "\u2014": "--",     # em-dash
            "\u2018": "'",      # left single quote
            "\u2019": "'",      # right single quote
            "\u201c": '"',      # left double quote
            "\u201d": '"',      # right double quote
            "\u2026": "...",    # ellipsis
            "\u00d7": "x",      # multiplication sign
            "\u2013": "-",      # en dash
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        # Remove any remaining non-latin1 characters
        return text.encode("latin-1", errors="replace").decode("latin-1")

    class CartPDF(FPDF):
        def header(self):
            self.set_font("Helvetica", "B", 16)
            self.cell(0, 10, "Steam Cart Report", new_x="LMARGIN", new_y="NEXT")
            self.ln(5)

        def footer(self):
            self.set_y(-15)
            self.set_font("Helvetica", "I", 8)
            self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    pkey = price_key_for_game(games[0]) if games else f"price_{currency}"

    def get_cell_color(col_idx: int, game: dict) -> tuple[int, int, int]:
        """Return RGB color tuple for a cell based on column and game data."""
        if col_idx == 5:  # ProtonDB
            tier = game.get("protondb_tier")
            if tier and tier not in ("gold", "platinum"):
                return (255, 0, 0)  # red
        elif col_idx == 3:  # Discount
            discount = game.get("discount_percentage")
            if discount is not None:
                if discount < 60:
                    return (255, 0, 0)
                elif discount < 80:
                    return (255, 255, 0)
        elif col_idx == 7:  # AI Fun
            fun = game.get("ai_fun_rating")
            if fun is not None:
                if fun < 0.65:
                    return (255, 0, 0)
                elif fun < 0.80:
                    return (255, 255, 0)
        elif col_idx == 2:  # Hist. Low
            hist = game.get("price_history")
            if hist == "new_low":
                return (0, 255, 0)
            elif hist == "matches_low":
                return (255, 255, 0)
        return (0, 0, 0)  # black

    def draw_games_table(pdf: CartPDF, game_list: list[dict], title: str | None = None):
        if title:
            pdf.set_font("Helvetica", "B", 14)
            pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(3)

        sorted_games = sorted(game_list, key=lambda g: g.get(pkey, 0) or 0)

        # Headers
        headers = ["Game", "Price", "Hist. Low", "Discount", "Linux", "ProtonDB", "Rating", "AI Fun"]
        col_widths = [65, 22, 22, 22, 15, 22, 18, 14]  # mm

        pdf.set_font("Helvetica", "B", 9)
        pdf.set_fill_color(200, 200, 200)
        for i, h in enumerate(headers):
            pdf.cell(col_widths[i], 7, h, border=1, align="C", fill=True)
        pdf.ln()

        # Rows
        pdf.set_font("Helvetica", "", 8)
        for game in sorted_games:
            price = game.get(pkey, 0) or 0
            discount = game.get("discount_percentage")
            discount_str = f"-{discount}%" if discount is not None else "—"
            linux = game.get("linux_native")
            hist_raw = game.get("price_history")

            cells = [
                game.get("name", "(unknown)"),
                format_price(price, currency),
                format_hist_low(hist_raw),
                discount_str,
                format_linux(linux),
                format_proton(game.get("protondb_tier"), linux),
                format_review(game.get("review_score")),
                format_fun_rating(game.get("ai_fun_rating")),
            ]

            for i, cell in enumerate(cells):
                if pdf.get_y() > 270:
                    pdf.ln(5)
                    pdf.set_font("Helvetica", "B", 9)
                    pdf.set_fill_color(200, 200, 200)
                    for j, h in enumerate(headers):
                        pdf.cell(col_widths[j], 7, h, border=1, align="C", fill=True)
                    pdf.ln()
                    pdf.set_font("Helvetica", "", 8)

                r, g, b = get_cell_color(i, game)
                pdf.set_text_color(r, g, b)
                pdf.cell(col_widths[i], 6, sanitize(cell), border=1)
            pdf.set_text_color(0, 0, 0)
            pdf.ln()

        pdf.ln(5)

        # Summary
        total = sum(g.get(pkey, 0) or 0 for g in sorted_games)
        linux_count = sum(1 for g in sorted_games if g.get("linux_native") is True)
        proton_count = sum(
            1 for g in sorted_games
            if g.get("protondb_tier") in ("platinum", "gold")
        )
        summary = (
            f"{len(sorted_games)} games · "
            f"total {format_price(total, currency)} · "
            f"{linux_count} Linux-native · "
            f"{proton_count} Proton gold+"
        )
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 8, sanitize(summary), new_x="LMARGIN", new_y="NEXT")
        pdf.ln(8)

    pdf = CartPDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    draw_games_table(pdf, games, title=None if dropped_games else None)

    if dropped_games:
        pdf.add_page()
        draw_games_table(pdf, dropped_games, title="Dropped Games")

    pdf.output(str(output_path))
    print(f"PDF saved to {output_path}")


def compute_widths(
    games: list[dict], currency: str, pkey: str
) -> tuple[list[int], int]:
    """Compute column widths over a set of games. Returns (widths, visible_cols)."""
    visible_cols = 8
    headers = ("Game", "Price", "Hist. Low", "Discount", "Linux", "ProtonDB", "Rating", "AI Fun", None)
    widths = [len(str(h)) if h else 0 for h in headers]
    for game in games:
        price = game.get(pkey, 0) or 0
        discount = game.get("discount_percentage")
        discount_str = f"-{discount}%" if discount is not None else "—"
        linux = game.get("linux_native")
        hist_raw = game.get("price_history")
        cells = [
            game.get("name", "(unknown)"),
            format_price(price, currency),
            format_hist_low(hist_raw),
            discount_str,
            format_linux(linux),
            format_proton(game.get("protondb_tier"), linux),
            format_review(game.get("review_score")),
            format_fun_rating(game.get("ai_fun_rating")),
        ]
        for i, cell in enumerate(cells):
            widths[i] = max(widths[i], len(cell))
    widths[8] = 0  # hidden columns
    return widths, visible_cols


def print_table(
    games: list[dict], currency: str, widths: list[int], visible_cols: int,
    title: str | None = None,
) -> None:
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

    headers = ("Game", "Price", "Hist. Low", "Discount", "Linux", "ProtonDB", "Rating", "AI Fun", None)

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

    if title:
        print(f"\n{BOLD}{title}{RESET}\n")
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
    ap.add_argument(
        "--list-dropped",
        action="store_true",
        help="Show a separate table of dropped games below the main table",
    )
    ap.add_argument(
        "--pdf",
        nargs="?",
        const="cart.pdf",
        default=None,
        help="Generate a PDF report (optional output path, defaults to cart.pdf)",
    )
    args = ap.parse_args()

    path = Path(args.input)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    currency, all_games = load_games(path)
    if not all_games:
        print("No games in file.")
        return 0

    dropped = [g for g in all_games if g.get("dropped")]
    active = [g for g in all_games if not g.get("dropped")]

    if args.hide_dropped:
        games_to_show = active
    else:
        games_to_show = all_games

    if not games_to_show:
        print("No games to display.")
        return 0

    # PDF output
    if args.pdf is not None:
        pdf_path = Path(args.pdf)
        dropped_for_pdf = dropped if (args.list_dropped and not args.hide_dropped) else None
        generate_pdf(games_to_show, currency, pdf_path, dropped_games=dropped_for_pdf)
        return 0

    # Terminal output
    pkey = price_key_for_game(games_to_show[0])
    # Compute widths over all games that will be rendered so columns align
    all_for_widths = list(games_to_show)
    if args.list_dropped and dropped and not args.hide_dropped:
        all_for_widths = list(all_games)
    widths, visible_cols = compute_widths(all_for_widths, currency, pkey)

    print_table(games_to_show, currency, widths, visible_cols,
                title="Active games" if (args.list_dropped and dropped and not args.hide_dropped) else None)

    if args.list_dropped and dropped and not args.hide_dropped:
        print_table(dropped, currency, widths, visible_cols, title="Dropped games")

    return 0


if __name__ == "__main__":
    sys.exit(main())
