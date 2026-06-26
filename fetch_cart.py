#!/usr/bin/env -S uv run
"""Read the current Steam shopping cart and dump it to games.json.

Uses Firefox cookies to authenticate the cart page fetch (the Steam Web API
IAccountCartService/GetCart endpoint requires a key tied to the specific
account, which is impractical for most users).  Enrichment uses the public
store API — no auth needed.

Data flow:
  1. GET /cart/ with Firefox session cookies -> parse the embedded
     `accountcart` JSON blob (packageid/bundleid + price per line item).
  2. For each package, GET /sub/{packageid}/ -> scrape the appid and
     discount % from the rendered page (no public API for this mapping).
  3. GET /api/appdetails/ for that appid -> authoritative name and
     platforms.linux flag (public, no auth needed).
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

STORE_HOST = "store.steampowered.com"
STORE_URL = f"https://{STORE_HOST}"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0"
)

# Steam internal currency_code -> ISO-ish label
CURRENCY_CODES: dict[int, str] = {
    1: "usd", 2: "gbp", 3: "eur", 4: "rub", 5: "brl",
    6: "jpy", 7: "nok", 8: "idr", 9: "myr", 10: "php",
    11: "sgd", 12: "thb", 13: "vnd", 14: "krw",
    16: "try", 17: "uah", 18: "mxn", 19: "cad",
    20: "cad", 21: "aud", 22: "nzd", 23: "inr",
    24: "hkd", 25: "twd", 26: "sar", 27: "zar",
    28: "aed", 29: "chf", 30: "clp", 31: "pen",
    32: "cop", 34: "uyu", 35: "ars",
}


# --------------------------------------------------------------------------- #
# Firefox cookie import
# --------------------------------------------------------------------------- #
def firefox_profile_dir() -> Path:
    home = Path(os.path.expanduser("~"))
    candidates = [
        home / ".mozilla/firefox",
        home / ".var/app/org.mozilla.firefox/.mozilla/firefox",
        home / "snap/firefox/common/.mozilla/firefox",
    ]
    base = next((c for c in candidates if c.is_dir()), None)
    if base is None:
        raise SystemExit(
            "Could not find a Firefox profile directory. Searched: "
            + ", ".join(str(c) for c in candidates)
        )
    return _pick_profile(base)


def _pick_profile(base: Path) -> Path:
    with_cookies = [
        c for c in base.iterdir() if c.is_dir() and (c / "cookies.sqlite").exists()
    ]
    if with_cookies:
        with_cookies.sort(
            key=lambda p: (p / "cookies.sqlite").stat().st_mtime, reverse=True
        )
        return with_cookies[0]

    profiles_ini = base / "profiles.ini"
    if profiles_ini.exists():
        from configparser import ConfigParser
        cp = ConfigParser()
        cp.read(profiles_ini)
        for section in cp.sections():
            if section.startswith("Profile") and cp.getboolean(
                section, "Default", fallback=False
            ):
                p = cp.get(section, "Path", fallback="")
                return Path(p if os.path.isabs(p) else base / p)

    defaults = sorted(base.glob("*.default-release")) or sorted(
        base.glob("*.default*")
    )
    if defaults:
        return defaults[0]
    raise SystemExit(f"No Firefox profile with cookies found under {base}")


def load_firefox_cookies(host: str) -> dict[str, str]:
    profile = firefox_profile_dir()
    cookies_db = profile / "cookies.sqlite"
    if not cookies_db.exists():
        raise SystemExit(f"cookies.sqlite not found in profile {profile}")

    with tempfile.TemporaryDirectory() as td:
        tmp_db = Path(td) / "cookies.sqlite"
        shutil.copy2(cookies_db, tmp_db)
        conn = sqlite3.connect(str(tmp_db))
        try:
            rows = conn.execute(
                "SELECT host, name, value FROM moz_cookies "
                "WHERE host = ? OR host = ?",
                (host, "." + host),
            ).fetchall()
        finally:
            conn.close()

    cookies: dict[str, str] = {}
    for host_key, name, value in rows:
        if name not in cookies or host_key.startswith("."):
            cookies[name] = value
    return cookies


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
def build_session(cookies: dict[str, str]) -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Referer": f"{STORE_URL}/"})
    for k, v in cookies.items():
        s.cookies.set(k, v, domain=STORE_HOST)
        s.cookies.set(k, v, domain="." + STORE_HOST)
    return s


def extract_accountcart(html_text: str) -> dict:
    """Pull the `accountcart` JSON blob out of the cart page HTML."""
    text = html.unescape(html_text)
    start = text.find('"accountcart":{')
    if start == -1:
        raise SystemExit(
            "Could not find accountcart blob on /cart/. "
            "You may not be logged in, or the cart is empty."
        )
    i = text.find("{", start)
    depth = 0
    j = i
    while j < len(text):
        c = text[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return json.loads(text[i : j + 1])


@dataclass
class SubInfo:
    appid: int | None
    discount: int
    title: str | None


def _parse_sub_page(text: str) -> SubInfo:
    text = html.unescape(text)
    appid_match = re.search(r'data-ds-appid="(\d+)"', text)
    appid = int(appid_match.group(1)) if appid_match else None

    discount_match = re.search(r'data-discount="(\d+)"', text)
    discount = int(discount_match.group(1)) if discount_match else 0

    title = None
    title_match = re.search(r"<title>([^<]+)</title>", text)
    if title_match:
        t = title_match.group(1)
        m = re.match(r"^(?:Save \d+% on )?(.+?) on Steam$", t)
        title = (m.group(1) if m else t).strip()

    return SubInfo(appid=appid, discount=discount, title=title)


def fetch_sub_info(session: requests.Session, package_id: int, debug: bool) -> SubInfo:
    r = session.get(
        f"{STORE_URL}/sub/{package_id}/",
        params={"cc": "ca", "l": "english"},
        timeout=20,
    )
    if not r.ok:
        if debug:
            print(f"[debug] sub {package_id} -> {r.status_code}")
        return SubInfo(appid=None, discount=0, title=None)
    return _parse_sub_page(r.text)


def fetch_bundle_info(
    session: requests.Session, bundle_id: int, debug: bool
) -> tuple[SubInfo, list[int]]:
    r = session.get(
        f"{STORE_URL}/bundle/{bundle_id}/",
        params={"cc": "ca", "l": "english"},
        timeout=20,
    )
    if not r.ok:
        if debug:
            print(f"[debug] bundle {bundle_id} -> {r.status_code}")
        return SubInfo(appid=None, discount=0, title=None), []
    text = html.unescape(r.text)
    info = _parse_sub_page(text)
    appids = [int(x) for x in re.findall(r'data-ds-appid="(\d+)"', text)]
    seen: set[int] = set()
    appids = [a for a in appids if not (a in seen or seen.add(a))]
    return info, appids


PROTONDB_API = "https://www.protondb.com/api/v1/reports/summaries"


def fetch_protondb_tier(appid: int, debug: bool) -> str | None:
    """Fetch the ProtonDB compatibility tier for an appid.

    Returns one of: platinum, gold, silver, bronze, borked, or None.
    """
    r = requests.get(f"{PROTONDB_API}/{appid}.json", timeout=10)
    if not r.ok:
        if debug:
            print(f"[debug] protondb {appid} -> {r.status_code}")
        return None
    data = r.json()
    return data.get("tier")


def fetch_app_reviews(appid: int, debug: bool) -> str | None:
    """Fetch review summary (e.g., 'Very Positive') for an appid."""
    r = requests.get(
        f"{STORE_URL}/appreviews/{appid}",
        params={"json": "1", "num_per_page": "0"},
        timeout=20,
    )
    if not r.ok:
        if debug:
            print(f"[debug] appreviews {appid} -> {r.status_code}")
        return None
    data = r.json()
    if not data.get("success"):
        return None
    query_summary = data.get("query_summary", {})
    return query_summary.get("review_score_desc")


def fetch_app_details(appid: int, debug: bool) -> dict | None:
    r = requests.get(
        f"{STORE_URL}/api/appdetails/",
        params={"appids": appid, "cc": "ca", "l": "english"},
        timeout=20,
    )
    if not r.ok:
        if debug:
            print(f"[debug] appdetails {appid} -> {r.status_code}")
        return None
    entry = r.json().get(str(appid), {})
    if not entry.get("success"):
        return None
    return entry.get("data")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description="Dump your Steam cart to games.json")
    ap.add_argument("-o", "--output", default="games.json")
    ap.add_argument(
        "--refresh",
        action="store_true",
        help="Re-fetch details for all games (default: skip games already in file, only add new ones)",
    )
    ap.add_argument("--debug", action="store_true", help="Print raw responses")
    args = ap.parse_args()

    # Load existing data so we can skip already-known games (unless --refresh)
    out_path = Path(args.output)
    existing_by_appid: dict[int, dict] = {}
    if not args.refresh and out_path.exists():
        try:
            old_data = json.loads(out_path.read_text())
            for og in old_data.get("games", []):
                aid = og.get("appid")
                if aid is not None:
                    existing_by_appid[aid] = og
        except (json.JSONDecodeError, KeyError):
            pass

    print("Reading Steam cookies from Firefox...")
    cookies = load_firefox_cookies(STORE_HOST)
    if "steamLoginSecure" not in cookies:
        raise SystemExit(
            "Missing steamLoginSecure cookie. Make sure you're logged in to "
            "store.steampowered.com in Firefox."
        )
    print(f"  found {len(cookies)} cookies (steamLoginSecure present)")

    session = build_session(cookies)

    print("Fetching cart page...")
    r = session.get(f"{STORE_URL}/cart/", timeout=20)
    r.raise_for_status()
    cart_data = extract_accountcart(r.text)
    line_items = cart_data.get("cart", {}).get("line_items", []) or []
    if not line_items:
        print("Your cart is empty.")
        # Mark all existing games as removed instead of wiping them
        empty_games: list[dict] = []
        if out_path.exists():
            try:
                old = json.loads(out_path.read_text())
                for og in old.get("games", []):
                    og["removed"] = True
                    empty_games.append(og)
            except (json.JSONDecodeError, KeyError):
                pass
        out_path.write_text(
            json.dumps({"currency": None, "count": 0, "games": empty_games}, indent=2)
        )
        if empty_games:
            print(f"  marked {len(empty_games)} previously-tracked game(s) as removed")
        return 0

    first_price = line_items[0].get("price_when_added", {}) or {}
    ccy_code = first_price.get("currency_code")
    currency = CURRENCY_CODES.get(ccy_code, "cad")

    print(f"Found {len(line_items)} item(s). Resolving details...")
    games: list[dict] = []
    price_key = f"price_{currency}"

    for idx, item in enumerate(line_items, 1):
        package_id = item.get("packageid")
        bundle_id = item.get("bundleid")
        price_blob = item.get("price_when_added", {}) or {}
        final_price = int(price_blob.get("amount_in_cents", 0)) / 100.0
        formatted = price_blob.get("formatted_amount", "")

        if package_id is None and bundle_id is None:
            print(
                f"  [{idx:>2}/{len(line_items)}] (no package/bundle id; skipping) "
                f"{formatted or f'{final_price:.2f}'}"
            )
            games.append({
                "name": item.get("name", "(no id)"),
                "appid": None,
                price_key: round(final_price, 2),
                "discount_percentage": None,
                "linux_native": None,
            })
            continue

        appid: int | None = None

        if bundle_id is not None:
            sub, appids = fetch_bundle_info(session, int(bundle_id), args.debug)
            appid = appids[0] if appids else sub.appid
        else:
            sub = fetch_sub_info(session, int(package_id), args.debug)
            appids = []
            appid = sub.appid

        # Default mode: skip re-fetching games already in the file
        if not args.refresh and appid is not None and appid in existing_by_appid:
            old = existing_by_appid[appid]
            old[price_key] = round(final_price, 2)
            games.append(old)
            print(
                f"  [{idx:>2}/{len(line_items)}] {old.get('name', '?')} "
                f"(already known, skipped)"
            )
            continue

        name = sub.title
        linux_native = False

        if bundle_id is not None:
            if appids:
                appid = appids[0]  # representative appid for ProtonDB lookup
                results = []
                for aid in appids:
                    details = fetch_app_details(aid, args.debug)
                    if details:
                        results.append(bool(details.get("platforms", {}).get("linux")))
                    time.sleep(0.3)
                linux_native = bool(results) and all(results)
        else:
            if sub.appid:
                details = fetch_app_details(sub.appid, args.debug)
                if details:
                    name = details.get("name") or name
                    linux_native = bool(
                        details.get("platforms", {}).get("linux")
                    )

        # ProtonDB tier — only for non-Linux-native games with an appid
        protondb_tier: str | None = None
        if not linux_native and appid is not None:
            protondb_tier = fetch_protondb_tier(appid, args.debug)
            time.sleep(0.3)

        # Review quality
        review_score: str | None = None
        if appid is not None:
            review_score = fetch_app_reviews(appid, args.debug)
            time.sleep(0.3)

        games.append({
            "name": name or f"(package {package_id})",
            "appid": appid,
            price_key: round(final_price, 2),
            "discount_percentage": sub.discount,
            "linux_native": linux_native,
            "protondb_tier": protondb_tier,
            "review_score": review_score,
        })
        flag = "linux" if linux_native else f"proton:{protondb_tier or '—'}"
        review_str = f" [{review_score}]" if review_score else ""
        print(
            f"  [{idx:>2}/{len(line_items)}] {name or '?'}: "
            f"{formatted or f'{final_price:.2f}'} -{sub.discount}% [{flag}]{review_str}"
        )
        time.sleep(0.5)

    # Merge: preserve extra fields, track removed games
    removed: list[dict] = []
    out_path = Path(args.output)
    if out_path.exists():
        try:
            old_data = json.loads(out_path.read_text())
            old_by_appid: dict[int, dict] = {}
            for og in old_data.get("games", []):
                aid = og.get("appid")
                if aid is not None:
                    old_by_appid[aid] = og
            # Preserve extra fields on current games
            cart_appids: set[int] = set()
            for g in games:
                aid = g.get("appid")
                if aid is not None:
                    cart_appids.add(aid)
                    # Clear removed flag if game is back in the cart
                    if aid in old_by_appid and old_by_appid[aid].get("removed"):
                        g.pop("removed", None)
                if aid is not None and aid in old_by_appid:
                    for k, v in old_by_appid[aid].items():
                        if k not in g:
                            g[k] = v
            # Mark games no longer in the cart as removed; preserve previously-removed ones
            for aid, og in old_by_appid.items():
                if aid not in cart_appids:
                    if not og.get("removed"):
                        og["removed"] = True
                    removed.append(og)
        except (json.JSONDecodeError, KeyError):
            pass

    all_games = games + removed
    out = {"currency": currency, "count": len(games), "games": all_games}
    out_path.write_text(
        json.dumps(out, indent=2, ensure_ascii=False)
    )
    msg = f"\nWrote {len(games)} game(s) to {args.output}"
    if removed:
        msg += f" ({len(removed)} removed)"
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
