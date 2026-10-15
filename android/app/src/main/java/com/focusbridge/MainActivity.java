package com.focusbridge;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Insets;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.window.OnBackInvokedDispatcher;

/**
 * The whole app: one screen holding a WebView that shows focusbridge.org.
 *
 * Why a WebView rather than the Trusted Web Activity the earlier builds were:
 * a TWA runs inside Chrome, so time spent in it can be counted as time in
 * Chrome rather than in this app. A WebView runs in this app's own process,
 * so whatever Google measures about testers using it is measured here.
 *
 * The page is the same one a browser gets. What the WebView lacks (speech,
 * file saving, a dependable clipboard) the page reaches through
 * {@link NativeBridge}, exposed to it as window.FBNative.
 */
public class MainActivity extends Activity {

    private static final int GREEN = Color.parseColor("#27AE60");
    private static final String OFFLINE_PAGE = "file:///android_asset/offline.html";

    private WebView web;
    private NativeBridge bridge;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (BuildConfig.DEBUG) {
            WebView.setWebContentsDebuggingEnabled(true);   // chrome://inspect, debug builds only
        }

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(GREEN);   // shows behind the status bar
        web = new WebView(this);
        root.addView(web, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);
        keepClearOfSystemBars(root);

        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);                    // the app keeps everything in localStorage
        settings.setMediaPlaybackRequiresUserGesture(false);    // sound effects
        settings.setSupportMultipleWindows(false);
        settings.setAllowFileAccess(false);                     // the bundled offline page still loads
        settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setUserAgentString(settings.getUserAgentString()
                + " FocusBridgeAndroid/" + BuildConfig.VERSION_NAME);

        bridge = new NativeBridge(this, BuildConfig.START_URL);
        web.addJavascriptInterface(bridge, "FBNative");
        web.setWebViewClient(new SiteClient());
        web.setWebChromeClient(new WebChromeClient());

        if (Build.VERSION.SDK_INT >= 33) {
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                    OnBackInvokedDispatcher.PRIORITY_DEFAULT, this::goBackOrLeave);
        }

        if (savedInstanceState == null || web.restoreState(savedInstanceState) == null) {
            web.loadUrl(BuildConfig.START_URL);
        }
    }

    /**
     * From Android 15 the app draws behind the status and navigation bars.
     * Pad the page by exactly their size (and the keyboard's), so nothing the
     * child needs to tap is under a system bar.
     */
    @SuppressWarnings("deprecation")   // getSystemWindowInset*: the only way on Android 10 and below
    private void keepClearOfSystemBars(FrameLayout root) {
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            if (Build.VERSION.SDK_INT >= 30) {
                Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.ime());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
            } else {
                view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                        insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            }
            return insets;
        });
        if (Build.VERSION.SDK_INT >= 30) {
            WindowInsetsController controller = getWindow().getInsetsController();
            if (controller != null) {   // white status-bar icons on the green bar
                controller.setSystemBarsAppearance(0, WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS);
            }
        }
    }

    /** Pages of the site stay in the app; anything else opens in the browser. */
    private final class SiteClient extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            return leaveIfExternal(request.getUrl());
        }

        @Override
        public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
            bridge.setPage(url);
        }

        @Override
        public void doUpdateVisitedHistory(WebView view, String url, boolean isReload) {
            bridge.setPage(url);
        }

        /**
         * No connection and nothing cached: a page of our own instead of the
         * WebView's bare error screen. Once the site has loaded, its service
         * worker serves it offline, and this is not reached.
         */
        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (request.isForMainFrame() && !request.getUrl().toString().startsWith(OFFLINE_PAGE)) {
                view.loadUrl(OFFLINE_PAGE + "?retry=" + Uri.encode(BuildConfig.START_URL));
            }
        }
    }

    private boolean leaveIfExternal(Uri url) {
        if (bridge.isSitePage(url.toString()) || url.toString().startsWith(OFFLINE_PAGE)) {
            return false;
        }
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, url));
        } catch (ActivityNotFoundException ignored) {
            // nothing on the device can open it: stay put
        }
        return true;
    }

    private void goBackOrLeave() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            finish();
        }
    }

    @Override
    @SuppressWarnings("deprecation")   // Android 12 and below; 13+ uses the callback above
    public void onBackPressed() {
        goBackOrLeave();
    }

    @Override
    @SuppressWarnings("deprecation")
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        bridge.onActivityResult(requestCode, resultCode, data);
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }

    @Override
    protected void onResume() {
        super.onResume();
        web.onResume();
    }

    @Override
    protected void onPause() {
        web.onPause();
        bridge.stopSpeaking();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        bridge.shutdown();
        web.destroy();
        super.onDestroy();
    }
}
