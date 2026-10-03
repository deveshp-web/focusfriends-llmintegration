#!/usr/bin/env python3
"""Build the folder Netlify publishes: the app, configured, behind security headers.

    python deploy/build_site.py
    python deploy/build_site.py --supabase-url https://abcd.supabase.co \\
                                --supabase-key sb_publishable_...

Writes deploy/site/. Either drag that folder onto your site's Deploys page in
Netlify, or connect the repository and let Netlify run this script on every
push (see netlify.toml). DEPLOY.md, at the top of the repository, walks through
both, and through the database upgrade that has to happen first.

What it does, in order:

  1. Copies the app's files, and only those, from integrated_version/. A file
     in that folder that is neither published nor listed as left out stops the
     build: shipping something by accident, or forgetting something new, should
     be loud.
  2. Writes the classroom server's address and public key into index.html.
     Every page source in the repository keeps them blank, so no project's
     address is committed. A secret ("service_role" / "sb_secret_") key is
     refused: published, it would hand every child's record to anyone.
  3. Stamps the service worker's cache name with a hash of everything it
     publishes. The worker serves scripts cache-first, so without a new name a
     returning device keeps the old ones indefinitely.
  4. Writes _headers: a Content-Security-Policy naming exactly the origins the
     app talks to, plus the usual hardening headers (see _headers.template).
  5. Checks that every file a page or the service worker refers to was
     published.

Settings come from the command line, or else from the environment, which is
how a Netlify build passes them: FB_SUPABASE_URL, FB_SUPABASE_KEY and,
optionally, FB_ANALYTICS (a GoatCounter site code). With no server configured
the site is demo-only: everything stays on each device and the classroom
features are unavailable. The build says so.

Standard library only, and Python 3.8+, so it runs in Netlify's build image
unchanged.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(HERE, "site")
HEADERS_TEMPLATE = os.path.join(HERE, "_headers.template")

# What each app folder publishes, and what it holds that is deliberately not
# published. Every file in the folder must be in one list or the other.
PUBLISH = {
    "integrated_version": [
        "index.html", "fb-insights.js", "demo-data.js", "privacy.html", "delete-account.html",
        "manifest.webmanifest", "sw.js", "icon-192.png", "icon-512.png"],
    "focusbridge_sourcecode": [
        "index.html", "privacy.html", "delete-account.html",
        "manifest.webmanifest", "sw.js", "icon-192.png", "icon-512.png"],
}
NOT_PUBLISHED = {
    "README.md",   # for developers
    "tools",       # test pages and the scripts that generate their data
}

# Where the app's code comes from, besides this site. The service worker
# fetches these too, and a worker's fetches obey connect-src - so they belong
# there as well as in script-src / style-src / font-src, or returning visitors
# (whose pages the worker controls) get a blank screen.
CDN_SCRIPTS = "https://unpkg.com"
FONT_CSS = "https://fonts.googleapis.com"
FONT_FILES = "https://fonts.gstatic.com"

# GoatCounter, only when analytics is switched on.
ANALYTICS_SCRIPT = "https://gc.zgo.at"
ANALYTICS_COUNT = "https://*.goatcounter.com"


class BuildError(Exception):
    """A problem the person running the build has to fix."""


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------

def server_origin(url: str) -> str:
    """The Supabase project's origin, e.g. https://abcd.supabase.co.

    HTTPS only, except a server on this machine (for testing the build).
    """
    parts = urllib.parse.urlsplit(url.strip())
    local = parts.hostname in ("127.0.0.1", "localhost")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise BuildError(f"The Supabase URL must start with https:// - got {url!r}.")
    if not parts.hostname or parts.path not in ("", "/") or parts.query or parts.fragment:
        raise BuildError(f"The Supabase URL should be just the project address, like "
                         f"https://abcd.supabase.co - got {url!r}.")
    return f"{parts.scheme}://{parts.netloc}"


def jwt_role(key: str) -> str | None:
    """The `role` claim of a legacy Supabase key (a JWT), or None if it is not one."""
    pieces = key.split(".")
    if len(pieces) != 3:
        return None
    try:
        payload = pieces[1] + "=" * (-len(pieces[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("role")
    except (ValueError, AttributeError):
        return None


def check_public_key(key: str) -> str:
    """Accept the project's public key; refuse anything that would expose data."""
    key = key.strip()
    if key.startswith("sb_secret_") or jwt_role(key) == "service_role":
        raise BuildError(
            "That is the project's SECRET key. Published in a web page it would let anyone "
            "read and change every child's record. Use the publishable (or 'anon public') key "
            "from Project Settings -> API Keys - and if the secret key was ever published, "
            "rotate it in Supabase now.")
    if key.startswith("sb_publishable_") or jwt_role(key) == "anon":
        return key
    raise BuildError("That does not look like a Supabase public key. It should start with "
                     "'sb_publishable_', or be the 'anon public' key (a long 'eyJ...' token).")


