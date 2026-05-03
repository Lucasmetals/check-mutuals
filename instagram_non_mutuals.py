"""
Compare Instagram followers and following and print non-mutual accounts.

By default, this script works with Instagram's official "Download your information"
export. It can also use a persistent Playwright browser profile, where you log in
once and later runs reuse the saved session.

Common input files:
  followers_and_following/followers_1.json
  followers_and_following/following.json

Examples:
  python instagram_non_mutuals.py --followers followers_1.json --following following.json
  python instagram_non_mutuals.py --export-dir instagram-export/followers_and_following
  python instagram_non_mutuals.py --export-dir instagram-export --csv non_mutuals.csv
  python instagram_non_mutuals.py
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from urllib.parse import urlencode
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


INSTAGRAM_PROFILE_RE = re.compile(
    r"(?:https?://(?:www\.)?instagram\.com/)?@?([A-Za-z0-9._]+)"
)
RESERVED_INSTAGRAM_PATHS = {
    "accounts",
    "about",
    "api",
    "developer",
    "direct",
    "explore",
    "p",
    "privacy",
    "reel",
    "stories",
}


class InstagramLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.usernames: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return

        for name, value in attrs:
            if name == "href" and value:
                username = username_from_text(value)
                if username:
                    self.usernames.add(username)

    def handle_data(self, data: str) -> None:
        username = username_from_text(data)
        if username:
            self.usernames.add(username)


def username_from_text(value: str) -> str | None:
    match = INSTAGRAM_PROFILE_RE.search(value.strip())
    if not match:
        return None

    username = match.group(1).strip(".").lower()
    if username in RESERVED_INSTAGRAM_PATHS:
        return None

    return username


def username_from_href(href: str) -> str | None:
    href = href.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    parts = [part for part in href.split("/") if part]
    if not parts:
        return None

    username = parts[-1].lower()
    if username in RESERVED_INSTAGRAM_PATHS:
        return None

    return username_from_text(username)


def walk_values(data: Any) -> Iterable[str]:
    if isinstance(data, dict):
        value = data.get("value")
        href = data.get("href")

        if isinstance(value, str):
            yield value
        if isinstance(href, str):
            yield href

        for child in data.values():
            yield from walk_values(child)
    elif isinstance(data, list):
        for item in data:
            yield from walk_values(item)
    elif isinstance(data, str):
        yield data


def load_usernames(path: Path) -> set[str]:
    suffix = path.suffix.lower()

    if suffix == ".json":
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        usernames = {
            username
            for value in walk_values(data)
            if (username := username_from_text(value))
        }
        return usernames

    if suffix in {".html", ".htm"}:
        parser = InstagramLinkParser()
        parser.feed(path.read_text(encoding="utf-8"))
        return parser.usernames

    raise ValueError(f"Unsupported file type: {path}")


def find_export_file(export_dir: Path, patterns: list[str]) -> Path:
    matches: list[Path] = []
    for pattern in patterns:
        matches.extend(export_dir.rglob(pattern))

    if not matches:
        raise FileNotFoundError(
            f"Could not find any of these files under {export_dir}: {', '.join(patterns)}"
        )

    return sorted(matches, key=lambda item: len(item.parts))[0]


def write_csv(path: Path, not_following_back: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["username"])

        for username in not_following_back:
            writer.writerow([username])


def print_results(
    followers_label: str,
    following_label: str,
    followers: set[str],
    following: set[str],
    csv_path: Path | None,
) -> None:
    not_following_back = sorted(following - followers)

    print(f"Followers source: {followers_label}")
    print(f"Following source: {following_label}")
    print(f"Followers: {len(followers)}")
    print(f"Following: {len(following)}")
    print()

    print(f"Accounts you follow that do not follow you back ({len(not_following_back)}):")
    for username in not_following_back:
        print(f"  {username}")

    if csv_path:
        write_csv(csv_path, not_following_back)
        print()
        print(f"Wrote CSV: {csv_path}")


def ensure_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise SystemExit(
            "Browser mode requires Playwright.\n"
            f"Python executable: {sys.executable}\n"
            f"Import error: {error!r}\n"
            "Install it with:\n"
            "  py -m pip install playwright\n"
            "  py -m playwright install chromium"
        )

    return sync_playwright, PlaywrightTimeoutError


def login_diagnostics(page: Any) -> dict[str, int | str]:
    page.wait_for_timeout(1000)
    return {
        "url": page.url,
        "login_url": int("/accounts/login" in page.url),
        "login_fields": page.locator("input[name='username'], input[name='password']").count(),
        "login_text": page.get_by_text("Log in to Instagram", exact=True).count(),
        "auth_wall": page.get_by_text("See photos, videos and more", exact=False).count(),
        "signup_buttons": page.get_by_role("button", name="Sign up").count(),
        "login_links": page.get_by_role("link", name="Log in").count(),
    }


def print_login_diagnostics(page: Any) -> None:
    diagnostics = login_diagnostics(page)
    print(
        "Login diagnostics: "
        f"url={diagnostics['url']!r}, "
        f"login_url={diagnostics['login_url']}, "
        f"login_fields={diagnostics['login_fields']}, "
        f"login_text={diagnostics['login_text']}, "
        f"auth_wall={diagnostics['auth_wall']}, "
        f"signup_buttons={diagnostics['signup_buttons']}, "
        f"login_links={diagnostics['login_links']}"
    )


def page_needs_login(page: Any) -> bool:
    diagnostics = login_diagnostics(page)
    return any(
        int(diagnostics[key]) > 0
        for key in ("login_url", "login_fields", "login_text", "auth_wall", "signup_buttons")
    )


def prepare_browser_session(page: Any, username: str, pause_before_scrape: bool) -> None:
    print("Preparing browser session...")
    page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded")
    print_login_diagnostics(page)

    if pause_before_scrape:
        print("The browser is open on the Instagram profile.")
        print("Log in if needed, close any popups, and make sure the profile page is visible.")
        input("Press Enter here to start scraping followers/following...")
        page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded")
        print_login_diagnostics(page)

    while page_needs_login(page):
        print_login_diagnostics(page)
        print("Instagram is not showing a logged-in session yet.")
        print("Use the browser window to log in. If you see the sign-up wall, click 'Log in'.")
        input("Press Enter here after the profile is visible while logged in...")
        page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded")

    print_login_diagnostics(page)
    print("Login state: ready to scrape.")


def get_profile_user_id(page: Any, username: str, timeout_ms: int) -> str:
    response = page.context.request.get(
        f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}",
        headers={
            "x-ig-app-id": "936619743392459",
            "x-requested-with": "XMLHttpRequest",
        },
        timeout=timeout_ms,
    )

    if response.ok:
        data = response.json()
        user_id = data.get("data", {}).get("user", {}).get("id")
        if user_id:
            return str(user_id)

    page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded")
    content = page.content()
    patterns = [
        rf'"id"\s*:\s*"(\d+)"\s*,\s*"username"\s*:\s*"{re.escape(username)}"',
        rf'"profile_id"\s*:\s*"(\d+)"',
        rf'"user_id"\s*:\s*"(\d+)"',
    ]

    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            return match.group(1)

    raise RuntimeError(f"Could not find Instagram user id for @{username}.")


def instagram_api_get(page: Any, path: str, params: dict[str, str], timeout_ms: int) -> dict[str, Any]:
    query = urlencode(params)
    response = page.context.request.get(
        f"https://www.instagram.com{path}?{query}",
        headers={
            "x-ig-app-id": "936619743392459",
            "x-requested-with": "XMLHttpRequest",
        },
        timeout=timeout_ms,
    )

    if not response.ok:
        raise RuntimeError(f"Instagram API request failed: HTTP {response.status} {path}")

    return response.json()


def fetch_relationship_api(
    page: Any,
    user_id: str,
    relationship: str,
    timeout_ms: int,
) -> set[str]:
    usernames: set[str] = set()
    max_id: str | None = None
    page_number = 1

    while True:
        params = {
            "count": "200",
            "search_surface": "follow_list_page",
        }
        if max_id:
            params["max_id"] = max_id

        data = instagram_api_get(
            page,
            f"/api/v1/friendships/{user_id}/{relationship}/",
            params,
            timeout_ms,
        )

        users = data.get("users", [])
        for user in users:
            username = user.get("username")
            if isinstance(username, str) and username:
                usernames.add(username.lower())

        print(f"  API page {page_number}: collected {len(usernames)} {relationship}.")

        max_id = data.get("next_max_id")
        if not max_id:
            break

        page_number += 1
        time.sleep(1)

    return usernames


def wait_for_login_if_needed(page: Any, username: str) -> None:
    print(f"Current page: {page.url}")
    if not page_needs_login(page):
        print("Login state: already authenticated.")
        return

    while page_needs_login(page):
        print_login_diagnostics(page)
        print("Login state: Instagram is asking for login.")
        print("Log in in the browser window, then come back here and press Enter.")
        input()
        page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded")
        print(f"Current page after login check: {page.url}")


def open_relationship_dialog(page: Any, username: str, relationship: str, timeout_ms: int) -> None:
    page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded")
    wait_for_login_if_needed(page, username)
    if page_needs_login(page):
        raise RuntimeError("Cannot open relationship list while Instagram is still asking for login.")

    try:
        page.locator(f"a[href='/{username}/{relationship}/']").first.click(timeout=timeout_ms)
        page.locator("div[role='dialog']").last.wait_for(timeout=timeout_ms)
        print(f"Opened {relationship} dialog.")
        return
    except Exception as click_error:
        print(f"Could not open {relationship} from profile link: {click_error!r}")

    page.goto(
        f"https://www.instagram.com/{username}/{relationship}/",
        wait_until="domcontentloaded",
    )
    wait_for_login_if_needed(page, username)
    page.locator("div[role='dialog']").last.wait_for(timeout=timeout_ms)
    print(f"Opened {relationship} dialog from direct URL.")


def dialog_usernames(page: Any) -> set[str]:
    hrefs = page.locator("div[role='dialog'] a[href]").evaluate_all(
        "(links) => links.map((link) => link.getAttribute('href'))"
    )
    usernames = {
        username
        for href in hrefs
        if isinstance(href, str)
        if (username := username_from_href(href))
    }
    return usernames


def scroll_relationship_dialog(page: Any) -> int:
    return page.locator("div[role='dialog']").last.evaluate(
        """
        (dialog) => {
          const candidates = [dialog, ...dialog.querySelectorAll('div')];
          let scrollable = dialog;

          for (const candidate of candidates) {
            if (candidate.scrollHeight > candidate.clientHeight + 20) {
              scrollable = candidate;
            }
          }

          const before = scrollable.scrollTop;
          scrollable.scrollTop = scrollable.scrollHeight;
          return Math.round(scrollable.scrollTop - before);
        }
        """
    )


def scrape_relationship(
    page: Any,
    username: str,
    relationship: str,
    timeout_ms: int,
    scroll_wait_seconds: float,
    max_idle_scrolls: int,
) -> set[str]:
    print(f"Opening {relationship} list...")
    open_relationship_dialog(page, username, relationship, timeout_ms)
    if page_needs_login(page):
        raise RuntimeError("Instagram returned to the login page before scraping could start.")

    usernames: set[str] = set()
    idle_scrolls = 0

    while idle_scrolls < max_idle_scrolls:
        before_count = len(usernames)
        usernames.update(dialog_usernames(page))
        moved_by = scroll_relationship_dialog(page)
        time.sleep(scroll_wait_seconds)
        usernames.update(dialog_usernames(page))

        if len(usernames) == before_count and moved_by == 0:
            idle_scrolls += 1
        else:
            idle_scrolls = 0

        print(f"  Collected {len(usernames)} {relationship}...", end="\r")

    print(f"  Collected {len(usernames)} {relationship}.   ")
    return usernames


def scrape_with_browser(
    username: str,
    profile_dir: Path,
    timeout_ms: int,
    scroll_wait_seconds: float,
    max_idle_scrolls: int,
    pause_before_scrape: bool,
) -> tuple[set[str], set[str]]:
    sync_playwright, _ = ensure_playwright()
    profile_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=False,
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()

        try:
            prepare_browser_session(page, username, pause_before_scrape)
            user_id = get_profile_user_id(page, username, timeout_ms)
            print(f"Instagram user id for @{username}: {user_id}")
            print("Fetching followers via authenticated Instagram API...")
            followers = fetch_relationship_api(page, user_id, "followers", timeout_ms)
            print("Fetching following via authenticated Instagram API...")
            following = fetch_relationship_api(page, user_id, "following", timeout_ms)
        finally:
            context.close()

    return followers, following


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find Instagram accounts that are not mutual followers."
    )
    parser.add_argument(
        "--export-dir",
        type=Path,
        help="Instagram export directory. The script will auto-detect follower/following files.",
    )
    parser.add_argument(
        "--browser-profile",
        type=Path,
        default=Path(".instagram_browser_profile"),
        help="Directory for the saved browser session used by interactive browser mode.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30000,
        help="Browser wait timeout in milliseconds.",
    )
    parser.add_argument(
        "--scroll-wait",
        type=float,
        default=1.0,
        help="Seconds to wait after each dialog scroll in browser mode.",
    )
    parser.add_argument(
        "--max-idle-scrolls",
        type=int,
        default=5,
        help="Stop browser scraping after this many scrolls without new accounts.",
    )
    parser.add_argument(
        "--no-login-pause",
        action="store_true",
        help="Do not pause before scraping in browser mode.",
    )
    parser.add_argument("--followers", type=Path, help="Path to followers_1.json/html.")
    parser.add_argument("--following", type=Path, help="Path to following.json/html.")
    parser.add_argument("--csv", type=Path, help="Optional output CSV path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.export_dir:
        followers_path = args.followers or find_export_file(
            args.export_dir,
            ["followers_*.json", "followers.json", "followers_*.html", "followers.html"],
        )
        following_path = args.following or find_export_file(
            args.export_dir,
            ["following.json", "following.html"],
        )
    elif args.followers or args.following:
        if not args.followers or not args.following:
            raise SystemExit("Provide both --followers and --following.")
        followers_path = args.followers
        following_path = args.following
    else:
        username = input("Instagram username to check: ").strip().lstrip("@").lower()
        if not username:
            raise SystemExit("Username is required.")

        followers, following = scrape_with_browser(
            username,
            args.browser_profile,
            args.timeout,
            args.scroll_wait,
            args.max_idle_scrolls,
            not args.no_login_pause,
        )
        print_results(
            f"Instagram browser session for @{username}",
            f"Instagram browser session for @{username}",
            followers,
            following,
            args.csv,
        )
        return 0

    followers = load_usernames(followers_path)
    following = load_usernames(following_path)

    print_results(
        str(followers_path),
        str(following_path),
        followers,
        following,
        args.csv,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
