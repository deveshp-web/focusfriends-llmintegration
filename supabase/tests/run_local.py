#!/usr/bin/env python3
"""Run the schema, its upgrade, and its security tests on a throwaway Postgres.

    python supabase/tests/run_local.py
    python supabase/tests/run_local.py --v3-data path/to/focus-bridge-seed-data.sql

It starts a temporary server (in a directory it deletes afterwards) and builds
three databases on it, each from scratch:

  fresh install     the Supabase stand-in, then the schema TWICE (the app says
                    it is safe to re-run, and that claim deserves a test), then
                    security_tests.sql.

  upgrade from v3   the schema the app shipped before (fixtures/schema_v3.sql)
                    holding data written through its own functions
                    (fixtures/v3_data.sql); a snapshot of that data; the current
                    schema twice; upgrade_checks.sql, which compares the result
                    with the snapshot; and security_tests.sql again, because the
                    protections have to hold on an upgraded database too.

  upgrade with odd rows
                    the same, plus rows v3 accepted that the new rules do not
                    (fixtures/v3_messy_rows.sql). The upgrade must keep them all
                    and name each rule they break.

Finally it compares the fresh database with the upgraded one, table by table,
rule by rule, grant by grant, and fails on any difference it was not told to
expect. That is what makes "upgrading gives you the same database as installing
fresh" a tested claim rather than a hope.

--v3-data swaps in another v3 dataset for the upgrade - for example the 1,200
student seed the app was demonstrated with. It needs a teacher account, so one
is created first.

It needs PostgreSQL 15 or newer (Supabase runs 15+). Point it at the binaries
with FOCUSBRIDGE_PG_BIN if `pg_ctl` is not on your PATH. One way to get them
without touching anything else on the machine:

    conda create -p ./pg -c conda-forge postgresql=17
    set FOCUSBRIDGE_PG_BIN=./pg/Library/bin        (Windows)
    export FOCUSBRIDGE_PG_BIN=./pg/bin             (macOS / Linux)

Standard library only, like the rest of the project.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
SUPABASE = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")

STUB = os.path.join(HERE, "supabase_stub.sql")
SCHEMA = os.path.join(SUPABASE, "focus-bridge-schema-secure.sql")
SECURITY_TESTS = os.path.join(SUPABASE, "security_tests.sql")
SCHEMA_V3 = os.path.join(FIXTURES, "schema_v3.sql")
V3_DATA = os.path.join(FIXTURES, "v3_data.sql")
V3_MESSY = os.path.join(FIXTURES, "v3_messy_rows.sql")
SNAPSHOT = os.path.join(HERE, "upgrade_snapshot.sql")
UPGRADE_CHECKS = os.path.join(HERE, "upgrade_checks.sql")

# The rules fixtures/v3_messy_rows.sql breaks on purpose, as the upgrade's
# notices name them. A second run of the schema re-reports only the optional
# columns: a rule added NOT VALID the first time is not added again.
MESSY_COLUMNS = {"classrooms.teacher_id", "consent.student_id", "consent.student_name",
                 "audit_log.classroom_code", "audit_log.actor"}
MESSY_RULES = {"students_name_check", "students_stars_check",
               "notes_student_id_classroom_code_fkey", "consent_student_id_classroom_code_fkey",
               "audit_log_classroom_code_fkey"}

# Differences between a fresh install and an upgraded v3 database that are
# there on purpose. Anything else fails the run.
EXPECTED_DIFFERENCES = {
    # v3's demo-mode classroom PIN. The current app never writes it to the
    # server; the upgrade leaves the column so as not to delete anything, and a
    # teacher can read and write it on their own rooms like any other column.
    "column public.classrooms.pin",
    "column grant authenticated public.classrooms.pin",
}

A_TEACHER = ("insert into auth.users (id, email) values "
             "('44444444-0000-4000-8000-000000000004', 'seed-teacher@example.invalid');")


@dataclass
class Step:
    label: str
    path: str | None = None
    sql: str | None = None
    # For a schema run: the rule names its "upgrade:" notices must report,
    # exactly. None means do not check.
    upgrade_notices: set | None = None


@dataclass
class Scenario:
    title: str
    database: str
    steps: list = field(default_factory=list)


def scenarios(v3_data: str | None) -> list:
    upgrade_data = ([Step("a teacher account", sql=A_TEACHER), Step("v3 data: " + os.path.basename(v3_data), v3_data)]
                    if v3_data else [Step("v3 data", V3_DATA)])
    return [
        Scenario("fresh install", "fb_fresh", [
            Step("Supabase stand-in", STUB),
            Step("schema", SCHEMA, upgrade_notices=set()),
            Step("schema again (re-run)", SCHEMA, upgrade_notices=set()),
            Step("security tests", SECURITY_TESTS),
        ]),
        Scenario("upgrade from v3", "fb_upgrade", [
            Step("Supabase stand-in", STUB),
            Step("v3 schema", SCHEMA_V3),
            Step("v3 schema again (as live projects ran it)", SCHEMA_V3),
            *upgrade_data,
            Step("snapshot of the v3 data", SNAPSHOT),
            Step("schema (the upgrade)", SCHEMA, upgrade_notices=None if v3_data else set()),
            Step("schema again (re-run)", SCHEMA, upgrade_notices=None if v3_data else set()),
            Step("upgrade checks", UPGRADE_CHECKS),
            Step("security tests", SECURITY_TESTS),
        ]),
        Scenario("upgrade from v3, with rows the new rules refuse", "fb_messy", [
            Step("Supabase stand-in", STUB),
            Step("v3 schema", SCHEMA_V3),
            Step("v3 data", V3_DATA),
            Step("v3 rows the new rules refuse", V3_MESSY),
            Step("snapshot of the v3 data", SNAPSHOT),
            Step("schema (the upgrade)", SCHEMA, upgrade_notices=MESSY_COLUMNS | MESSY_RULES),
            Step("schema again (re-run)", SCHEMA, upgrade_notices=MESSY_COLUMNS),
            Step("upgrade checks", UPGRADE_CHECKS),
            Step("security tests", SECURITY_TESTS),
        ]),
    ]


# Everything about a database that the schema decides, one row per object, so
# two databases can be compared as two sets of lines.
CATALOG = r"""
with ns as (select oid from pg_namespace where nspname in ('public', 'private'))
select 'column ' || table_schema || '.' || table_name || '.' || column_name,
       concat_ws(' ', data_type, 'nullable=' || is_nullable,
                 'default=' || coalesce(column_default, '-'),
                 'identity=' || coalesce(identity_generation, '-'))
  from information_schema.columns where table_schema in ('public', 'private')
