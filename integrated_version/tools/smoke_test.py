#!/usr/bin/env python3
"""Run the smoke test and the engine verification in a real browser, headless.

WHY A BROWSER AND NOT A TEST RUNNER
-----------------------------------
The thing being tested is a single HTML file that compiles its own JSX at load
time with Babel standalone. There is no build step to hook into and no module
graph to import: the only runtime that can honestly tell you whether
``index.html`` works is a browser loading ``index.html``.

So this script starts a local server, points headless Chrome (or Edge) at the
two test pages, and reads the verdict out of the DOM they render:

    tools/verify.html   does the browser engine agree with the Python pipeline?
    tools/smoke.html    does the screen actually render, against real data?
    tools/e2e.html      does the journey work, clicked through like a teacher?

Both pages are perfectly usable by hand - open them and look. This script is
for running them without a click.

Usage::

    python tools/smoke_test.py            # all three pages
    python tools/smoke_test.py --page smoke
    python tools/smoke_test.py --keep     # leave the server up afterwards
"""

from __future__ import annotations

import argparse
import functools
import http.server
import os
import re
import socket
import subprocess
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))

BROWSERS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def find_browser():
    for path in BROWSERS:
        if os.path.exists(path):
            return path
    return None


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def serve(directory, port):
    """A quiet static server on a background thread, rooted at the app."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
    handler.log_message = lambda *args, **kwargs: None            # noqa: ARG005
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def dump_dom(browser, url, seconds=30):
    """Load a page headless and return the DOM it ended up with.

    ``--virtual-time-budget`` is what makes this deterministic: the browser
    runs its clock forward as fast as the page allows and only then dumps, so
    a page that waits on a fetch and two render passes is finished rather than
    caught halfway.
    """
    result = subprocess.run(
        [browser, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--disable-dev-shm-usage", "--virtual-time-budget=%d" % (seconds * 1000),
         "--dump-dom", url],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=seconds + 60)
    return result.stdout or ""


def strip_tags(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def read_verdict(dom, page):
    """Pull the pass/fail line and the per-check list out of a rendered page."""
    banner = re.search(r'<div class="banner ([^"]*)"[^>]*>(.*?)</div>', dom, re.S)
    if not banner:
        return None, "no verdict banner in the page — it probably threw before rendering", []

    failed = "bad" in banner.group(1)
    summary = strip_tags(banner.group(2))

    lines = []
    if page in ("smoke", "e2e"):
        for css, body in re.findall(r'<li class="(pass|fail)">(.*?)</li>', dom, re.S):
            lines.append(("FAIL" if css == "fail" else "pass", strip_tags(body)))
    else:
        for card in re.findall(r'<div class="card">(.*?)</div>\s*</div>', dom, re.S):
            heading = re.search(r"<h2>(.*?)</h2>", card, re.S)
            if heading:
                text = strip_tags(heading.group(1))
                lines.append(("FAIL" if "mismatched" in text else "pass", text))
    return (not failed), summary, lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--page", choices=["verify", "smoke", "e2e", "all"], default="all")
    parser.add_argument("--keep", action="store_true",
                        help="leave the server running and print the URLs")
    parser.add_argument("--browser", default=None)
    args = parser.parse_args(argv)

    browser = args.browser or find_browser()
    if not browser:
        sys.exit("no Chrome or Edge found. Pass --browser, or open tools/verify.html "
                 "and tools/smoke.html by hand.")

    for required in ("demo-data.js", "fb-insights.js", os.path.join("tools", "expected.js")):
        if not os.path.exists(os.path.join(ROOT, required)):
            sys.exit(f"missing {required}. Run tools/export_demo_rooms.py and "
                     "tools/verify_engine.py first.")

    port = free_port()
    server = serve(ROOT, port)
    base = f"http://127.0.0.1:{port}"
    pages = ["verify", "smoke", "e2e"] if args.page == "all" else [args.page]

    print(f"serving {ROOT} on {base}")
    print(f"browser {browser}\n")

    ok = True
    for page in pages:
        dom = dump_dom(browser, f"{base}/tools/{page}.html")
        passed, summary, lines = read_verdict(dom, page)
        marker = "ok  " if passed else "FAIL"
        print(f"[{marker}] tools/{page}.html")
        print(f"        {summary}")
        for status, text in lines:
            print(f"        {'FAIL  ' if status == 'FAIL' else '  ·   '}{text}")
        print()
        ok = ok and bool(passed)

    if args.keep:
        print(f"server still up — {base}/tools/verify.html  {base}/tools/smoke.html  "
              f"{base}/tools/e2e.html")
        print("ctrl-c to stop.")
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    server.shutdown()

    print("all checks passed" if ok else "SOMETHING FAILED — see above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
