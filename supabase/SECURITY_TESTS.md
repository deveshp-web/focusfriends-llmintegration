# Security tests — run before any real student data goes in

The app tells whoever sets it up to run this before entering real data. It has
two parts: an automated test that checks the database itself, and a short list
of project settings no SQL can check for you.

## 1. Run the automated tests

**In Supabase:** SQL Editor → paste all of `security_tests.sql` → **Run**.

A pass is a run that finishes without an error. The editor will just say
*Success. No rows returned*. A failure stops the run with a message that starts
`FAIL` and names the test, for example:

    FAIL  L1b a class code plus a child's id does not sign you in as them  it was allowed: ...

The tests create two throwaway teachers and classrooms and then **roll
everything back**, so they are safe on a live project and leave nothing behind.

**Locally**, without a Supabase project:

    python supabase/tests/run_local.py

That starts a temporary Postgres and builds three databases on it:

- **a fresh install**: the schema applied twice (to prove it is safe to
  re-run), then the security tests;
- **an upgrade from v3**: the schema the app shipped before
  (`tests/fixtures/schema_v3.sql`), holding data written through its own
  functions, then upgraded by the current schema, then checked against a
  snapshot taken just before the upgrade (`tests/upgrade_checks.sql`), then
  the security tests again;
- **an upgrade with odd rows**: the same, plus rows v3 accepted that the new
  rules refuse. Every one must be kept, and every broken rule named.

Last, it compares the fresh database with the upgraded one, object by object:
columns, rules, indexes, policies, functions, grants. It fails on any
difference it was not told to expect. It prints each test:

    fresh install
      [ok  ] security tests
             pass  J1 joining hands back a 64-hex session token
             ...
             ALL 79 SECURITY TESTS PASSED
    upgrade from v3
      [ok  ] upgrade checks
             pass  U1 every classroom, child, note, story, consent row and log entry is kept
             ...
    fresh install and upgrade, side by side
      [ok  ] an upgraded v3 database matches a fresh install: 289 objects compared

To upgrade a bigger v3 dataset instead, such as the 1,200-student seed the app
was demonstrated with, pass `--v3-data path/to/seed.sql`.

### What the 79 tests prove

| group | what it checks |
|---|---|
| **L1, L2** | A class code is not enough to become a child. Not by their id, not by their name and picture, not by another child's token. Tap-your-name works only on a tablet the teacher trusted; revoking it ends the sessions it started. |
| **C** | Consent can only be recorded with that child's own session, and stores the name on file, not the one sent. |
| **S** | Saves merge instead of overwrite: two tablets do not erase each other's check-ins. Check-ins dated in the future are refused. Unrecognised feelings are kept, because the analysis needs them to break a streak. A save cannot move a record to another class or rename it. |
| **R** | A teacher sees and changes only their own classrooms. They cannot reassign a classroom or change its code, cannot write notes about another class's children, and cannot edit consent or the access log. The access log records who really acted. |
| **T, G** | Anyone with the public key can reach nothing but the eight student functions. The public classroom lookup reveals no teacher or school details. |
| **F** | An inventory: RLS is on everywhere, no helper function is callable, every privileged function pins its `search_path`, anon holds no table privilege, the usage report is closed to the API, and no API role holds a sequence. This catches a *future* change that accidentally exposes something. |
| **D** | Deleting a child or a teacher account removes everything it should and nothing it should not. |

### What the 20 upgrade checks prove

| | |
|---|---|
| **U1–U7** | Nothing is lost. Every row is kept, every check-in byte for byte, and badges and last-active days keep their values in their new types. Rooms set to initials now show IDs, and v3 consent is marked unverified. |
| **U8–U15** | The old way in is closed. The v3 tokens are gone, no v3 function or policy survives, and a v3 child's name and picture, id, or old token opens nothing. |
| **U16–U20** | The new way back works for a v3 class. Its teacher still sees exactly their own children. On a tablet they trust, the child is back in their own record and the next check-in saves on top of the old ones. Deleting the teacher's account now deletes their rooms instead of orphaning them. |

Each protection was also checked the other way. The original weakness was put
back into a copy of the schema, and the suite failed on the test written for
it. That happened for 10 of 10 weaknesses in the schema. The same was done for
12 of 12 steps of the upgrade, from leaving old policies in place to throwing
away a log that was not a list.

## 2. Check these project settings by hand

1. **The key in `index.html` is the public one.** Use the *anon* or
   *publishable* key, never *service_role* or *secret*. The app refuses to start
   with a service key, but a key that was ever published should be rotated.
2. **Email sign-in is on, and "Confirm email" is off.** The setup instructions
   at the top of `index.html` require this, because teachers sign up with a
   username. Know what it means: anyone can create a teacher account. They will
   see only the classrooms they create.
3. **Trust the classroom tablets.** On each one, sign in as the teacher, open
   *Settings*, tap *Trust this device*, and sign out. Use *Stop trusting all
   devices* when a tablet is lost, and at the end of a school year.
4. **Decide the retention period.** `privacy.html` still says
   `[RETENTION PERIOD]`. Nothing in this schema deletes old data on a schedule.
   Pick a period, and schedule the deletion (for example with `pg_cron`) before
   real data arrives.
5. **Remember that backups hold children's records.** Session tokens and device
   secrets are stored only as hashes, so a backup cannot be used to sign in,
   but the check-ins themselves are in it.

## What this does not protect against

- **Junk students.** A class code still lets anyone join as a *new* student,
  up to 200 per class. Those records are visible to the teacher, who can delete
  them, and they can never reach another child's data.
- **A trusted tablet in the wrong hands.** Whoever holds a trusted tablet can
  tap any name on it. That is the point of trusting it, so revoke a lost one.
- **Teacher passwords.** A teacher account is exactly as strong as its password.
- **What teachers type.** Note text is shown as it was written. If a note names
  a child in a room set to "ID only", the note still shows the name.
- **The page's own code.** React and Babel load from a public CDN without an
  integrity check. Whoever controls what that CDN serves controls the page, and
  no database setting changes that. Pinning exact versions with Subresource
  Integrity is the fix, and it is separate from this schema.