union all
select 'constraint ' || conrelid::regclass::text || '.' || conname,
       pg_get_constraintdef(oid) || case when convalidated then '' else ' NOT VALID' end
  from pg_constraint where connamespace in (select oid from ns)
union all
select 'index ' || schemaname || '.' || indexname, indexdef
  from pg_indexes where schemaname in ('public', 'private')
union all
select 'policy ' || tablename || '.' || policyname,
       concat_ws(' | ', cmd, permissive, array_to_string(roles, ','), qual, with_check)
  from pg_policies where schemaname in ('public', 'private')
union all
select 'function ' || p.oid::regprocedure::text, md5(pg_get_functiondef(p.oid))
  from pg_proc p where p.pronamespace in (select oid from ns)
union all
select 'trigger ' || tgrelid::regclass::text || '.' || tgname, pg_get_triggerdef(oid)
  from pg_trigger where not tgisinternal
   and tgrelid in (select c.oid from pg_class c where c.relnamespace in (select oid from ns))
union all
select 'view ' || schemaname || '.' || viewname, definition
  from pg_views where schemaname in ('public', 'private')
union all
select 'row security ' || c.oid::regclass::text, c.relrowsecurity::text
  from pg_class c where c.relnamespace in (select oid from ns) and c.relkind = 'r'
union all
select 'table grant ' || grantee || ' ' || table_schema || '.' || table_name,
       string_agg(privilege_type, ',' order by privilege_type)
  from information_schema.role_table_grants
 where grantee in ('anon', 'authenticated') and table_schema in ('public', 'private')
 group by grantee, table_schema, table_name
union all
select 'column grant ' || grantee || ' ' || table_schema || '.' || table_name || '.' || column_name,
       string_agg(privilege_type, ',' order by privilege_type)
  from information_schema.column_privileges
 where grantee in ('anon', 'authenticated') and table_schema in ('public', 'private')
 group by grantee, table_schema, table_name, column_name
union all
select 'execute ' || r.rolname || ' ' || p.oid::regprocedure::text, 'granted'
  from pg_proc p cross join pg_roles r
 where p.pronamespace in (select oid from ns) and r.rolname in ('anon', 'authenticated')
   and has_function_privilege(r.oid, p.oid, 'execute')
union all
select 'sequence grant ' || r.rolname || ' ' || c.oid::regclass::text, 'granted'
  from pg_class c cross join pg_roles r
 where c.relnamespace in (select oid from ns) and c.relkind = 'S'
   and r.rolname in ('anon', 'authenticated')
   and has_sequence_privilege(r.oid, c.oid, 'usage, select, update')
