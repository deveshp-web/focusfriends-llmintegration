# Focus Bridge for iPhone and iPad

A complete, self-contained Xcode project for the iOS app. The app is a
native shell around https://focusbridge.org, the same design as the Android
app.

**This folder doesn't depend on anything else.** Move it wherever you like.
It needs nothing from the main repository, and the website needs no changes
for it.

## What's here

    FocusBridge.xcodeproj    open this in Xcode
    FocusBridge/             the app: Swift sources, icon, privacy manifest
    Config/Info.plist        the two Info.plist keys the build can't generate
    HANDOFF.md               instructions for Xcode's coding assistant (and you)
    APP_STORE.md             App Store Connect: privacy labels, review notes, screenshots

## To use it

1. Get this folder onto a Mac with **Xcode 16 or newer**. Copy it, or clone
   the `ios-app` branch, which holds only this project:

       git clone -b ios-app https://github.com/deveshp-web/focusfriends-llmintegration.git FocusBridge-iOS

2. Open `FocusBridge.xcodeproj`.
3. Read **HANDOFF.md**. Its first section is a prompt to paste into Xcode's
   coding assistant, which then builds, fixes and checks the app step by
   step, asking you for the parts only you can do: signing, listening,
   turning the network off.
4. When it runs, follow **APP_STORE.md** to publish.

## Good to know

- **It has never been compiled.** It was written on Windows, where Xcode
  doesn't run. HANDOFF.md lists the build problems most likely to come up
  and their fixes, plus a fallback if the project file won't open.
- **You need an Apple Developer Program membership** to run it on a real
  device for more than a few days, and to publish.
- **Bundle ID:** `org.focusbridge.app`. Change it in Signing & Capabilities if
  you prefer another.
- **Before testers use it,** the website's database upgrade must be done
  (DEPLOY.md in the main repository). Otherwise new students can't join
  classes.
