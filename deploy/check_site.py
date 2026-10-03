#!/usr/bin/env python3
"""Load the built site in a real browser, under its real headers, twice.

    python deploy/check_site.py                    # build two test sites and check them
    python deploy/check_site.py --site deploy/site # check the folder you are about to upload

A Content-Security-Policy that is one origin short does not fail a build. It
fails in the browser, sometimes only on the second visit, and the symptom is a
blank page. So this serves a site exactly as Netlify would - every response
carries the headers its _headers file assigns to that path - and opens it in
headless Chrome (or Edge) two times with one profile:

  visit 1   a first-time visitor. The service worker installs.
  visit 2   a returning visitor. The service worker now fetches the CDN
            scripts itself, under the policy's connect-src.

On each visit a small probe, added to the page by this server only, records
every policy violation, checks React loaded and the app rendered, loads the
sample classrooms the way the app does, and - when the site is configured with
a classroom server on this machine - calls it the way the app does.

With no --site, it builds two sites into a temporary folder with
deploy/build_site.py and checks both: one demo-only, and one configured with a
classroom server that this script stands up locally. It never contacts a real
Supabase project; with --site, a configured server that is not on this machine
is checked on paper (its origin must be in connect-src) but not called.

Standard library only.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_site  # noqa: E402  (deploy/ is not a package)

BROWSERS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)

# Runs first on the page. Collects violations as they happen; after the app
# has had time to render, writes what it found into a <pre> for --dump-dom.
PROBE = """<script>(function(){
var seen=[];document.addEventListener("securitypolicyviolation",function(e){seen.push(e.effectiveDirective+" "+(e.blockedURI||"inline"));});
window.addEventListener("load",function(){setTimeout(async function(){
var r={csp:seen,controlled:!!(navigator.serviceWorker&&navigator.serviceWorker.controller),react:typeof React!=="undefined"};
var url=window.FB_SUPABASE_URL||"";
if(/^http:\\/\\/(127\\.0\\.0\\.1|localhost)[:\\/]/.test(url)){try{var res=await fetch(url+"/rest/v1/rpc/bs_get_classroom",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({p_code:"PROBE0"})});r.server=res.status;}catch(e){r.server=String(e);}}
await new Promise(function(done){var s=document.createElement("script");s.src="./demo-data.js";s.onload=function(){r.demo=!!window.FB_DEMO_ROOMS;done();};s.onerror=function(){r.demo="absent";done();};document.head.appendChild(s);});
var root=document.getElementById("root");r.root=root?root.innerText.replace(/\\s+/g," ").slice(0,300):"";
var pre=document.createElement("pre");pre.id="fb-site-check";pre.textContent=JSON.stringify(r);document.body.appendChild(pre);
},8000);});})();</script>"""


def find_browser():
    return next((path for path in BROWSERS if os.path.exists(path)), None)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def parse_headers(text):
    """Netlify's _headers format: a path line, then indented `Name: value` lines."""
    rules = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line[0].isspace():
            rules.append((line.strip(), []))
        else:
            name, _, value = line.strip().partition(":")
            rules[-1][1].append((name.strip(), value.strip()))
    return rules


def headers_for(rules, path):
    found = []
    for pattern, headers in rules:
        if pattern == path or (pattern.endswith("*") and path.startswith(pattern[:-1])):
            found += headers
    return found


def serve_site(site, port):
    """The site, as Netlify would serve it, plus the probe in index.html."""
    with open(os.path.join(site, "_headers"), encoding="utf-8") as handle:
        rules = parse_headers(handle.read())

    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def end_headers(self):
            for name, value in headers_for(rules, self.path.split("?")[0]):
                self.send_header(name, value)
            super().end_headers()

        def do_GET(self):
            if self.path.split("?")[0] in ("/", "/index.html"):
                with open(os.path.join(site, "index.html"), "rb") as handle:
                    body = handle.read().replace(b"<head>", b"<head>" + PROBE.encode(), 1)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

    handler = functools.partial(Handler, directory=site)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, rules


