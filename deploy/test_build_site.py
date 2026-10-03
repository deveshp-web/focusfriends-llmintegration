"""Unit tests for deploy/build_site.py.

    python -m unittest discover -s deploy

The browser half of the story is check_site.py; these cover the rules that
decide what gets published at all.
"""

import base64
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_site  # noqa: E402


def b64(part):
    return base64.urlsafe_b64encode(json.dumps(part).encode()).decode().rstrip("=")


def jwt(claims):
    """A token shaped like Supabase's legacy keys (the signature is not checked)."""
    return f"{b64({'alg': 'HS256'})}.{b64(claims)}.signature"


def settings(**overrides):
    options = SimpleNamespace(supabase_url=None, supabase_key=None, analytics=None)
    for name, value in overrides.items():
        setattr(options, name, value)
    return build_site.read_settings(options)


class Keys(unittest.TestCase):
    def test_a_secret_key_is_refused_in_either_format(self):
        for key in ("sb_secret_abc123", jwt({"role": "service_role"})):
            with self.assertRaisesRegex(build_site.BuildError, "SECRET"):
                build_site.check_public_key(key)

    def test_both_public_key_formats_are_accepted(self):
        for key in ("sb_publishable_abc123", jwt({"role": "anon"})):
            self.assertEqual(build_site.check_public_key(key), key)

    def test_anything_else_is_refused(self):
        for key in ("hunter2", jwt({"role": "authenticated"}), "a.b.c"):
            with self.assertRaises(build_site.BuildError):
                build_site.check_public_key(key)


class Settings(unittest.TestCase):
    NAMES = ("FB_SUPABASE_URL", "FB_SUPABASE_KEY", "FB_ANALYTICS")

    def setUp(self):
        # The build reads these from the environment; start each test without them.
        saved = {name: os.environ.pop(name) for name in self.NAMES if name in os.environ}
        self.addCleanup(os.environ.update, saved)

    def test_the_url_becomes_a_bare_origin(self):
        got = settings(supabase_url="https://abcd.supabase.co/", supabase_key="sb_publishable_x")
        self.assertEqual(got["url"], "https://abcd.supabase.co")

    def test_plain_http_only_for_this_machine(self):
        with self.assertRaisesRegex(build_site.BuildError, "https"):
            settings(supabase_url="http://abcd.supabase.co", supabase_key="sb_publishable_x")
        got = settings(supabase_url="http://127.0.0.1:5000", supabase_key="sb_publishable_x")
        self.assertEqual(got["url"], "http://127.0.0.1:5000")

    def test_a_path_in_the_url_is_refused(self):
        with self.assertRaises(build_site.BuildError):
            settings(supabase_url="https://abcd.supabase.co/rest/v1", supabase_key="sb_publishable_x")

    def test_url_and_key_come_together(self):
        with self.assertRaisesRegex(build_site.BuildError, "both"):
            settings(supabase_url="https://abcd.supabase.co")

    def test_the_environment_is_read_when_no_flags_are_given(self):
        os.environ.update(FB_SUPABASE_URL="https://env.supabase.co", FB_SUPABASE_KEY="sb_publishable_env")
        self.addCleanup(lambda: [os.environ.pop(name, None) for name in self.NAMES])
        self.assertEqual(settings()["url"], "https://env.supabase.co")

    def test_no_settings_means_a_demo_site(self):
        self.assertEqual(settings(), {"url": "", "key": "", "analytics": ""})


class Build(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.work, True)

    def build(self, name="site", **config):
        out = os.path.join(self.work, name)
        build_site.build("integrated_version",
                         {"url": "", "key": "", "analytics": "", **config}, out)
        return out

    def read(self, out, name):
        with open(os.path.join(out, name), encoding="utf-8") as handle:
            return handle.read()

    def test_only_the_app_is_published(self):
        out = self.build()
        self.assertEqual(sorted(os.listdir(out)),
                         sorted(build_site.PUBLISH["integrated_version"] + ["_headers"]))

    def test_the_server_settings_are_written_into_the_page(self):
        out = self.build(url="https://abcd.supabase.co", key="sb_publishable_x")
        page = self.read(out, "index.html")
        self.assertIn('window.FB_SUPABASE_URL = "https://abcd.supabase.co";', page)
        self.assertIn('window.FB_SUPABASE_KEY = "sb_publishable_x";', page)

    def test_the_policy_lets_the_page_reach_its_server_and_nothing_extra(self):
        headers = self.read(self.build(url="https://abcd.supabase.co", key="sb_publishable_x"), "_headers")
        self.assertIn("connect-src 'self' https://abcd.supabase.co https://unpkg.com", headers)
        self.assertNotIn("goatcounter", headers)
        self.assertNotIn("{", headers)

    def test_analytics_adds_only_its_own_origins(self):
        out = self.build(analytics="myschool")
        self.assertIn("gc.zgo.at", self.read(out, "_headers"))
        for page in ("index.html", "privacy.html", "delete-account.html"):
            self.assertIn('window.FF_ANALYTICS="myschool";', self.read(out, page))

    def test_the_cache_name_changes_when_the_published_files_do(self):
        first = self.read(self.build("a"), "sw.js")
        same = self.read(self.build("b"), "sw.js")
        other = self.read(self.build("c", url="https://abcd.supabase.co", key="sb_publishable_x"), "sw.js")
        self.assertEqual(first, same)
        self.assertNotEqual(first, other)

    def test_an_unlisted_file_in_the_app_folder_stops_the_build(self):
        app = os.path.join(self.work, "app")
        shutil.copytree(os.path.join(build_site.REPO, "integrated_version"), app,
                        ignore=shutil.ignore_patterns("tools"))
        open(os.path.join(app, "notes-to-self.txt"), "w").close()
        with self.assertRaisesRegex(build_site.BuildError, "notes-to-self.txt"):
            build_site.copy_app(app, build_site.PUBLISH["integrated_version"],
                                os.path.join(self.work, "out"))


if __name__ == "__main__":
    unittest.main()