def read_settings(options) -> dict:
    url = options.supabase_url or os.environ.get("FB_SUPABASE_URL", "")
    key = options.supabase_key or os.environ.get("FB_SUPABASE_KEY", "")
    analytics = options.analytics or os.environ.get("FB_ANALYTICS", "")
    if bool(url) != bool(key):
        raise BuildError("Give both the Supabase URL and its public key, or neither.")
    if analytics and not re.fullmatch(r"[a-z0-9-]{1,50}", analytics):
        raise BuildError(f"A GoatCounter site code is lower-case letters, digits and dashes - "
                         f"got {analytics!r}.")
    origin = server_origin(url) if url else ""
    return {"url": origin, "key": check_public_key(key) if key else "", "analytics": analytics}


# ---------------------------------------------------------------------------
# the build
# ---------------------------------------------------------------------------

def copy_app(app_dir: str, files: list, out: str) -> None:
    present = set(os.listdir(app_dir))
    unlisted = sorted(present - set(files) - NOT_PUBLISHED)
    if unlisted:
        raise BuildError(f"{', '.join(unlisted)} in {os.path.basename(app_dir)}/ is neither "
                         f"published nor left out on purpose. Add it to PUBLISH or NOT_PUBLISHED "
                         f"in deploy/build_site.py.")
    missing = [name for name in files if name not in present]
    if missing:
        raise BuildError(f"{os.path.basename(app_dir)}/ is missing {', '.join(missing)}.")
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    for name in files:
        shutil.copy2(os.path.join(app_dir, name), os.path.join(out, name))


def replace_once(text: str, pattern: str, replacement: str, where: str) -> str:
    new, count = re.subn(pattern, lambda _match: replacement, text)
    if count != 1:
        raise BuildError(f"Expected one {pattern!r} in {where}, found {count}. "
                         f"Has the page's config block changed?")
    return new


def edit(path: str, change) -> None:
    with open(path, encoding="utf-8", newline="") as handle:
        text = handle.read()
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(change(text))


def configure_pages(out: str, settings: dict) -> None:
    def index(text):
        text = replace_once(text, r'window\.FB_SUPABASE_URL = "[^"]*";',
                            f'window.FB_SUPABASE_URL = {json.dumps(settings["url"])};', "index.html")
        return replace_once(text, r'window\.FB_SUPABASE_KEY = "[^"]*";',
                            f'window.FB_SUPABASE_KEY = {json.dumps(settings["key"])};', "index.html")
    edit(os.path.join(out, "index.html"), index)

    for page in ("index.html", "privacy.html", "delete-account.html"):
        edit(os.path.join(out, page), lambda text, page=page: replace_once(
            text, r'window\.FF_ANALYTICS="[^"]*";',
            f'window.FF_ANALYTICS={json.dumps(settings["analytics"])};', page))


def site_hash(out: str, skip: str) -> str:
    digest = hashlib.sha256()
    for name in sorted(os.listdir(out)):
        if name != skip:
            digest.update(name.encode())
            with open(os.path.join(out, name), "rb") as handle:
                digest.update(handle.read())
    return digest.hexdigest()[:10]


