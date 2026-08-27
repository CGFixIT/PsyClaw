#!/usr/bin/env python3
"""Render the two CyClaw consoles in isolated browser contexts.

This is an optional browser lane. It requires the Playwright Python package and
an installed Chromium browser. It never contacts an external provider.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any


def _exercise(page: Any, name: str) -> None:
    if name == "terminal":
        page.locator("#queryInput").fill("what is CyClaw")
        page.locator("#sendBtn").click()
        page.locator("#results").wait_for(state="visible", timeout=15_000)
    else:
        page.locator("#input").fill("/status")
        page.locator("#send").click()
        page.locator("#stream").wait_for(state="visible", timeout=10_000)


def _render(page: Any, name: str, url: str, out: Path, mobile: bool, exercise: bool) -> list[str]:
    failures: list[str] = []
    page_errors: list[str] = []
    console_errors: list[str] = []
    request_failures: list[str] = []
    page.on("pageerror", lambda exc: page_errors.append(str(exc)))
    page.on(
        "console",
        lambda message: console_errors.append(message.text)
        if message.type == "error"
        else None,
    )
    page.on(
        "requestfailed",
        lambda request: request_failures.append(
            f"{request.method} {request.url}: {request.failure}"
        ),
    )
    try:
        page.goto(url, wait_until="networkidle", timeout=30_000)
        page.locator("body").wait_for(state="visible", timeout=5_000)
        if not page.locator("body").inner_text().strip():
            failures.append(f"{name}: body rendered empty")
        if exercise:
            _exercise(page, name)
    except Exception as exc:
        failures.append(f"{name}: browser flow failed: {type(exc).__name__}: {exc}")
    suffix = "mobile" if mobile else "desktop"
    page.screenshot(path=str(out / f"{name}-{suffix}.png"), full_page=True)
    if page_errors:
        failures.append(f"{name}: page errors: {page_errors}")
    if console_errors:
        failures.append(f"{name}: console errors: {console_errors}")
    if request_failures:
        failures.append(f"{name}: request failures: {request_failures}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default="http://127.0.0.1:8787/")
    parser.add_argument("--harness", default="http://127.0.0.1:8790/")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--no-exercise", action="store_true")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP: install playwright and a Chromium browser for rendering evidence")
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        for mobile, viewport in (
            (False, {"width": 1440, "height": 1000}),
            (True, {"width": 390, "height": 844}),
        ):
            for name, url in (("terminal", args.gateway), ("harness", args.harness)):
                context = browser.new_context(viewport=viewport, is_mobile=mobile)
                page = context.new_page()
                failures.extend(
                    _render(page, name, url, args.out, mobile, not args.no_exercise)
                )
                context.close()
        browser.close()

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print(f"PASS: browser rendering and screenshots written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
