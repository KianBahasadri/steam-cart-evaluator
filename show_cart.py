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

    # Steam-inspired palette
    STEAM_DARK   = (27, 40, 56)    # #1b2838
    STEAM_MID    = (44, 62, 80)    # #2c3e50
    STEAM_ACCENT = (102, 192, 244) # #66c0f4
    ROW_EVEN     = (245, 245, 250)
    ROW_ODD      = (255, 255, 255)
    TEXT_DARK    = (40, 40, 40)
    TEXT_LIGHT   = (220, 220, 220)
    BORDER_LIGHT = (200, 200, 210)
    RED_RGB      = (220, 53, 69)
    ORANGE_RGB   = (255, 165, 0)
    YELLOW_RGB   = (200, 180, 0)
    GREEN_RGB    = (40, 167, 69)

    # Pastel background tints for status indicators (text stays dark for legibility)
    BG_GREEN  = (209, 236, 216)
    BG_YELLOW = (255, 242, 204)
    BG_ORANGE = (255, 228, 196)
    BG_RED    = (255, 213, 213)

    # Cell style logic returns background fill color or None
    def get_cell_bg(col_idx: int, game: dict) -> tuple[int, int, int] | None:
        """Return RGB background fill or None."""
        if col_idx == 5:  # ProtonDB
            tier = game.get("protondb_tier")
            if tier and tier not in ("gold", "platinum"):
                return BG_RED
        elif col_idx == 3:  # Discount
            discount = game.get("discount_percentage")
            if discount is not None:
                if discount < 60:
                    return BG_RED
                elif discount < 80:
                    return BG_ORANGE
        elif col_idx == 7:  # AI Fun
            fun = game.get("ai_fun_rating")
            if fun is not None:
                if fun < 0.65:
                    return BG_RED
                elif fun < 0.80:
                    return BG_YELLOW
        elif col_idx == 2:  # Hist. Low
            hist = game.get("price_history")
            if hist == "new_low":
                return BG_GREEN
            elif hist == "matches_low":
                return BG_YELLOW
        return None

    font_dir = "/usr/share/fonts/TTF"
    font_regular = f"{font_dir}/DejaVuSans.ttf"
    font_bold = f"{font_dir}/DejaVuSans-Bold.ttf"
    font_oblique = f"{font_dir}/DejaVuSans-Oblique.ttf"

    class CartPDF(FPDF):
        def header(self):
            # Blue title bar
            self.set_fill_color(*STEAM_DARK)
            self.rect(0, 0, self.w, 18, style="F")
            self.set_y(3)
            self.set_font("DejaVu", "B", 15)
            self.set_text_color(*TEXT_LIGHT)
            self.cell(0, 12, "  Steam Cart Report", new_x="LMARGIN", new_y="NEXT")
            self.set_text_color(*TEXT_DARK)
            self.ln(6)

        def footer(self):
            self.set_y(-12)
            self.set_font("DejaVu", "I", 7)
            self.set_text_color(160, 160, 160)
            self.cell(0, 10, f"— {self.page_no()}/{{nb}} —", align="C")
            self.set_text_color(*TEXT_DARK)

    pkey = price_key_for_game(games[0]) if games else f"price_{currency}"

    HEADERS = ["Game", "Price", "Hist. Low", "Discount", "Linux", "ProtonDB", "Rating", "AI Fun"]
    COL_W = [62, 22, 22, 22, 16, 22, 18, 14]  # mm  (total ~198 on 210mm wide page)
    TOTAL_W = sum(COL_W)
    ROW_H = 6
    HEAD_H = 7

    def build_rows(game_list: list[dict]) -> list[dict]:
        sorted_games = sorted(game_list, key=lambda g: g.get(pkey, 0) or 0)
        rows = []
        for game in sorted_games:
            price = game.get(pkey, 0) or 0
            discount = game.get("discount_percentage")
            discount_str = f"-{discount}%" if discount is not None else "—"
            linux = game.get("linux_native")
            hist_raw = game.get("price_history")
            rows.append({
                "game": game,
                "cells": [
                    game.get("name", "(unknown)"),
                    format_price(price, currency),
                    format_hist_low(hist_raw),
                    discount_str,
                    format_linux(linux),
                    format_proton(game.get("protondb_tier"), linux),
                    format_review(game.get("review_score")),
                    format_fun_rating(game.get("ai_fun_rating")),
                ],
            })
        return rows

    def draw_table(pdf: CartPDF, game_list: list[dict], title: str | None = None):
        if title:
            pdf.set_font("DejaVu", "B", 13)
            pdf.set_text_color(*STEAM_MID)
            pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
            pdf.ln(2)

        rows = build_rows(game_list)
        margin_left = (pdf.w - TOTAL_W) / 2

        def draw_header_row():
            pdf.set_fill_color(*STEAM_DARK)
            pdf.set_text_color(*TEXT_LIGHT)
            pdf.set_font("DejaVu", "B", 7.5)
            x = margin_left
            for i, h in enumerate(HEADERS):
                pdf.set_xy(x, pdf.get_y())
                pdf.cell(COL_W[i], HEAD_H, h, align="C", border=0, fill=True)
                x += COL_W[i]
            # Underline the header with an accent bar
            pdf.set_fill_color(*STEAM_ACCENT)
            pdf.rect(margin_left, pdf.get_y(), TOTAL_W, 0.6, style="F")
            pdf.ln(HEAD_H + 0.6)
            pdf.set_text_color(*TEXT_DARK)

        draw_header_row()
        pdf.set_font("DejaVu", "", 7.5)

        for idx, row_data in enumerate(rows):
            # Check if we need a new page
            if pdf.get_y() > 268:
                pdf.add_page()
                pdf.ln(2)
                draw_header_row()
                pdf.set_font("DejaVu", "", 7.5)

            bg = ROW_EVEN if idx % 2 == 0 else ROW_ODD
            pdf.set_fill_color(*bg)
            y = pdf.get_y()

            # Row background
            pdf.rect(margin_left, y, TOTAL_W, ROW_H, style="F")

            x = margin_left
            game = row_data["game"]
            for col_i, cell in enumerate(row_data["cells"]):
                pdf.set_xy(x, y)
                bg_style = get_cell_bg(col_i, game)
                # Draw pastel background if this cell has a status indicator
                if bg_style:
                    pdf.set_fill_color(*bg_style)
                    pdf.rect(x, y, COL_W[col_i], ROW_H, style="F")
                # Text is always dark for legibility
                pdf.set_text_color(*TEXT_DARK)
                # Left-align the Game column, center everything else
                align = "L" if col_i == 0 else "C"
                padding = 2 if col_i == 0 else 0
                if col_i == 0:
                    pdf.set_xy(x + padding, y)
                    # Truncate game name if too wide
                    text_w = pdf.get_string_width(cell)
                    avail = COL_W[col_i] - 2 * padding
                    if text_w > avail:
                        while pdf.get_string_width(cell + "…") > avail and len(cell) > 3:
                            cell = cell[:-1]
                        cell += "…"
                    pdf.cell(COL_W[col_i] - 2 * padding, ROW_H, cell, align="L")
                else:
                    pdf.cell(COL_W[col_i], ROW_H, cell, align=align)
                x += COL_W[col_i]

            pdf.set_text_color(*TEXT_DARK)
            pdf.set_xy(margin_left, y + ROW_H)
            # Light horizontal line at the bottom of each row
            pdf.set_draw_color(*BORDER_LIGHT)
            pdf.line(margin_left, y + ROW_H, margin_left + TOTAL_W, y + ROW_H)

        pdf.ln(4)

        # ── Summary block ──────────────────────────────────────────────────────
        sorted_games = [r["game"] for r in rows]
        total = sum(g.get(pkey, 0) or 0 for g in sorted_games)
        linux_count = sum(1 for g in sorted_games if g.get("linux_native") is True)
        proton_count = sum(1 for g in sorted_games
                           if g.get("protondb_tier") in ("platinum", "gold"))

        # Summary box
        box_h = 14
        box_y = pdf.get_y()
        pdf.set_fill_color(*STEAM_DARK)
        pdf.rect(margin_left, box_y, TOTAL_W, box_h, style="F")
        # Accent top bar on the box
        pdf.set_fill_color(*STEAM_ACCENT)
        pdf.rect(margin_left, box_y, TOTAL_W, 0.8, style="F")

        pdf.set_xy(margin_left, box_y + 2)
        pdf.set_font("DejaVu", "B", 9)
        pdf.set_text_color(*TEXT_LIGHT)
        summary_line = (
            f"{len(sorted_games)} games   ·   "
            f"Total {format_price(total, currency)}   ·   "
            f"{linux_count} Linux-native   ·   "
            f"{proton_count} Proton gold+"
        )
        pdf.cell(TOTAL_W, 10, summary_line, align="C")
        pdf.set_text_color(*TEXT_DARK)
        pdf.set_xy(margin_left, box_y + box_h + 4)

        # ── Price distribution bars ────────────────────────────────────────────
        # PDF RGB colors for price brackets (matching terminal colors)
        PDF_BRACKET_COLORS = {
            GREEN: GREEN_RGB,
            BLUE: (52, 152, 219),    # Blue
            YELLOW: YELLOW_RGB,
            ORANGE: ORANGE_RGB,
            RED: RED_RGB,
        }
        counts = []
        for label, lo, hi, ansi_color in PRICE_BRACKETS:
            bracket = [g for g in sorted_games if lo <= (g.get(pkey, 0) or 0) < hi]
            n = len(bracket)
            s = sum((g.get(pkey, 0) or 0) for g in bracket)
            rgb = PDF_BRACKET_COLORS.get(ansi_color, (128, 128, 128))
            counts.append((label, n, s, rgb))

        total_qty = sum(c[1] for c in counts)
        total_price_val = sum(c[2] for c in counts)
        if total_qty > 0 and pdf.get_y() < 250:
            bar_w = TOTAL_W * 0.9
            bar_h = 8
            bx = margin_left + (TOTAL_W - bar_w) / 2

            for bar_title, weights, total_w in [
                ("by quantity", [c[1] for c in counts], total_qty),
                ("by price",    [c[2] for c in counts], total_price_val),
            ]:
                if total_w == 0:
                    continue
                if pdf.get_y() > 262:
                    break

                pdf.set_font("DejaVu", "B", 8)
                pdf.set_text_color(*STEAM_MID)
                pdf.cell(TOTAL_W, 5, bar_title, align="C", new_x="LMARGIN", new_y="NEXT")
                pdf.ln(1)

                # Stacked bar
                cx = bx
                for (_, _, _, rgb), w in zip(counts, weights):
                    seg_w = (w / total_w) * bar_w if total_w else 0
                    if seg_w > 0:
                        pdf.set_fill_color(*rgb)
                        pdf.rect(cx, pdf.get_y(), seg_w, bar_h, style="F")
                        cx += seg_w
                pdf.ln(bar_h + 1)

                # Legend inline
                pdf.set_font("DejaVu", "", 7)
                legend_parts = []
                for label, n, s, rgb in counts:
                    if n > 0:
                        if bar_title == "by quantity":
                            legend_parts.append((f"{label}: {n}/ {total_qty} ({n/total_qty*100:.0f}%)", rgb))
                        else:
                            legend_parts.append((f"{label}: {format_price(s, currency)} ({s/total_price_val*100:.1f}%)", rgb))

                lx = margin_left
                for text, rgb in legend_parts:
                    pdf.set_fill_color(*rgb)
                    swatch_w = 4
                    pdf.rect(lx, pdf.get_y() + 1, swatch_w, 3, style="F")
                    pdf.set_xy(lx + swatch_w + 1, pdf.get_y())
                    pdf.set_text_color(*TEXT_DARK)
                    tw = pdf.get_string_width(text) + 2
                    pdf.cell(tw, 5, text)
                    lx += swatch_w + 1 + tw + 4
                pdf.ln(6)

        pdf.ln(4)

    def draw_legend(pdf: CartPDF) -> None:
        """Draw a legend and methodology section at the bottom of the report."""
        # Always start on a new page - legend is reference material
        pdf.add_page()

        # Use the same table-width / margin for alignment
        margin_left = (pdf.w - TOTAL_W) / 2

        # Section rule + title
        pdf.set_draw_color(*BORDER_LIGHT)
        pdf.line(margin_left, pdf.get_y(), margin_left + TOTAL_W, pdf.get_y())
        pdf.ln(3)
        pdf.set_font("DejaVu", "B", 11)
        pdf.set_text_color(*STEAM_MID)
        pdf.set_x(margin_left)
        pdf.cell(TOTAL_W, 6, "Legend & Methodology", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*TEXT_DARK)
        pdf.ln(3)

        # ── Color coding by column ──────────────────────────────────────────────
        pdf.set_font("DejaVu", "B", 8)
        pdf.set_x(margin_left)
        pdf.cell(TOTAL_W, 5, "Color coding by column", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        swatch_size = 3.5
        indent = margin_left + 1

        # Helper: draw one color legend row
        def draw_swatch_row(rgb, label, desc):
            y = pdf.get_y() + 0.3
            pdf.set_fill_color(*rgb)
            pdf.rect(indent, y, swatch_size, swatch_size, style="F")
            pdf.set_draw_color(*BORDER_LIGHT)
            pdf.rect(indent, y, swatch_size, swatch_size)
            pdf.set_xy(indent + swatch_size + 1.5, pdf.get_y())
            pdf.set_font("DejaVu", "B", 7)
            pdf.set_text_color(*TEXT_DARK)
            pdf.cell(18, 4.5, label + ":")
            pdf.set_font("DejaVu", "", 7)
            pdf.cell(0, 4.5, desc, new_x="LMARGIN", new_y="NEXT")

        # --- Historical Low column ---
        pdf.set_font("DejaVu", "B", 7.5)
        pdf.set_x(indent - 0.5)
        pdf.cell(0, 5, "Hist. Low", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(0.5)
        draw_swatch_row(BG_GREEN, "Green", "Current price is a historical new low")
        draw_swatch_row(BG_YELLOW, "Yellow", "Price matches a previous all-time low")
        pdf.ln(1.5)

        # --- Discount column ---
        pdf.set_font("DejaVu", "B", 7.5)
        pdf.set_x(indent - 0.5)
        pdf.cell(0, 5, "Discount", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(0.5)
        draw_swatch_row(BG_RED, "Red", "Poor discount: less than 60% off")
        draw_swatch_row(BG_ORANGE, "Orange", "Fair discount: 60-79% off")
        pdf.ln(1.5)

        # --- AI Fun column ---
        pdf.set_font("DejaVu", "B", 7.5)
        pdf.set_x(indent - 0.5)
        pdf.cell(0, 5, "AI Fun", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(0.5)
        draw_swatch_row(BG_RED, "Red", "Low fun rating: below 0.65")
        draw_swatch_row(BG_YELLOW, "Yellow", "Mid-range: 0.65 to 0.79")
        pdf.ln(1.5)

        # --- ProtonDB column ---
        pdf.set_font("DejaVu", "B", 7.5)
        pdf.set_x(indent - 0.5)
        pdf.cell(0, 5, "ProtonDB", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(0.5)
        draw_swatch_row(BG_RED, "Red", "Below Gold: Silver, Bronze, or Borked tier")
        pdf.ln(1.5)

        pdf.set_font("DejaVu", "", 7)
        pdf.set_text_color(*(130, 130, 130))
        pdf.set_x(indent - 0.5)
        pdf.cell(0, 4, "Unhighlighted cells met the default threshold (e.g., 80%+ discount, AI Fun >= 0.80, Gold/Platinum).",
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*TEXT_DARK)
        pdf.ln(3)

        # ── Review rating shorthand ───────────────────────────────────────────
        pdf.set_font("DejaVu", "B", 8)
        pdf.set_x(margin_left)
        pdf.cell(TOTAL_W, 5, "Review rating shorthand", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
        review_table = [
            ("+++", "Overwhelmingly Positive"),
            ("++",  "Very Positive"),
            ("+",   "Mostly Positive"),
        ]
        for code, meaning in review_table:
            pdf.set_x(indent)
            pdf.set_font("DejaVu", "B", 7.5)
            pdf.cell(12, 5, code)
            pdf.set_font("DejaVu", "", 7.5)
            pdf.cell(0, 5, meaning, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

        # ── AI fun rating ────────────────────────────────────────────────────
        pdf.set_font("DejaVu", "B", 8)
        pdf.set_x(margin_left)
        pdf.cell(TOTAL_W, 5, "AI Fun rating", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
        pdf.set_font("DejaVu", "", 7.5)
        pdf.set_x(indent)
        fun_lines = [
            "0.0-1.0 score from a multi-LLM panel (see Data sources below).",
            "Colored cells: red if < 0.65, yellow if 0.65-0.79, no highlight if >= 0.80.",
        ]
        for line in fun_lines:
            pdf.set_x(indent)
            pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

        # ── Historical low labels ────────────────────────────────────────────
        pdf.set_font("DejaVu", "B", 8)
        pdf.set_x(margin_left)
        pdf.cell(TOTAL_W, 5, "Historical low labels", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
        hist_table = [
            ("NEW LOW",  "Current price is lower than any previously recorded price."),
            ("MATCHES",  "Current price equals a previously recorded all-time low."),
            ("NOT LOW",  "Current price is above the historical lowest price."),
        ]
        for label, meaning in hist_table:
            pdf.set_x(indent)
            pdf.set_font("DejaVu", "B", 7.5)
            pdf.cell(22, 5, label)
            pdf.set_font("DejaVu", "", 7.5)
            pdf.cell(0, 5, meaning, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

        # ── Data sources ─────────────────────────────────────────────────────
        pdf.set_font("DejaVu", "B", 8)
        pdf.set_x(margin_left)
        pdf.cell(TOTAL_W, 5, "Data sources", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)
        pdf.set_font("DejaVu", "", 7.5)
        source_lines = [
            "Cart contents: Steam store, authenticated via Firefox session cookies.",
            "Game details (name, price, discount): public Steam store API.",
            "Linux support & reviews: public Steam reviews/store APIs.",
            "ProtonDB compatibility tiers: ProtonDB public API (protondb.com).",
            "Historical-low classification: parsed from a saved SteamDB wishlist page.",
            "AI fun ratings: a parallel panel of Gemini, GLM, and Codex subagents",
            "  scores each game 0-1, and the average is written to the report.",
        ]
        for line in source_lines:
            pdf.set_x(indent)
            pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        # Closing rule
        pdf.set_draw_color(*STEAM_ACCENT)
        pdf.line(margin_left, pdf.get_y(), margin_left + TOTAL_W, pdf.get_y())
        pdf.set_text_color(*TEXT_DARK)

    # ── Build the document ─────────────────────────────────────────────────────
    pdf = CartPDF()
    pdf.add_font("DejaVu", "", font_regular)
    pdf.add_font("DejaVu", "B", font_bold)
    pdf.add_font("DejaVu", "I", font_oblique)
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    draw_table(pdf, games, title=None if not dropped_games else "Active games")

    if dropped_games:
        pdf.add_page()
        draw_table(pdf, dropped_games, title="Dropped games")

    draw_legend(pdf)

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

    # When --list-dropped is active, dropped games appear only in their own table
    show_dropped_separately = args.list_dropped and dropped and not args.hide_dropped

    if args.hide_dropped or show_dropped_separately:
        games_to_show = active
    else:
        games_to_show = all_games

    if not games_to_show and not show_dropped_separately:
        print("No games to display.")
        return 0

    # PDF output
    if args.pdf is not None:
        pdf_path = Path(args.pdf)
        dropped_for_pdf = dropped if show_dropped_separately else None
        if not games_to_show and show_dropped_separately:
            # Edge case: every game is dropped; give PDF something for the main page
            pass
        if not games_to_show and not show_dropped_separately:
            return 0
        generate_pdf(games_to_show, currency, pdf_path, dropped_games=dropped_for_pdf)
        return 0

    # Terminal output
    if not games_to_show:
        # Only a dropped table will be rendered, so use that as the source for widths/pkey
        pkey = price_key_for_game(dropped[0])
        widths, visible_cols = compute_widths(dropped, currency, pkey)
        print_table(dropped, currency, widths, visible_cols, title="Dropped games")
        return 0

    pkey = price_key_for_game(games_to_show[0])
    # Compute widths over all games that will be rendered so columns align
    all_for_widths = list(games_to_show)
    if show_dropped_separately:
        all_for_widths.extend(dropped)
    widths, visible_cols = compute_widths(all_for_widths, currency, pkey)

    print_table(
        games_to_show, currency, widths, visible_cols,
        title="Active games" if show_dropped_separately else None,
    )

    if show_dropped_separately:
        print_table(dropped, currency, widths, visible_cols, title="Dropped games")

    return 0


if __name__ == "__main__":
    sys.exit(main())
