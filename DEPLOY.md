# Deploying Focus Bridge

The app is a static site, hosted on Netlify. Children's records live in a
Supabase database. This release changes both, and **the two must change on the
same day, database first**. The new page calls database functions the old
database does not have, and the new database removes the old functions the old
page calls. Whichever goes first, the app is broken until the other follows.

If your project has never had a real class on it, the order still matters, but
nothing else here will surprise anyone.

## What people will notice

- **Every child signs in again, once.** The old sign-in tokens came from the
  function that let anyone with a class code open any child's record, so
  none of them can be trusted, and the upgrade removes them. A child whose
  device was signed in sees *"Let's sign you in again"* and is asked to get
  their teacher. Nothing they did before is lost.
- **Classroom tablets need trusting.** On a tablet the teacher has trusted, a
  child taps their name and is straight back in their own record. Anywhere
  else, a returning child cannot get back in by typing their name and picking
  their picture any more. That was the loophole.
- **Rooms set to show initials now show ID numbers.** The app no longer offers
  initials, and ID numbers are the more private of the two choices it has.
- **Consent recorded before the upgrade is marked `legacy_unverified`.** The
  old app let anyone with a class code write a consent record, so these rows
  are not evidence on their own. If you rely on them, collect consent again.

## 1. Back up the database

Rolling the site back is easy, but rolling the database back means restoring a
backup. Take one first. On a paid plan, Supabase keeps daily backups under
**Database → Backups**. On the free plan, dump it yourself with `pg_dump` and
the connection string from **Project Settings → Database**.

## 2. Upgrade the database

Do this when no class is using the app, after school for example.

1. Supabase → **SQL Editor** → paste all of
   `supabase/focus-bridge-schema-secure.sql` → **Run**.
   It upgrades an existing database in place, keeping every record, and is
   safe to run again. If any existing row breaks one of the new rules, it
   prints a notice starting `upgrade:` naming the rule. The row is kept, and
   the rule applies only to new rows.
2. Paste and run `supabase/security_tests.sql` the same way. It has to finish
   without an error. It rolls itself back and leaves nothing behind.

If step 1 stops with an error, nothing has changed. The file runs as a single
transaction, so you can fix the problem and run it again.

## 3. Publish the site

Use either option. Both run the same build: it copies `integrated_version/`,
writes your server's address and public key into it, gives the service worker
a new cache name so devices pick up the new files, and adds security headers.

**You need two values** from Supabase → **Project Settings → API Keys**: the
project URL, and the **publishable** key (`sb_publishable_…`, or the older
"anon public" key). Never the secret / service_role key. The build refuses it.

### Option A: drag and drop

    python deploy/build_site.py --supabase-url https://YOUR-PROJECT.supabase.co --supabase-key sb_publishable_...
    python deploy/check_site.py --site deploy/site

The second command loads the built site in Chrome under its real headers, as
a first-time visitor and as a returning one, and fails if anything is blocked.
Then: Netlify → your site → **Deploys** → drag the `deploy/site` folder onto
the page.

### Option B: let Netlify build from GitHub

1. Netlify → your site → **Site configuration → Build & deploy**: link this
   repository, and set the production branch to the branch with these files.
   `netlify.toml` supplies the build command and publish folder.
2. **Site configuration → Environment variables**: add `FB_SUPABASE_URL` and
   `FB_SUPABASE_KEY`. Do **not** tick "Contains secret values" for the key.
   It is the public key, the build has to write it into the page, and Netlify
   fails any build whose output contains a value marked secret. If Netlify's
   smart detection flags it anyway, add the key to
   `SECRETS_SCAN_SMART_DETECTION_OMIT_VALUES`.
3. Trigger a deploy. Every push to the branch deploys again.

## 4. Trust the classroom tablets

For each class, on each class tablet: the teacher signs in, opens
**Settings**, taps **Trust this device**, and signs out. Children then tap
their name. Use **Stop trusting all devices** when a tablet is lost, and at the
end of the school year.

## 5. Check it

Open the site. The landing page should say **🔒 Server connected**. On a
trusted tablet, a child taps their name and sees their own stars and
check-ins.

## The Android app (Google Play)

The Play Store app (`android/`, see its README) is a WebView showing
focusbridge.org. So it is exactly as healthy as the site and the database,
and the order above still comes first: **database, then site, then app**.
The site has to be up before the app ships, because it carries the code that
connects read-aloud, saving and copying to the app.

