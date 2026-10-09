# Handoff: Focus Bridge for iPhone and iPad

This folder is a complete Xcode project for the iOS version of Focus Bridge.
It was written on a Windows machine with no Mac, so **it has never been
compiled or run**. Your job, as the assistant in Xcode, is to build it, fix
whatever stops it from building, check that it works, and get it ready for
TestFlight. Most of the design work is done. Please don't redo it.

## To the person handing this over

1. Copy this whole folder to the Mac. Open `FocusBridge.xcodeproj` in Xcode
   16 or newer.
2. Open Xcode's coding assistant and paste this:

   > You are finishing an iOS app in this Xcode project. Read HANDOFF.md in
   > the project root in full before changing anything. Follow its Rules
   > exactly, and work through its Tasks in order. After each task, tell me
   > what you did and what you saw. If a task's check fails and you can't
   > fix it within the Rules, stop and tell me.

3. Some steps need you rather than the assistant: choosing your team in
   Signing, listening for read-aloud, toggling the network, and App Store
   Connect. HANDOFF.md says which.

---

## What the app is

**Focus Bridge** is an app for special-education students and their
teachers. Children check in with how they feel, follow a picture schedule,
use a calm corner and read social stories. Teachers get a dashboard with
insights about their class. It is a web app, a single-page React app at
**https://focusbridge.org**. Home mode keeps everything on the device.
Classroom mode stores data in a Supabase database.

This iOS app is a **native shell around that website**: one `WKWebView`
showing the site, plus native help with the things a web view can't do
alone. An Android version with the same design is already in Google Play
testing. Both apps expose the same bridge (`window.FBNative`) to the page.

Why a shell rather than a rewrite: the website is the product, and it
changes often. A shell gets every website update immediately, with no app
release.

## Project map

| File | Job |
|---|---|
| `FocusBridge/FocusBridgeApp.swift` | `@main` SwiftUI app. Sets the audio session so read-aloud is heard on silent. Hosts the UIKit screen. |
| `FocusBridge/WebViewController.swift` | The one screen: the web view, where links may go, the offline view, JS `alert`/`confirm`. |
| `FocusBridge/NativeBridge.swift` | `window.FBNative`: the injected JavaScript and the native side (speech, saving through the share sheet, copying). |
| `FocusBridge/ParentalGate.swift` | "Ask a grown-up": an arithmetic question in front of anything that leaves the app. |
| `FocusBridge/OfflineView.swift` | "No internet yet", for a first launch with no connection. |
| `FocusBridge/SiteConfig.swift` | The site's URL, hosts and colours. Everything that names the website is here. |
| `FocusBridge/Assets.xcassets` | App icon (1024 px, no alpha) and accent colour. |
| `FocusBridge/PrivacyInfo.xcprivacy` | Privacy manifest: no tracking. Lists the data classroom mode collects. |
| `Config/Info.plist` | Keys the build can't generate: `WKAppBoundDomains` and the encryption-export answer. The rest of Info.plist comes from `INFOPLIST_KEY_*` build settings. |
| `APP_STORE.md` | App Store Connect: privacy labels, review notes, screenshots. For the person, not for you. |

The `FocusBridge/` folder is a **synchronized folder** (Xcode 16). Any file
added to it on disk is part of the target automatically. Don't add files to
the target by hand. `Config/Info.plist` is deliberately *outside* that folder.

## The bridge contract

The website and the Android app depend on these names. **Don't rename
anything here.**

| JavaScript (`window.FBNative`) | Message to `fbnative` handler | Native action |
|---|---|---|
| `speak(text, rate, pitch)` | `{op:"speak", text, rate, pitch}` | `AVSpeechSynthesizer` |
| `stop()` | `{op:"stop"}` | stop speaking |
| `saveFile(name, mime, text)` | `{op:"saveFile", name, mime, text}` | temp file → grown-up check → share sheet |
| `copyText(text)` | `{op:"copyText", text}` | `UIPasteboard` |
| `platform` | (none) | the string `"ios"` |

The injected script also replaces `window.speechSynthesis`, so the page's
existing read-aloud code uses the native voice. It also catches the page's
blob "download" links and routes them to `saveFile`. That makes the app work
with every version of the website, including versions that know nothing
about apps. Messages are honoured only from the main frame of
`https://focusbridge.org`.

## Rules

