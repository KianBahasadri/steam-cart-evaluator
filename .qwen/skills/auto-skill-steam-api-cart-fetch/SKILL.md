---
name: steam-api-cart-fetch
description: Migrate a Steam cart scraping script from Firefox cookie auth to the official Steam Web API while preserving downstream JSON output
source: auto-skill
extracted_at: '2026-06-25T00:00:00.000Z'
---

# Steam Cart: Cookie Auth → Official Web API

## When to use
Rewriting a Steam cart fetcher (or any Steam store script) that currently relies on browser-cookie import (`steamLoginSecure` from Firefox `cookies.sqlite`) to use the official Steam Web API instead.

## Authentication
- Load `STEAM_API_KEY` from `.env` via `python-dotenv`.
- Pass `?key=...` on every `api.steampowered.com` call. No browser cookies needed.

## Step 1 — Fetch cart contents via official API
- Endpoint: `https://api.steampowered.com/IAccountCartService/GetCart/v1/`
- Method: GET
- Params: `key=API_KEY`, `user_country=ca` (or any ISO country code for pricing locality)
- Response shape:
  ```json
  {
    "response": {
      "cart": {
        "packages": [
          {
            "lineitemid": "...",
            "packageid": 38846,
            "name": "Superpower 2",
            "price": { "currency": "EUR", "initial": 999, "final": 99, "discount_percent": 90 }
          }
        ]
      }
    }
  }
  ```
- The cart API gives you `packageid`/`bundleid`, `name`, and current price (in cents, with `discount_percent`) directly — no need to scrape the `/cart/` HTML for the `accountcart` blob.

## Step 2 — Package → appid mapping (STILL needs HTML scraping)
**There is no public API for this.** You must keep the `/sub/{packageid}/` scrape.
- Scrape `data-ds-appid="(\d+)"` from the rendered page to get the appid.
- Same applies for bundles: scrape `/bundle/{bundleid}/` and collect all `data-ds-appid` values.
- Pass `?cc=ca&l=english` for consistent localization.

## Step 3 — App details (public, no auth)
- Endpoint: `https://store.steampowered.com/api/appdetails/?appids={id}&cc=ca&l=english`
- Already public — no API key required. Use for:
  - Authoritative `name`
  - `platforms.linux` boolean
  - `price_overview.discount_percent` as a fallback discount source
- Rate limit: ~200 requests / 5 minutes. Sleep 0.3–0.5s between calls.

## Step 4 — Output schema (preserve for downstream)
The companion `show_cart.py` expects this exact shape in `games.json`:
```json
{
  "currency": "cad",
  "count": 37,
  "games": [
    {
      "name": "...",
      "price_cad": 5.59,
      "discount_percentage": 90,
      "linux_native": false
    }
  ]
}
```
- The price key is `price_<currency>` where currency is lowercase ISO.
- `linux_native` = `true`/`false`/`null`. For bundles, only `true` if **every** app in the bundle supports Linux.

## What you can remove after migrating
- All Firefox profile detection (`~/.mozilla/firefox`, Flatpak, Snap paths)
- `cookies.sqlite` copy-to-temp + `sqlite3` parsing
- `session-cookies` building (`steamLoginSecure` session)
- `accountcart` HTML blob parsing with brace-matcher
- `CURRENCY_CODES` int→string map (cart API returns currency strings directly)

## Gotchas
- GetCart needs the API key to belong to the *same Steam account* whose cart you want to read.
- Bundle items: iterate every appid for Linux check; short-circuiting on the first non-Linux app saves API calls but breaks the count.
- Cart API `final` and `initial` prices are in **cents**; divide by 100 before writing.