1. Build `app-release.aab` (android/README.md) and upload it to your
   **closed testing** track as a new release of the same app. Testers get it
   as an update, and the 14-day clock keeps running.
2. Fix the privacy policy before you apply for production. `privacy.html`
   is still marked *"Draft — pending legal review"* and has blanks:
   `[DATE]`, `[DESCRIBE YOUR VERIFICATION METHOD]`, `[REGION]`,
   `[RETENTION PERIOD]`, `[TIMEFRAME]`, `[CONTACT]` and
   `[NAME / ROLE] — [EMAIL] — [MAILING ADDRESS]`. Play requires a complete
   policy for any app that reaches children, and the reviewer reads it.
3. Update **App content → Data safety**. The app includes classroom mode, so
   it does collect data, and the form must say so. A starting point (check
   it against your own reading and your attorney's):

   | Question | Answer |
   |---|---|
   | Collects or shares required data types? | **Yes** |
   | Encrypted in transit? | **Yes**. The app loads only HTTPS, and Supabase is HTTPS. |
   | Users can ask for deletion? | **Yes**. In the app (a child's own data, a teacher's whole account), and at `https://focusbridge.org/delete-account.html`, which is also the "delete account URL" Play asks for. |
   | Shared with third parties? | **No**. Supabase stores the data on your behalf, which Play's definitions count as a service provider, not sharing. |
   | **Personal info → Name** | Collected. A student's first name or nickname (or an ID, at the teacher's choice), and a teacher's display name. Purpose: app functionality. |
   | **Personal info → User IDs** | Collected. A teacher's sign-in username, and student IDs. Purpose: app functionality, account management. |
   | **Personal info → Other info** | Collected. A school's name and ZIP code, entered by the teacher. Purpose: analytics, for counts by region only. |
   | **Health and fitness → Health info** | Collected. The emotion check-ins describe how a child feels, so declare them here. Over-declaring costs little, and under-declaring is a policy violation. Purpose: app functionality. |
   | **App activity → App interactions** | Collected. Tasks done, stories read, stars and badges. |
   | **App activity → Other user-generated content** | Collected. Teachers' notes and the social stories they write. |

   Home mode keeps everything on the device, and data that never leaves the
   device is not "collected" in Play's sense. If you ever switch on the
   GoatCounter counts (`FB_ANALYTICS`), add them too.
4. In the production-access questionnaire, be specific. Say who your testers
   were, what they reported, and what you changed. Moving from a TWA to a
   native app with read-aloud, saving and an offline screen is a real change
   made in response to testing. Google requires at least 12 testers opted
   in for the last 14 days, and it looks for people actually using the app,
   each on their own phone.

## If something goes wrong

- **"Database function … not found"** in the app: the database step did not
  run, or Supabase has not noticed the new functions yet. Run
  `notify pgrst, 'reload schema';` in the SQL Editor.
- **A blank page for people who used the app before**: the security headers
  are blocking something the service worker fetches. `deploy/check_site.py`
  exists to catch exactly this before it ships.
- **Rolling back**: Netlify can republish an earlier deploy in one click
  (**Deploys** → pick one → **Publish deploy**). But an old page cannot talk to
  the upgraded database, so rolling the site back also means restoring the
  database from the backup in step 1. Fixing forward is usually quicker.

## Testing all of this locally first

    python supabase/tests/run_local.py          # fresh install, upgrade from v3, security tests
    python integrated_version/tools/smoke_test.py
    python deploy/check_site.py

`run_local.py` needs PostgreSQL 15+, and its docstring shows how to get it
without installing anything system-wide. The other two need Chrome or Edge.

## What the headers do, and what they cannot

The Content-Security-Policy fixes where code can come from: this site and the
unpkg CDN, and nothing else. Data can go only to this site, your Supabase
project, and the CDN and font hosts the service worker fetches from. No other
site can put the app inside its own page.

The policy has to allow inline and `eval`'d script, because the app compiles
its own JSX in the browser. So it limits the damage of an injected script
without preventing one. The remaining exposure is the CDN itself. React and
Babel load from unpkg without an integrity check, so whoever controls what
unpkg serves controls the page. Pinning exact versions with Subresource
Integrity, or serving those three files from this site, would close that.
`_headers.template` explains each directive.