order by 1
"""

NOTICE_COLUMN = re.compile(r"upgrade: some (\S+) values are empty")
NOTICE_RULE = re.compile(r"upgrade: some existing \S+ rows break (\S+);")


def find_bin(name):
    folder = os.environ.get("FOCUSBRIDGE_PG_BIN")
    if folder:
        for candidate in (name, name + ".exe"):
            path = os.path.join(folder, candidate)
            if os.path.exists(path):
                return path
        return None
    return shutil.which(name)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    """One temporary Postgres server, and psql pointed at it."""

    def __init__(self, tools, env, workdir):
        self.tools, self.env = tools, env
        self.data = os.path.join(workdir, "data")
        self.log = os.path.join(workdir, "server.log")
        self.port = free_port()
        self.started = False

    def start(self):
        subprocess.run([self.tools["initdb"], "-D", self.data, "-U", "postgres", "-A", "trust",
                        "-E", "UTF8", "--no-locale"],
                       check=True, capture_output=True, env=self.env)
        # Not captured: on Windows the server pg_ctl launches inherits pg_ctl's
        # output handles, so a captured pipe never closes and this call would
        # wait forever. The server's own output goes to `log` instead.
        subprocess.run([self.tools["pg_ctl"], "-D", self.data, "-l", self.log, "-w", "-t", "60",
                        "start", "-o", f"-p {self.port} -c listen_addresses=127.0.0.1"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       stdin=subprocess.DEVNULL, env=self.env)
        self.started = True

    def stop(self):
        if self.started:
            subprocess.run([self.tools["pg_ctl"], "-D", self.data, "-m", "fast", "-w", "stop"],
                           capture_output=True, env=self.env)

    def psql(self, database, *args):
        return subprocess.run(
            [self.tools["psql"], "-X", "-q", "-h", "127.0.0.1", "-p", str(self.port),
             "-U", "postgres", "-d", database, "-v", "ON_ERROR_STOP=1", *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=self.env)


def run_step(server, database, step, verbose, shown):
    """Run one step; print it; return True if it passed."""
    args = ["-f", step.path] if step.path else ["-c", step.sql]
    result = server.psql(database, *args)
    # psql writes NOTICE lines (the per-test "pass" lines) to stderr.
    notices = [line.split("NOTICE:", 1)[1].strip()
               for line in result.stderr.splitlines() if "NOTICE:" in line]
    # The test lines; not Postgres's own "does not exist, skipping" chatter.
    passes = [n for n in notices if n.startswith(("pass ", "ALL "))]
    if result.returncode != 0:
        print(f"  [FAIL] {step.label}")
        for line in notices:
            print(f"         {line}")
        print("\n" + result.stderr.strip())
        return False

    problem = None
    if step.upgrade_notices is not None:
        reported = {m.group(1) for n in notices for m in (NOTICE_COLUMN.search(n), NOTICE_RULE.search(n)) if m}
        if reported != step.upgrade_notices:
            problem = (f"the upgrade reported {sorted(reported) or 'no broken rules'}, "
                       f"expected {sorted(step.upgrade_notices) or 'none'}")
    print(f"  [{'FAIL' if problem else 'ok  '}] {step.label}")
    # A file's pass lines are shown the first time it runs; after that, only
    # its summary line, so a repeated suite does not bury the rest.
    first_time = step.path not in shown
    shown.add(step.path)
    for line in (notices if verbose else passes if first_time else passes[-1:]):
        print(f"         {line}")
    if problem:
        print(f"         {problem}")
    return problem is None


def catalog(server, database):
    result = server.psql(database, "-A", "-t", "-F", "\t", "-c", CATALOG)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    rows = {}
    for line in result.stdout.splitlines():
        if line.strip():
            key, _, value = line.partition("\t")
            rows[key] = value
    return rows


def compare(fresh, upgraded):
    """Print how two catalogs differ; return True if only as expected."""
    problems = []
    for key in sorted(set(fresh) | set(upgraded)):
        if key in EXPECTED_DIFFERENCES or fresh.get(key) == upgraded.get(key):
            continue
        problems.append(f"{key}\n             fresh:    {fresh.get(key, '(missing)')}"
                        f"\n             upgraded: {upgraded.get(key, '(missing)')}")
    if problems:
        print(f"  [FAIL] an upgraded v3 database differs from a fresh install in {len(problems)} place(s)")
        for problem in problems:
            print(f"         {problem}")
        return False
    print(f"  [ok  ] an upgraded v3 database matches a fresh install: {len(fresh)} objects compared")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--v3-data", help="a v3 dataset to upgrade instead of fixtures/v3_data.sql")
    parser.add_argument("--verbose", action="store_true", help="print every notice, every time")
    options = parser.parse_args()

    tools = {name: find_bin(name) for name in ("initdb", "pg_ctl", "psql")}
    missing = [name for name, path in tools.items() if not path]
    if missing:
        sys.exit(f"PostgreSQL binaries not found ({', '.join(missing)}). Install PostgreSQL "
                 f"15+ or set FOCUSBRIDGE_PG_BIN - see this file's docstring.")

    # The schema and tests contain emoji; make sure psql reads them as UTF-8.
    env = dict(os.environ, PGCLIENTENCODING="UTF8")
    workdir = tempfile.mkdtemp(prefix="focusbridge-pg-")
    server = Server(tools, env, workdir)
    shown = set()
    try:
        server.start()
        for scenario in scenarios(options.v3_data):
            print(scenario.title)
            server.psql("postgres", "-c", f"create database {scenario.database}")
            for step in scenario.steps:
                if not run_step(server, scenario.database, step, options.verbose, shown):
                    return 1
        print("fresh install and upgrade, side by side")
        if not compare(catalog(server, "fb_fresh"), catalog(server, "fb_upgrade")):
            return 1
        return 0
    finally:
        server.stop()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
