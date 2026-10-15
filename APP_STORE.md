# Putting Focus Bridge on the App Store

For the person publishing the app. Everything here happens in App Store
Connect (appstoreconnect.apple.com). It needs a paid Apple Developer Program
membership.

## Before anything else

- **The privacy policy must be live and true.** It was finalized on
  2026-10-09; make sure that version is the one at
  `https://focusbridge.org/privacy.html` (upload the rebuilt site). App
  Review reads it. It promises no analytics, so switching analytics on
  means updating the policy first.
- **The website and its database must be up to date.** The app shows the live
  site, so anything broken there is broken in the app. See DEPLOY.md in the
  main repository.
- **Make a reviewer account.** Classroom features need a teacher login and a
  class code. Create a teacher account and a demo classroom with a few
  invented students, and keep the username, password and class code for the
  review notes below.

## 1. Create the app record

**Apps → + → New App**:

- **Platform:** iOS
- **Name:** Focus Bridge. If it's taken, something like "Focus Bridge
  Classroom". The name on the home screen stays "Focus Bridge".
- **Primary language:** English (U.S.)
- **Bundle ID:** `org.focusbridge.app`, registered automatically the first
  time Xcode signs the app. Pick it from the list.
- **SKU:** anything unique to you, e.g. `focusbridge-ios-1`
- **User access:** Full access

## 2. App Privacy (the "nutrition label")

**App Privacy → Get Started.** The app includes classroom mode, so it does
collect data. These answers match `PrivacyInfo.xcprivacy` in the project and
the Google Play Data safety form. Check them with whoever reviews your
privacy policy.

- **Do you or your third-party partners collect data from this app?** Yes.
- **Used to track you?** No, for every type. The app has no tracking, ads or
  analytics.

| Data type | Collected | Linked to the user | Purpose |
|---|---|---|---|
| **Contact Info → Name** (a student's first name or nickname; a teacher's display name) | Yes | Yes | App Functionality |
| **Identifiers → User ID** (a teacher's sign-in username, student IDs) | Yes | Yes | App Functionality |
| **Health & Fitness → Health** (emotion check-ins, which describe how a child feels) | Yes | Yes | App Functionality |
| **User Content → Other User Content** (teachers' notes, social stories) | Yes | Yes | App Functionality |
| **Usage Data → Product Interaction** (tasks done, stories read, stars, badges) | Yes | Yes | App Functionality |

Home mode keeps everything on the device, which Apple doesn't count as
"collected". Declaring check-ins under Health is the cautious reading: over-
declaring costs little, and under-declaring gets apps removed.

**Privacy Policy URL:** `https://focusbridge.org/privacy.html`.

## 3. Age rating and the Kids Category

**Age Rating:** answer the questionnaire honestly. There is no violence,
gambling or mature content, and it has no web browsing outside its own site,
so the result should be 4+.

**Kids Category** (App Information → Category) is optional. It puts the app
where parents look, and in return App Review checks guideline 1.3 strictly:

- No links out of the app, purchases or other exits without a parental gate.
  The app gates every link out and the share sheet ("What is 7 × 8?").
- No third-party analytics or advertising (there are none).
- A complete privacy policy.

If you choose it, pick the age band that fits your students, e.g. 6–8.

## 4. App Review information

**Sign-in required:** Yes, for classroom features. Enter the demo teacher's
username and password. Then in **Notes**, something like:

> Focus Bridge helps special-education students check in with their
> feelings, follow a picture schedule and use calming tools, and helps their
> teachers see patterns over time.
>
> Without signing in: tap "Start Here!" for the at-home experience. All
> of its data stays on the device.
>
> Classroom features: tap "Teachers" and sign in with the account above to
> see the demo classroom and its Insights tab. To see the student side, tap
> "Students" and enter class code ______.
>
> Native features: read-aloud uses the device's voice (turn on Read aloud in
> the child's settings). "Download my data" saves through the share sheet.
> The app works offline after its first launch, and a parental gate guards
> every link out of the app.
>
> Account deletion: a teacher can delete their account and all its
> classrooms in Settings → Delete my account. A student can delete their own
> data from their settings.

## 5. Screenshots

Required sizes, taken from the simulator (⌘S saves a screenshot):

- **iPhone 6.9-inch** (e.g. iPhone 16 Pro Max simulator)
- **iPad 13-inch** (e.g. iPad Pro 13-inch simulator), because the app runs on
  iPad

Show the landing page, a child's check-in, the calm corner, and the
teacher's Insights tab. Use the demo classroom: never a real child's name.

## 6. If App Review says "4.2 — Minimum Functionality"

The most common rejection for apps built on a website. The honest answer is
what the app adds beyond Safari:

- read-aloud in the device's voice, even on silent;
- saving data through the share sheet;
- working offline after the first launch;
- a parental gate on every exit;
- a full-screen, distraction-free experience for children who find browsers
  confusing.

Reply in Resolution Center with that list, and screenshots of the offline
screen and the grown-up check. If it's still refused, the usual next step is
to move more of the experience into native screens. That is a bigger
project, worth planning only if review asks for it.

## 7. TestFlight before release

Once a build is uploaded (HANDOFF.md, task 5), add testers under
**TestFlight**. Teachers you trust are ideal. Internal testers (people in
your App Store Connect team) need no review. External testers need a short
Beta App Review.
