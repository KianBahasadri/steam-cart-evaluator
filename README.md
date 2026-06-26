# Steam Cart Evaluator

Fetch your Steam shopping cart and display it with Linux compatibility info, ProtonDB ratings, and review quality.

## Example Output

```
┌───────────────────────────────────────────────┬──────────┬──────────┬───────┬──────────┬────────┐
│ Game                                          │ Price    │ Discount │ Linux │ ProtonDB │ Rating │
├───────────────────────────────────────────────┼──────────┼──────────┼───────┼──────────┼────────┤
│ QUICKERFLAK                                   │ C$ 0.64  │ -90%     │ no    │ platinum │ ++     │
│ Hotline Miami                                 │ C$ 1.29  │ -90%     │ yes   │          │ +++    │
│ The Binding of Isaac: Rebirth                 │ C$ 1.69  │ -90%     │ yes   │          │ +++    │
│ The Witcher 3: Wild Hunt                      │ C$ 5.59  │ -90%     │ no    │ platinum │ +++    │
│ Hollow Knight                                 │ C$ 9.74  │ -50%     │ yes   │          │ +++    │
│ Kenshi                                        │ C$ 13.59 │ -60%     │ no    │ platinum │ +++    │
│ Project Zomboid                               │ C$ 17.41 │ -33%     │ yes   │          │ ++     │
│ Red Dead Redemption 2                         │ C$ 19.99 │ -75%     │ no    │ gold     │ ++     │
└───────────────────────────────────────────────┴──────────┴──────────┴───────┴──────────┴────────┘

42 games · total C$ 197.58 · 16 Linux-native · 23 Proton gold+
```

### Column Legend

- **Linux**: `yes` if the game has native Linux support
- **ProtonDB**: Compatibility tier for non-native games (platinum/gold/silver/bronze/borked) — blank if Linux-native
- **Rating**: `+++` = Overwhelmingly Positive, `++` = Very Positive, `+` = Mostly Positive

### Color Coding (terminal only)

- **Discounts**: red if <60% off, yellow if <80% off
- **ProtonDB**: red if below gold tier (silver/bronze/borked)

## Usage

### Prerequisites

- Firefox with an active Steam session (logged in at store.steampowered.com)
- Python 3.8+

### Installation

```bash
pip install -r requirements.txt
```

### Fetch Cart Data

```bash
./fetch_cart.py
```

Reads Firefox cookies, fetches your Steam cart, and saves to `games.json` with:
- Game name and current price
- Discount percentage
- Linux native support status
- ProtonDB compatibility tier (for non-native games)
- Steam review quality rating

Options:
- `-o FILE` — output to custom file (default: `games.json`)
- `--debug` — print raw API responses

### Display Cart Table

```bash
./show_cart.py
```

Reads `games.json` and displays sorted table with color-coded columns.

Options:
- `-i FILE` — read from custom file (default: `games.json`)

### Wishlist Diff

```bash
./wishlist_diff.py
```

Shows games that are in your cart but **not** on your wishlist. Extracts your SteamID from Firefox cookies and fetches the wishlist via Steam's public API.

Options:
- `-i FILE` — read from custom file (default: `games.json`)

### Fun Rating

Follow `fun_rating_workflow.md` to rate each cart game for fun factor on a 0.0–1.0
scale. An orchestrator spawns per-game subagents that consult Gemini, GLM, and Codex
in parallel, consolidate their scores, and write `ai_fun_rating` to `games.json`.

## Data Sources

- **Cart data**: Steam store (requires Firefox cookies from logged-in session)
- **Game details**: Steam store API (public, no auth)
- **ProtonDB tiers**: [ProtonDB API](https://www.protondb.com/) (public)
- **Review quality**: Steam reviews API (public)
- **Wishlist**: [IWishlistService API](https://steamapi.palash.dev/reference/steam/wishlist) (public, uses your SteamID)
- **Fun ratings**: Gemini, GLM (Pioneer), and Codex agent panel (see `fun_rating_workflow.md`)
