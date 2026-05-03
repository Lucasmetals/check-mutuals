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
  python instagram_non_mutuals.py --browser your_username
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
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


def write_csv(path: Path, not_following_back: list[str], you_do_not_follow_back: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["type", "username"])

        for username in not_following_back:
            writer.writerow(["not_following_you_back", username])
        for username in you_do_not_follow_back:
            writer.writerow(["you_do_not_follow_back", username])


def print_results(
    followers_label: str,
    following_label: str,
    followers: set[str],
    following: set[str],
    csv_path: Path | None,
) -> None:
    not_following_back = sorted(following - followers)
    you_do_not_follow_back = sorted(followers - following)

    print(f"Followers source: {followers_label}")
    print(f"Following source: {following_label}")
    print(f"Followers: {len(followers)}")
    print(f"Following: {len(following)}")
    print()

    print(f"Accounts you follow that do not follow you back ({len(not_following_back)}):")
    for username in not_following_back:
        print(f"  {username}")

    print()
    print(f"Accounts following you that you do not follow back ({len(you_do_not_follow_back)}):")
    for username in you_do_not_follow_back:
        print(f"  {username}")

    if csv_path:
        write_csv(csv_path, not_following_back, you_do_not_follow_back)
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


def wait_for_login_if_needed(page: Any, username: str) -> None:
    if "/accounts/login" not in page.url:
        return

    print("Instagram needs you to log in.")
    print("Use the browser window that opened, then come back here and press Enter.")
    input()
    page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded")


def open_relationship_dialog(page: Any, username: str, relationship: str, timeout_ms: int) -> None:
    page.goto(
        f"https://www.instagram.com/{username}/{relationship}/",
        wait_until="domcontentloaded",
    )
    wait_for_login_if_needed(page, username)

    try:
        page.locator("div[role='dialog']").last.wait_for(timeout=timeout_ms)
        return
    except Exception:
        pass

    page.goto(f"https://www.instagram.com/{username}/", wait_until="domcontentloaded")
    wait_for_login_if_needed(page, username)
    page.locator(f"a[href='/{username}/{relationship}/']").first.click(timeout=timeout_ms)
    page.locator("div[role='dialog']").last.wait_for(timeout=timeout_ms)


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
            followers = scrape_relationship(
                page,
                username,
                "followers",
                timeout_ms,
                scroll_wait_seconds,
                max_idle_scrolls,
            )
            following = scrape_relationship(
                page,
                username,
                "following",
                timeout_ms,
                scroll_wait_seconds,
                max_idle_scrolls,
            )
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
        "--browser",
        metavar="USERNAME",
        help="Use a persistent browser session to scrape followers/following for this account.",
    )
    parser.add_argument(
        "--browser-profile",
        type=Path,
        default=Path(".instagram_browser_profile"),
        help="Directory for the saved browser session used by --browser.",
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
    parser.add_argument("--followers", type=Path, help="Path to followers_1.json/html.")
    parser.add_argument("--following", type=Path, help="Path to following.json/html.")
    parser.add_argument("--csv", type=Path, help="Optional output CSV path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.browser:
        username = args.browser.strip().lstrip("@").lower()
        followers, following = scrape_with_browser(
            username,
            args.browser_profile,
            args.timeout,
            args.scroll_wait,
            args.max_idle_scrolls,
        )
        print_results(
            f"Instagram browser session for @{username}",
            f"Instagram browser session for @{username}",
            followers,
            following,
            args.csv,
        )
        return 0

    if args.export_dir:
        followers_path = args.followers or find_export_file(
            args.export_dir,
            ["followers_*.json", "followers.json", "followers_*.html", "followers.html"],
        )
        following_path = args.following or find_export_file(
            args.export_dir,
            ["following.json", "following.html"],
        )
    else:
        if not args.followers or not args.following:
            raise SystemExit("Provide --export-dir or both --followers and --following.")
        followers_path = args.followers
        following_path = args.following

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