def stamp_service_worker(out: str) -> str:
    stamp = site_hash(out, skip="sw.js")
    cache = {}

    def change(text):
        match = re.search(r"const CACHE = '([^']+)';", text)
        if not match:
            raise BuildError("sw.js has no `const CACHE = '...';` line to stamp.")
        cache["name"] = f"{match.group(1)}-{stamp}"
        return text.replace(match.group(0), f"const CACHE = '{cache['name']}';", 1)

    edit(os.path.join(out, "sw.js"), change)
    return cache["name"]


def write_headers(out: str, settings: dict) -> None:
    analytics = bool(settings["analytics"])
    fill = {
        "SCRIPT_SRC": " ".join(filter(None, [CDN_SCRIPTS, analytics and ANALYTICS_SCRIPT])),
        "IMG_SRC": " ".join(filter(None, [analytics and ANALYTICS_COUNT])),
        "CONNECT_SRC": " ".join(filter(None, [
            settings["url"], CDN_SCRIPTS, FONT_CSS, FONT_FILES, analytics and ANALYTICS_COUNT])),
        "STYLE_SRC": FONT_CSS,
        "FONT_SRC": FONT_FILES,
    }
    with open(HEADERS_TEMPLATE, encoding="utf-8") as handle:
        template = handle.read()
    lines = []
    for line in template.splitlines():
        if line.startswith("#"):
            continue   # the template's own explanation stays in the repository
        for name, value in fill.items():
            line = line.replace("{" + name + "}", (" " + value) if value else "")
        lines.append(line)
    leftover = re.findall(r"\{[A-Z_]+\}", "\n".join(lines))
    if leftover:
        raise BuildError(f"_headers.template uses {leftover}, which build_site.py does not fill.")
    with open(os.path.join(out, "_headers"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines).strip() + "\n")


def check_references(out: str) -> None:
    published = set(os.listdir(out))
    problems = []
    for name in sorted(published):
        if not name.endswith((".html", ".webmanifest", ".js")) or name == "demo-data.js":
            continue
        with open(os.path.join(out, name), encoding="utf-8") as handle:
            text = handle.read()
        refs = set(re.findall(r'(?:src|href)="\./([^"#?]+)"', text))        # pages
        refs |= set(re.findall(r'"src":\s*"\./([^"]+)"', text))             # the manifest
        refs |= set(re.findall(r"'\./([^']+)'", text)) if name == "sw.js" else set()
        problems += [f"{name} refers to ./{ref}, which is not published"
                     for ref in sorted(refs) if ref not in published]
    if problems:
        raise BuildError("\n".join(problems))


def build(app: str, settings: dict, out: str) -> dict:
    app_dir = os.path.join(REPO, app)
    copy_app(app_dir, PUBLISH[app], out)
    configure_pages(out, settings)
    cache = stamp_service_worker(out)
    write_headers(out, settings)
    check_references(out)
    return {"files": sorted(os.listdir(out)), "cache": cache}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--app", choices=sorted(PUBLISH), default="integrated_version",
                        help="which copy of the app to publish (default: integrated_version)")
    parser.add_argument("--supabase-url", help="the project's URL (or set FB_SUPABASE_URL)")
    parser.add_argument("--supabase-key", help="the project's PUBLIC key (or set FB_SUPABASE_KEY)")
    parser.add_argument("--analytics", help="a GoatCounter site code (or set FB_ANALYTICS)")
    parser.add_argument("--out", default=DEFAULT_OUT, help="where to write the site (default: deploy/site)")
    options = parser.parse_args(argv)

    try:
        settings = read_settings(options)
        result = build(options.app, settings, options.out)
    except BuildError as problem:
        print(f"build_site: {problem}", file=sys.stderr)
        return 1

    print(f"Built {os.path.relpath(options.out)} from {options.app}/ "
          f"({len(result['files'])} files, service worker cache {result['cache']})")
    if settings["url"]:
        print(f"  classroom server: {settings['url']}")
    else:
        print("  classroom server: NONE - this is a demo-only site. Nothing leaves each device,\n"
              "  and teachers cannot create classrooms. Set FB_SUPABASE_URL and FB_SUPABASE_KEY\n"
              "  (or pass --supabase-url / --supabase-key) for the classroom version.")
    print(f"  analytics: {'GoatCounter ' + settings['analytics'] if settings['analytics'] else 'off'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