1. **Don't change the website** or anything outside this folder. The app must
   work with the site as it is deployed.
2. **Don't add packages, SDKs, analytics, ads, crash reporters or tracking.**
   This is a children's app, and its privacy labels say there is none.
3. **Keep the bridge contract above** exactly: the names, the `fbnative`
   handler, and `platform: "ios"`.
4. **Keep `limitsNavigationsToAppBoundDomains = true`** and the matching
   `WKAppBoundDomains` in `Config/Info.plist`. App-bound domains are what lets
   WebKit run the site's service worker inside an app, so it works offline.
   They also keep the app on its own site. If the site's address ever
   changes, change `SiteConfig.swift` and `Config/Info.plist` together.
5. **Keep the parental gate** in front of links out of the app and in front
   of the share sheet. App Review requires it for apps used by children.
6. **Keep Swift 5 language mode and iOS 16.0** as the deployment target.
   Moving to Swift 6 mode turns concurrency warnings into errors, for no
   benefit here.
7. **Fix compile errors with the smallest change that works.** Don't
   restructure files or rename types. If a fix would break a rule, stop and
   ask.
8. The bundle ID is `org.focusbridge.app`. Change it only if the person asks.
   It must match the App Store Connect record.

## Tasks

### 1. Sign (needs the person)

Select the **FocusBridge** target → **Signing & Capabilities**. Under
**Team**, choose the person's Apple Developer team. Leave "Automatically
manage signing" on.

**Check:** no red signing errors on that tab. If Xcode says the bundle ID is
unavailable, ask the person for another (for example `org.focusbridge.ios`)
and change only `PRODUCT_BUNDLE_IDENTIFIER`.

### 2. Build for a simulator

Choose an iPhone simulator and build (⌘B).

**Check:** "Build Succeeded". If it fails, see *Likely build problems* below,
fix, and build again. Report any warnings you leave in place.

### 3. Run and check on the simulator

Run (⌘R) on an iPhone simulator. Then, with the person, check each item:

1. **It opens the site.** The landing page shows "Focus Bridge" and a badge,
   "🔒 Server connected" (or "Demo mode"). Nothing is hidden under the
   status bar or the home indicator.
