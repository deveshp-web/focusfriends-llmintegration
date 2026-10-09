# Focus Bridge for Android

The Play Store app. It is one screen holding a WebView that shows
https://focusbridge.org, plus a small native bridge for the three things a
WebView cannot do on its own. Package `com.focusbridge`, the same listing as
the earlier builds.

## Why a WebView, not the TWA the earlier builds were

Versions 1–3 were PWABuilder packages: Trusted Web Activities. A TWA runs the
site inside Chrome, so the time testers spend in it can be counted as time in
Chrome rather than in this app. Google doesn't say how it measures tester
engagement for production access, but a closed test that looks unused is the
usual reason for a "requires more testing" reply. A WebView runs inside this
app's own process, so that question doesn't arise.

Two smaller reasons:

- A TWA proves it owns the site through `/.well-known/assetlinks.json`, which
  focusbridge.org never served, so the TWA showed a browser address bar.
- A WebView app can carry its own offline page and native features.

## What the native side adds

| The page needs | A WebView lacks it, so the app provides |
|---|---|
| Read-aloud (`speechSynthesis`) | Android's text-to-speech engine |
| "Download my data" and the teacher's export (a blob download) | The system "save file" picker |
| The copy buttons (`navigator.clipboard`) | Android's clipboard |
| A first launch with no internet | A page of its own, with *Try again* |

The page reaches these through `window.FBNative`, and only when it is there.
See the "INSIDE THE ANDROID APP" block near the top of `index.html`. In a
browser `FBNative` does not exist, so the website is unchanged. The bridge
answers only while the WebView is showing the site itself. It returns no data
to the page, and every action needs a person at the device.

Everything else is the website as it is. Links to other sites open in the
browser. After one visit online, the site's service worker keeps the app
working offline, inside the WebView too.

## Building

You need Android Studio, which brings the JDK and the Android SDK. Nothing
else: the project uses no libraries beyond Android itself.

    cd android
    set JAVA_HOME=C:\Program Files\Android\Android Studio\jbr

**A debug build** installs beside the Play version (`com.focusbridge.debug`).
It can be pointed at a copy of the site on this computer:

    python ..\deploy\build_site.py --out %TEMP%\fb-site
    (serve that folder on port 8765, then)
    gradlew installDebug -PfbStartUrl=http://127.0.0.1:8765/
    adb reverse tcp:8765 tcp:8765

Use `127.0.0.1` with `adb reverse`, not `10.0.2.2`. Service workers only run
on a secure origin, and of the two, only `127.0.0.1` counts.

**A release build** for Play has to be signed with the upload key PWABuilder
made for the earlier versions, the one whose SHA-256 starts `DE:0F:50:BB`.
Play refuses an update signed with any other key. The build reads the key from
the environment, so no password is ever in a file here:

    set FB_KEYSTORE=C:\path\to\signing.keystore
    set FB_KEYSTORE_PASSWORD=...     (from signing-key-info.txt)
    set FB_KEY_ALIAS=my-key-alias
    set FB_KEY_PASSWORD=...
    gradlew bundleRelease

The bundle to upload is `app/build/outputs/bundle/release/app-release.aab`.
Keep `signing.keystore` and `signing-key-info.txt` somewhere safe and backed
up. Without them you can never update this app again.

**Every upload needs a higher `versionCode`** in `app/build.gradle.kts`. The
TWA builds were 1–3; this one is 4.

## Uploading to Play

1. Play Console → Focus Bridge → **Test and release → Testing → Closed
   testing** → your track → **Create new release** → upload `app-release.aab`.
   It is an update to the same app, so your testers keep their place and the
   14-day clock keeps running.
2. In the release notes, say what changed: it now runs as a native app, with
   read-aloud, saving your data, and an offline screen. Google's production
   questionnaire asks what you changed in response to testing, and this is a
   real answer.
3. Update **App content → Data safety** before sending the release for
   review. The store app includes classroom mode, so the form must match it.
   See DEPLOY.md, "The Android app".
