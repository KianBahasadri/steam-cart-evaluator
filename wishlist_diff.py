#!/usr/bin/env -S uv run
"""Show games that are in the Steam cart but NOT on the wishlist.

Reads games.json (produced by fetch_cart.py) for cart contents, fetches the
user's wishlist via the IWishlistService API (using the SteamID extracted
from Firefox cookies), and prints the diff.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path

import requests

import re

from fetch_cart import (
    STORE_HOST,
    STORE_URL,
    build_session,
    load_firefox_cookies,
)

WISHLIST_API = (
    "https://api.steampowered.com/IWishlistService/GetWishlist/v1"
)
ADD_WISHLIST_URL = f"{STORE_URL}/api/addtowishlist/"


def extract_sessionid(html_text: str) -> str | None:
    for pattern in (
        r'sessionid["\s:=]+(["\']?)([a-zA-Z0-9]{24,})\1',
        r'g_sessionID\s*=\s*["\']([^"\']+)["\']',
        r'<input[^>]+name=["\']sessionid["\'][^>]+value=["\']([^"\']+)["\']',
    ):
        m = re.search(pattern, html_text)
        if m:
            return m.group(m.lastindex)
    return None


def steamid_from_cookies(cookies: dict[str, str]) -> int | None:
    token = cookies.get("steamLoginSecure", "")
    decoded = urllib.parse.unquote(token)
    if not decoded:
        return None
    sid = decoded.split("|")[0]
    if sid.isdigit() and len(sid) >= 17:
        return int(sid)
    return None


def fetch_wishlist_appids(steamid: int) -> set[int]:
    r = requests.get(
        WISHLIST_API,
        params={"steamid": steamid},
        timeout=20,
    )
    if not r.ok:
        print(
            f"Warning: wishlist API returned {r.status_code}",
            file=sys.stderr,
        )
        return set()
    data = r.json().get("response", {})
    items = data.get("items") or []
    return {int(item["appid"]) for item in items if "appid" in item}


def add_to_wishlist(
    session: requests.Session, appid: int, sessionid: str
) -> bool:
    r = session.post(
        ADD_WISHLIST_URL,
        data={"appid": appid, "sessionid": sessionid},
        timeout=20,
    )
    return r.ok


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Show cart games not on your wishlist"
    )
    ap.add_argument(
        "-i",
        "--input",
        default="games.json",
        help="Input JSON file from fetch_cart.py (default: games.json)",
    )
    ap.add_argument(
        "--add",
        action="store_true",
        help="Add non-wishlisted games to your wishlist",
    )
    args = ap.parse_args()

    path = Path(args.input)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    data = json.loads(path.read_text())
    cart_games = [g for g in data.get("games", []) if not g.get("removed")]
    if not cart_games:
        print("No games in cart file.")
        return 0

    print("Reading Steam cookies from Firefox...")
    cookies = load_firefox_cookies(STORE_HOST)
    steamid = steamid_from_cookies(cookies)
    if steamid is None:
        print(
            "Could not extract SteamID from cookies. "
            "Make sure you're logged in to Steam in Firefox.",
            file=sys.stderr,
        )
        return 1

    print(f"Fetching wishlist for SteamID {steamid}...")
    wishlist = fetch_wishlist_appids(steamid)
    print(f"  found {len(wishlist)} wishlist item(s)")

    not_wishlisted: list[dict] = []
    no_appid: list[dict] = []
    for game in cart_games:
        appid = game.get("appid")
        if appid is None:
            no_appid.append(game)
        elif int(appid) not in wishlist:
            not_wishlisted.append(game)

    if not not_wishlisted and not no_appid:
        print("\nAll cart games are on your wishlist!")
        return 0

    if not_wishlisted:
        print(f"\n{len(not_wishlisted)} game(s) in cart but NOT on wishlist:\n")
        for game in not_wishlisted:
            name = game.get("name", "?")
            appid = game.get("appid", "?")
            print(f"  {name} (appid {appid})")
    else:
        print()

    if no_appid:
        print(
            f"  ({len(no_appid)} game(s) could not be compared — "
            "no appid; re-run fetch_cart.py)"
        )

    if args.add and not_wishlisted:
        print("\nFetching cart page to extract session ID...")
        session = build_session(cookies)
        cart_resp = session.get(f"{STORE_URL}/cart/", timeout=20)
        cart_resp.raise_for_status()

        sessionid = extract_sessionid(cart_resp.text)
        if not sessionid:
            print(
                "\nCannot add to wishlist: could not extract sessionid from cart page",
                file=sys.stderr,
            )
            return 1

        print(f"Adding {len(not_wishlisted)} game(s) to wishlist...")
        added = 0
        for game in not_wishlisted:
            appid = int(game.get("appid", 0))
            name = game.get("name", "?")
            if add_to_wishlist(session, appid, sessionid):
                print(f"  ✓ added {name}")
                added += 1
            else:
                print(f"  ✗ failed to add {name}", file=sys.stderr)
        print(f"\nAdded {added}/{len(not_wishlisted)} game(s) to wishlist")

    return 0


if __name__ == "__main__":
    sys.exit(main())