2. **The bridge is there.** In Safari on the Mac: **Develop → (the
   simulator) → focusbridge.org**. In the Console run
   `FBNative.platform`, which should give `"ios"`. Then run
   `typeof speechSynthesis.speak`, which should give `"function"`. (Debug
   builds allow this inspection; release builds don't.)
3. **Read-aloud works** (the person listens). In the Console run
   `speechSynthesis.speak(new SpeechSynthesisUtterance("Hello from Focus Bridge"))`.
   The simulator speaks.
4. **Saving works.** Run
   `FBNative.saveFile("test.json", "application/json", '{"ok":true}')`. A
   "What is a × b?" question appears. The right answer opens the share
   sheet, and **Save to Files** saves a 11-byte `test.json`. A wrong answer
   does nothing.
5. **Copying works.** Run `FBNative.copyText("ABC123")`, then paste into
   the simulator's Notes app.
6. **Links out are gated.** Run
   `var a = document.createElement("a"); a.href = "https://www.apple.com"; document.body.appendChild(a); a.click();`.
   The grown-up question appears, and the right answer opens Safari.
7. **First launch offline** (the person turns off the Mac's network, or uses
   Network Link Conditioner at 100% loss). Delete the app from the
   simulator, then run it. You should see "No internet yet". Turn the network
   back on and tap **Try again**: the site loads.
8. **Offline after a visit.** With the network on, open the app, wait for it
   to load, and quit it. Repeat once more. Then turn the network off and
   open the app: the site still appears, served by its service worker.
9. **iPad and rotation.** Run on an iPad simulator and rotate it. The page
   fills the safe area in every orientation.

**If item 8 fails:** check that the built app's Info.plist contains
`WKAppBoundDomains` (**Product → Show Build Folder in Finder**, open the app
bundle). Without that key, WebKit won't run the service worker.

### 4. Run on a real iPhone or iPad (needs the person)

Same checks, plus one the simulator can't do: **turn the ringer switch to
silent and use read-aloud**. It must still be heard. That is what
`FocusBridgeApp`'s audio session is for.

### 5. Archive for TestFlight (needs the person)

**Product → Archive**, then **Distribute App → App Store Connect →
Upload**. This needs the App Store Connect record described in
`APP_STORE.md`.

**Check:** the build appears in App Store Connect under TestFlight after
processing.

## Likely build problems, and the fix

This code was written against the iOS 18/26 SDK without being compiled. The
errors most likely to appear:

- **"Instance method … nearly matches optional requirement …"** (a warning),
  and that delegate method is then never called. A `WKNavigationDelegate` or
  `WKUIDelegate` signature differs slightly from the SDK's. Apply Xcode's
  fix-it, or retype the method from autocomplete, keeping the body. This
  matters most for `decidePolicyFor navigationAction` (links out) and
  `didFailProvisionalNavigation` (the offline view).
- **Concurrency warnings** about main actor isolation or `Sendable`.
  Acceptable in Swift 5 mode. Don't switch to Swift 6.
- **`'protocol'` member access** in `SiteConfig.isSiteOrigin`. It is written
  ``origin.`protocol` ``. If the compiler objects, compare only
  `origin.host`; the HTTPS check is a second lock, not the main one.
- **"Multiple commands produce …/Info.plist"**: an `Info.plist` got into
  the `FocusBridge/` folder. It must live only in `Config/`.
- **Missing app icon or accent colour**: check that `Assets.xcassets` is in
  `FocusBridge/` and that the target's build settings name `AppIcon` and
  `AccentColor`.
- **The project won't open** ("damaged" or "cannot be opened"). The project
  file was written by hand. Make a new project and move the code into it:
  1. **File → New → Project → iOS App**: Product Name `FocusBridge`,
     Interface **SwiftUI**, Language **Swift**, no tests, no storage. Save
     it in a new folder.
  2. Delete the generated `ContentView.swift`, `FocusBridgeApp.swift` and
     `Assets.xcassets`. Copy in this folder's `FocusBridge/*` files, and
     the `Config/` folder beside the project.
  3. Target build settings: `INFOPLIST_FILE = Config/Info.plist`,
     `IPHONEOS_DEPLOYMENT_TARGET = 16.0`, `SWIFT_VERSION = 5.0`,
     `PRODUCT_BUNDLE_IDENTIFIER = org.focusbridge.app`,
     `INFOPLIST_KEY_CFBundleDisplayName = Focus Bridge`,
     `INFOPLIST_KEY_UIUserInterfaceStyle = Light`,
     `INFOPLIST_KEY_LSApplicationCategoryType = public.app-category.education`,
     `TARGETED_DEVICE_FAMILY = 1,2`, and all four orientations for iPad.
  4. Carry on from Task 1.

## How it behaves, and why

- **Safe area:** the web view sits inside the safe area, with the site's page
  colour behind the status bar. The site wasn't written for notches.
- **Light appearance only** (`UIUserInterfaceStyle = Light`). The site has no
  dark mode, and a dark status bar over a light page would be unreadable.
- **Back:** swipe from the left edge (`allowsBackForwardNavigationGestures`).
  The site is a single page with few history entries.
- **Read-aloud** uses `.playback` with `.spokenAudio`, so it plays with the
  ringer on silent and mixes with other audio rather than stopping it.
- **Web content process ended** (memory pressure): reload.
- **Downloads** never navigate: `shouldPerformDownload` is cancelled, and the
  bridge saves the file instead.

## Decisions that belong to the person

- **Kids Category or not.** If yes, App Review applies guideline 1.3 strictly:
  no links out without the gate (done), no third-party analytics (there are
  none), and a complete privacy policy. See APP_STORE.md.
- **The bundle ID**, if `org.focusbridge.app` is taken.
- **The website must be healthy first.** On 2026-10-07 the live site had been
  updated but its database had not, so new students could not join classes.
  The repository's DEPLOY.md explains the database upgrade. Do it before
  TestFlight, or testers will hit the same wall.

## What was checked before handoff, and what wasn't

Checked on Windows:

- The project file parses, every object reference resolves, and every path
  it names exists.
- The plist and JSON files are valid.
- The Swift files parse.
- The icon is 1024×1024 with no alpha.
- The same bridge design passed 12 of 12 checks in the Android emulator
  against the same site: speech, saving, copying, offline from cache, the
  first-launch offline page, and refusing other pages.

**Not checked:** compiling, running, and every iOS-specific behaviour.
Those are Tasks 2–4.
