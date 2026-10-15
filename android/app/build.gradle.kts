// The app module. Plain Java and the Android framework only: no AndroidX and
// no other libraries, so there is nothing to keep up to date but the SDK.

plugins {
    id("com.android.application")
}

// Release signing comes from the environment, never from a file in the
// repository. These are the upload key PWABuilder generated for the earlier
// (TWA) builds; Play only accepts an update signed with that same key.
//   FB_KEYSTORE           path to signing.keystore
//   FB_KEYSTORE_PASSWORD  "Key store password" in signing-key-info.txt
//   FB_KEY_ALIAS          "Key alias"
//   FB_KEY_PASSWORD       "Key password"
// Without them, bundleRelease still builds, unsigned, and Play refuses it.
val keystore: String? = System.getenv("FB_KEYSTORE")

// The site the app shows. A debug build can be pointed at a copy of the site
// on this computer instead (the emulator reaches it at 10.0.2.2):
//   gradlew installDebug -PfbStartUrl=http://10.0.2.2:8000/
val liveSite = "https://focusbridge.org/"
val debugSite: String = (findProperty("fbStartUrl") as String?) ?: liveSite

android {
    namespace = "com.focusbridge"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.focusbridge"   // the Play listing's package: must never change
        // 24 (Android 7.0): the lowest Play's automatic integrity protection,
        // switched on for this app, accepts. Play refuses uploads below it.
        minSdk = 24
        targetSdk = 36
        // Must go up with every upload. The TWA builds were 1-3; 4 was the first
        // WebView build; 5 shipped mid-test with the feedback link and plain
        // error messages (both on the website side).
        versionCode = 5
        versionName = "1.1.1"
        buildConfigField("String", "START_URL", "\"$liveSite\"")
    }

    buildFeatures {
        buildConfig = true
    }

    signingConfigs {
        if (keystore != null) {
            create("upload") {
                storeFile = file(keystore)
                storePassword = System.getenv("FB_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("FB_KEY_ALIAS")
                keyPassword = System.getenv("FB_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (keystore != null) {
                signingConfig = signingConfigs.getByName("upload")
            }
        }
        debug {
            // Installs beside the Play version instead of over it.
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
            buildConfigField("String", "START_URL", "\"$debugSite\"")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}