def serve_classroom_server(port):
    """Answers the probe's one call the way Supabase would, CORS included."""
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def cors(self, status, body=b""):
            self.send_response(status)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.cors(204)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self.cors(200, b"[]")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def visit(browser, url, profile):
    result = subprocess.run(
        [browser, "--headless=new", "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
         f"--user-data-dir={profile}", "--virtual-time-budget=40000", "--dump-dom", url],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    match = re.search(r'<pre id="fb-site-check">(.*?)</pre>', result.stdout, re.S)
    if not match:
        return None
    return json.loads(match.group(1).replace("&quot;", '"').replace("&lt;", "<")
                      .replace("&gt;", ">").replace("&amp;", "&"))


def configured_server(site):
    with open(os.path.join(site, "index.html"), encoding="utf-8") as handle:
        match = re.search(r'window\.FB_SUPABASE_URL = "([^"]*)";', handle.read())
    return match.group(1) if match else ""


def check(site, browser, title):
    """Serve `site`, visit it twice, print what was found; True if all is well."""
    print(title)
    problems = []
    port = free_port()
    server, rules = serve_site(site, port)
    profile = tempfile.mkdtemp(prefix="fb-site-profile-")
    origin = configured_server(site)
    try:
        page_headers = dict(headers_for(rules, "/index.html"))
        policy = page_headers.get("Content-Security-Policy", "")
        connect = re.search(r"connect-src ([^;]*)", policy)
        if not policy:
            problems.append("the pages are served without a Content-Security-Policy")
        elif origin and (not connect or origin not in connect.group(1).split()):
            problems.append(f"connect-src does not allow the classroom server {origin}")
        sw = urllib.request.urlopen(f"http://127.0.0.1:{port}/sw.js")
        if "no-cache" not in (sw.headers.get("Cache-Control") or ""):
            problems.append("sw.js is served without Cache-Control: no-cache")

        local_server = bool(re.match(r"http://(127\.0\.0\.1|localhost)[:/]", origin))
        expect_badge = "Server connected" if origin else "Demo mode"
        for label in ("first visit", "returning visit"):
            report = visit(browser, f"http://127.0.0.1:{port}/", profile)
            if report is None:
                problems.append(f"{label}: the page never finished - nothing to read")
                continue
            notes = []
            if report["csp"]:
                problems.append(f"{label}: blocked by the policy: {'; '.join(report['csp'])}")
            if not report["react"] or expect_badge not in report["root"]:
                problems.append(f"{label}: the app did not render (expected {expect_badge!r}); "
                                f"the page showed: {report['root'][:120]!r}")
            if report.get("demo") is False:
                problems.append(f"{label}: the sample classrooms did not load")
            if local_server and report.get("server") != 200:
                problems.append(f"{label}: could not reach the classroom server: {report.get('server')}")
            if label == "returning visit" and not report["controlled"]:
                problems.append("returning visit: the service worker was not in control, "
                                "so this visit did not test it")
            notes.append("service worker in control" if report["controlled"] else "no service worker yet")
            if local_server:
                notes.append(f"classroom server answered {report.get('server')}")
            if report.get("demo") is True:
                notes.append("sample classrooms loaded")
            ok = not any(p.startswith(label) for p in problems)
            print(f"  [{'ok  ' if ok else 'FAIL'}] {label}: {len(report['csp'])} policy violations; "
                  + "; ".join(notes))
    finally:
        server.shutdown()
        shutil.rmtree(profile, ignore_errors=True)
    for problem in problems:
        print(f"         {problem}")
    return not problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--site", help="a built site to check, e.g. deploy/site")
    options = parser.parse_args(argv)

    browser = find_browser()
    if not browser:
        sys.exit("No Chrome or Edge found; this check needs a real browser.")

    if options.site:
        return 0 if check(options.site, browser, f"checking {options.site}") else 1

    work = tempfile.mkdtemp(prefix="fb-site-check-")
    api_port = free_port()
    api = serve_classroom_server(api_port)
    try:
        demo = os.path.join(work, "demo")
        build_site.build("integrated_version",
                         {"url": "", "key": "", "analytics": ""}, demo)
        classroom = os.path.join(work, "classroom")
        build_site.build("integrated_version",
                         {"url": f"http://127.0.0.1:{api_port}",
                          "key": "sb_publishable_site_check_only", "analytics": ""}, classroom)
        results = [check(demo, browser, "demo-only site"),
                   check(classroom, browser, "site with a classroom server")]
        return 0 if all(results) else 1
    finally:
        api.shutdown()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
