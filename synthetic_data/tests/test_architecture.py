"""Tests that protect the *shape* of the codebase, not its behaviour.

WHY THESE EXIST
---------------
The whole point of the refactor was that the four stages stop depending on each
other and share a `core/` instead. That is an easy property to state and an easy
one to lose: the next person who needs ``display_name`` inside the notifier can
restore the old tangle with a single plausible-looking import, and no behavioural
test would notice.

So the invariant is checked mechanically. These read the import statements
straight out of the source with :mod:`ast` - no importing, no running - so they
are fast and cannot be fooled by a module that happens not to execute the bad
line at runtime.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "focusbridge"

#: The four stages plus the review side channel. None of these may import
#: another one.
STAGES = ("detect", "recommend", "notify", "dashboard", "review")


def module_files():
    """Every ``.py`` file in the package, with its dotted name."""
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE.parent)
        dotted = ".".join(relative.with_suffix("").parts)
        yield dotted, path


def imports_of(path):
    """The dotted module names a file imports.

    Relative imports are resolved against the file's own package so that
    ``from ..core.triage import x`` inside ``focusbridge/notify/diff.py`` comes
    back as ``focusbridge.core.triage`` and can be reasoned about.
    """
    source = path.read_text(encoding="utf-8")
    package_parts = path.relative_to(PACKAGE.parent).with_suffix("").parts[:-1]
    found = set()

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                found.add(node.module or "")
                continue
            # `level` counts the leading dots: 1 = this package, 2 = the parent.
            base = package_parts[:len(package_parts) - (node.level - 1)]
            found.add(".".join(base + ((node.module,) if node.module else ())))
    return found


class LayeringTests(unittest.TestCase):

    def test_no_stage_imports_another_stage(self):
        """The invariant the refactor exists to create.

        Before it, ``dashboard`` imported the *detector* for a JSON reader and
        ``notify`` imported the *recommender* to format a name - so the stages
        could not be changed independently, which was the whole problem.
        """
        for dotted, path in module_files():
            parts = dotted.split(".")
            if len(parts) < 3 or parts[1] not in STAGES:
                continue
            own_stage = parts[1]
            for imported in imports_of(path):
                if not imported.startswith("focusbridge."):
                    continue
                other = imported.split(".")[1]
                if other in STAGES and other != own_stage:
                    self.fail(f"{dotted} imports {imported} - stages must share "
                              f"code through focusbridge.core, not through "
                              f"each other")

    def test_core_never_imports_a_stage(self):
        """`core/` is the bottom of the stack; it must not know stages exist.

        A single import the other way would make the dependency graph cyclic and
        turn "you can read core on its own" into a lie.
        """
        for dotted, path in module_files():
            if not dotted.startswith("focusbridge.core"):
                continue
            for imported in imports_of(path):
                if imported.startswith("focusbridge."):
                    self.assertNotIn(imported.split(".")[1], STAGES,
                                     f"{dotted} imports {imported}")

    def test_core_uses_only_the_standard_library(self):
        """The stdlib-only promise, enforced rather than hoped for.

        The project's headline claim is "Python 3.9+, standard library only".
        `core/` is where a stray third-party import would do the most damage,
        because everything else depends on it.
        """
        stdlib_ok = {"__future__", "json", "os", "sys", "argparse", "functools",
                     "hashlib", "bisect", "math", "re", "random", "shutil",
                     "datetime", "collections", "pathlib", "typing",
                     "dataclasses", "itertools", "unittest", "ast"}
        for dotted, path in module_files():
            if not dotted.startswith("focusbridge.core"):
                continue
            for imported in imports_of(path):
                root = imported.split(".")[0]
                if root in ("focusbridge", ""):
                    continue
                self.assertIn(root, stdlib_ok,
                              f"{dotted} imports non-stdlib {imported!r}")


class DocumentationTests(unittest.TestCase):

    def test_every_module_has_a_docstring(self):
        """A module with no docstring is a module nobody explained."""
        for dotted, path in module_files():
            with self.subTest(module=dotted):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                self.assertIsNotNone(ast.get_docstring(tree),
                                     f"{dotted} has no module docstring")

    def test_every_public_function_has_a_docstring(self):
        """Undocumented public functions are how a package stops being readable.

        Private helpers (a leading underscore) are exempt: their name and their
        single call site are usually explanation enough.
        """
        missing = []
        for dotted, path in module_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if isinstance(node, (ast.FunctionDef, ast.ClassDef)) \
                        and not node.name.startswith("_") \
                        and ast.get_docstring(node) is None:
                    missing.append(f"{dotted}.{node.name}")
        self.assertEqual(missing, [], f"undocumented public names: {missing}")

    def test_the_package_map_lists_files_that_exist(self):
        """The map in ``focusbridge/__init__.py`` must not go stale.

        A directory map that no longer matches the directory is worse than none
        at all - it sends a reader to a file that is not there.
        """
        text = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
        for stage in STAGES + ("core",):
            self.assertTrue((PACKAGE / stage).is_dir(), f"{stage}/ is missing")
        for name in ("paths.py", "jsonio.py", "vocabulary.py", "timeline.py",
                     "triage.py", "rules.py", "settings.py", "summarize.py",
                     "judge.py", "pipeline.py", "stats.py", "phrasing.py",
                     "context.py", "student_rules.py", "room_rules.py",
                     "diff.py", "digest.py", "payload.py", "render.py",
                     "store.py"):
            if name in text:
                self.assertTrue(any(PACKAGE.rglob(name)),
                                f"the package map names {name}, which does not exist")


class EntryPointTests(unittest.TestCase):

    def test_every_entry_point_script_stays_thin(self):
        """The five CLI scripts are shims. If one grows, logic has leaked out.

        Twenty lines is generous for "import main and call it" plus a docstring
        explaining where the real code lives.
        """
        root = PACKAGE.parent
        for name in ("detect_patterns.py", "recommend.py", "notify.py",
                     "dashboard.py", "review_flags.py"):
            with self.subTest(script=name):
                source = (root / name).read_text(encoding="utf-8")
                tree = ast.parse(source)
                # Count statements that are not the docstring.
                statements = [n for n in tree.body
                              if not (isinstance(n, ast.Expr)
                                      and isinstance(n.value, ast.Constant))]
                self.assertLessEqual(
                    len(statements), 3,
                    f"{name} has grown logic; it should only import and call main")


if __name__ == "__main__":
    unittest.main()
