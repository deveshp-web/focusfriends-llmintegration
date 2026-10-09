package com.focusbridge;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.speech.tts.TextToSpeech;
import android.util.Log;
import android.webkit.JavascriptInterface;
import android.widget.Toast;

import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.Locale;

/**
 * What the page can ask the app to do, as window.FBNative. index.html uses it
 * only when it is there, so the same page works in a browser unchanged.
 *
 *   speak(text, rate, pitch), stop()   read-aloud, through the phone's
 *                                      text-to-speech engine (a WebView has
 *                                      no speech synthesis of its own)
 *   saveFile(name, mime, text)         "Download my data" and the teacher's
 *                                      export: the child or teacher picks
 *                                      where it goes
 *   copyText(text)                     the copy buttons
 *
 * Every method runs only while the WebView is showing the site itself, so a
 * page from anywhere else can never reach them. None of them returns data to
 * the page, and each needs the person holding the device to act.
 *
 * WebView calls these on a background thread; anything touching the screen is
 * handed to the UI thread.
 */
final class NativeBridge implements TextToSpeech.OnInitListener {

    private static final String TAG = "FocusBridge";
    static final int SAVE_REQUEST = 41;

    private final Activity activity;
    private final String siteOrigin;
    private volatile String currentPage = "";

    private final TextToSpeech tts;
    private volatile boolean ttsReady;
    private String waitingToSpeak;          // asked before the engine was ready
    private float waitingRate = 1f, waitingPitch = 1f;

    private String pendingSave;             // the text, while the picker is open

    NativeBridge(Activity activity, String startUrl) {
        this.activity = activity;
        this.siteOrigin = origin(Uri.parse(startUrl));
        this.tts = new TextToSpeech(activity, this);
    }

    // -- which page is showing -----------------------------------------------

    void setPage(String url) {
        currentPage = url == null ? "" : url;
    }

    boolean isSitePage(String url) {
        return url != null && origin(Uri.parse(url)).equals(siteOrigin);
    }

    private boolean onSite() {
        boolean ok = isSitePage(currentPage);
        if (!ok) Log.w(TAG, "bridge call refused: not on the site (" + currentPage + ")");
        return ok;
    }

    private static String origin(Uri uri) {
        return (uri.getScheme() + "://" + uri.getHost()
                + (uri.getPort() == -1 ? "" : ":" + uri.getPort())).toLowerCase(Locale.ROOT);
    }

    // -- read-aloud ----------------------------------------------------------

    @Override
    public void onInit(int status) {
        if (status != TextToSpeech.SUCCESS) {
            Log.w(TAG, "no text-to-speech engine: read-aloud is silent");
            return;
        }
        int language = tts.setLanguage(Locale.getDefault());
        if (language == TextToSpeech.LANG_MISSING_DATA || language == TextToSpeech.LANG_NOT_SUPPORTED) {
            tts.setLanguage(Locale.US);
        }
        ttsReady = true;
        synchronized (this) {
            if (waitingToSpeak != null) {
                say(waitingToSpeak, waitingRate, waitingPitch);
                waitingToSpeak = null;
            }
        }
    }

    @JavascriptInterface
    public void speak(String text, float rate, float pitch) {
        if (!onSite() || text == null) return;
        String clipped = text.length() > 2000 ? text.substring(0, 2000) : text;
        synchronized (this) {
            if (!ttsReady) {
                waitingToSpeak = clipped;
                waitingRate = rate;
                waitingPitch = pitch;
                return;
            }
        }
        say(clipped, rate, pitch);
    }

    private void say(String text, float rate, float pitch) {
        tts.setSpeechRate(clamp(rate));
        tts.setPitch(clamp(pitch));
        tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "focusbridge");
        Log.d(TAG, "speak: " + text.length() + " characters");
    }

    private static float clamp(float value) {
        return Float.isNaN(value) ? 1f : Math.max(0.5f, Math.min(2f, value));
    }

    @JavascriptInterface
    public void stop() {
        if (onSite()) stopSpeaking();
    }

    void stopSpeaking() {
        if (ttsReady) tts.stop();
    }

    // -- saving a file -------------------------------------------------------

    @JavascriptInterface
    public void saveFile(String name, String mime, String text) {
        if (!onSite() || text == null) return;
        String fileName = name == null || name.isEmpty() ? "focus-bridge.json" : name;
        String type = mime == null || mime.isEmpty() ? "application/json" : mime;
        activity.runOnUiThread(() -> {
            pendingSave = text;
            Intent pick = new Intent(Intent.ACTION_CREATE_DOCUMENT)
                    .addCategory(Intent.CATEGORY_OPENABLE)
                    .setType(type)
                    .putExtra(Intent.EXTRA_TITLE, fileName);
            try {
                activity.startActivityForResult(pick, SAVE_REQUEST);
            } catch (ActivityNotFoundException e) {
                pendingSave = null;
                toast("This device cannot save files.");
            }
        });
    }

    /** The picker closed: write the file where the person chose, if they chose. */
    void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode != SAVE_REQUEST) return;
        String text = pendingSave;
        pendingSave = null;
        Uri where = data == null ? null : data.getData();
        if (resultCode != Activity.RESULT_OK || where == null || text == null) return;
        try (OutputStream out = activity.getContentResolver().openOutputStream(where)) {
            if (out == null) throw new java.io.IOException("no stream");
            byte[] bytes = text.getBytes(StandardCharsets.UTF_8);
            out.write(bytes);
            Log.d(TAG, "saved " + bytes.length + " bytes");
            toast("Saved.");
        } catch (Exception e) {
            Log.w(TAG, "save failed", e);
            toast("Could not save the file.");
        }
    }

    // -- copying -------------------------------------------------------------

    @JavascriptInterface
    public void copyText(String text) {
        if (!onSite() || text == null) return;
        activity.runOnUiThread(() -> {
            ClipboardManager clipboard =
                    (ClipboardManager) activity.getSystemService(Context.CLIPBOARD_SERVICE);
            if (clipboard == null) return;
            clipboard.setPrimaryClip(ClipData.newPlainText("Focus Bridge", text));
            Log.d(TAG, "copied " + text.length() + " characters");
            if (Build.VERSION.SDK_INT < 33) {
                toast("Copied.");   // Android 13+ shows its own confirmation
            }
        });
    }

    // -- housekeeping --------------------------------------------------------

    private void toast(String message) {
        activity.runOnUiThread(() -> Toast.makeText(activity, message, Toast.LENGTH_SHORT).show());
    }

    void shutdown() {
        tts.shutdown();
    }
}
