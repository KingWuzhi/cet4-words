package com.cet4.words;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.DialogInterface;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.speech.tts.TextToSpeech;
import android.view.ViewGroup;
import android.webkit.JavascriptInterface;
import android.webkit.JsPromptResult;
import android.webkit.JsResult;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import java.io.OutputStream;
import java.util.Locale;

/**
 * 四级背单词 —— 单页 WebView 外壳。
 * 词库、界面、判题全部在 assets/index.html 里离线运行；
 * 这里提供：原生存储兜底、单词朗读(TTS)、返回键行为、存档文件的读写。
 */
public class MainActivity extends Activity {

    private static final String ASSET_URL = "file:///android_asset/index.html";
    private static final int REQ_PICK_FILE = 1001;   // 导入存档：选文件
    private static final int REQ_SAVE_FILE = 1002;   // 导出存档：存文件

    private WebView web;
    private SharedPreferences prefs;
    private TextToSpeech tts;
    private volatile boolean ttsReady = false;

    private ValueCallback<Uri[]> pendingFileChooser;
    private String pendingSaveBody;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        prefs = getSharedPreferences("cet4w", MODE_PRIVATE);

        getWindow().setStatusBarColor(Color.parseColor("#2249B8"));
        getWindow().setNavigationBarColor(Color.WHITE);

        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#F4F6FB"));
        web.setOverScrollMode(WebView.OVER_SCROLL_NEVER);
        setContentView(web, new ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(true);
        s.setAllowContentAccess(true);      // 导入存档要读系统文件选择器返回的 content://
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setDisplayZoomControls(false);
        s.setTextZoom(100);                 // 不跟随系统字体缩放，避免布局被撑坏
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setCacheMode(WebSettings.LOAD_NO_CACHE);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView view, android.webkit.WebResourceRequest request,
                                        android.webkit.WebResourceError error) {
                if (request != null && request.isForMainFrame()) {
                    view.loadData("<html><head><meta name='viewport' content='width=device-width'>" +
                            "</head><body style='font-family:sans-serif;padding:28px;color:#333;line-height:1.7'>" +
                            "<h3>页面加载失败</h3><p>词库文件 assets/index.html 未找到或读取失败。</p>" +
                            "<p style='color:#888;font-size:13px'>请重新安装应用，或改用单文件 HTML 版本。</p>" +
                            "</body></html>", "text/html", "UTF-8");
                }
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onJsAlert(WebView v, String url, String message, final JsResult result) {
                showDialog(message, "知道了", null, result, true);
                return true;
            }

            @Override
            public boolean onJsConfirm(WebView v, String url, String message, final JsResult result) {
                showDialog(message, "确定", "取消", result, false);
                return true;
            }

            @Override
            public boolean onJsPrompt(WebView v, String url, String message, String defaultValue,
                                      final JsPromptResult result) {
                result.cancel();
                return true;
            }

            /** 网页里的 <input type=file>（导入存档）需要自己拉起系统文件选择器。 */
            @Override
            public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (pendingFileChooser != null) {
                    pendingFileChooser.onReceiveValue(null);
                    pendingFileChooser = null;
                }
                pendingFileChooser = callback;
                try {
                    Intent i = params.createIntent();
                    i.addCategory(Intent.CATEGORY_OPENABLE);
                    startActivityForResult(i, REQ_PICK_FILE);
                    return true;
                } catch (Throwable t) {
                    pendingFileChooser = null;
                    return false;
                }
            }
        });

        web.addJavascriptInterface(new Bridge(), "AndroidBridge");

        if (state != null) {
            web.restoreState(state);
        } else {
            web.loadUrl(ASSET_URL);
        }

        initTts();
    }

    /** 页面里的 alert / confirm 必须自己实现，否则 WebView 默认直接丢弃。 */
    private void showDialog(String message, String positive, final String negative,
                            final JsResult result, final boolean alertOnly) {
        AlertDialog.Builder b = new AlertDialog.Builder(this)
                .setMessage(message)
                .setCancelable(false)
                .setPositiveButton(positive, new DialogInterface.OnClickListener() {
                    public void onClick(DialogInterface d, int w) {
                        result.confirm();
                    }
                });
        if (negative != null) {
            b.setNegativeButton(negative, new DialogInterface.OnClickListener() {
                public void onClick(DialogInterface d, int w) {
                    result.cancel();
                }
            });
        }
        b.show();
    }

    private void initTts() {
        try {
            tts = new TextToSpeech(this, new TextToSpeech.OnInitListener() {
                public void onInit(int status) {
                    if (status != TextToSpeech.SUCCESS || tts == null) return;
                    int r = tts.setLanguage(Locale.US);
                    tts.setSpeechRate(0.9f);
                    tts.setPitch(1.0f);
                    ttsReady = (r != TextToSpeech.LANG_MISSING_DATA && r != TextToSpeech.LANG_NOT_SUPPORTED);
                }
            });
        } catch (Throwable t) {
            ttsReady = false;
        }
    }

    /** 暴露给网页的桥：存储 + 朗读。 */
    public class Bridge {

        @JavascriptInterface
        public String getPref(String key) {
            try {
                return prefs.getString(key, "");
            } catch (Throwable t) {
                return "";
            }
        }

        @JavascriptInterface
        public void setPref(String key, String value) {
            try {
                prefs.edit().putString(key, value).apply();
            } catch (Throwable t) {
            }
        }

        @JavascriptInterface
        public void removePref(String key) {
            try {
                prefs.edit().remove(key).apply();
            } catch (Throwable t) {
            }
        }

        @JavascriptInterface
        public void speak(final String text) {
            if (tts == null) return;
            runOnUiThread(new Runnable() {
                public void run() {
                    if (!ttsReady) return;
                    try {
                        tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "cet4w");
                    } catch (Throwable t) {
                    }
                }
            });
        }

        @JavascriptInterface
        public boolean isTtsReady() {
            return ttsReady;
        }

        /**
         * 把存档写到用户选的位置（系统“保存到文件”对话框，不需要任何存储权限）。
         * 写完后回调网页的 window.__onSaveResult(true/false)。
         */
        @JavascriptInterface
        public boolean saveFile(final String name, final String content) {
            pendingSaveBody = content;
            runOnUiThread(new Runnable() {
                public void run() {
                    try {
                        Intent i = new Intent(Intent.ACTION_CREATE_DOCUMENT);
                        i.addCategory(Intent.CATEGORY_OPENABLE);
                        i.setType("application/json");
                        i.putExtra(Intent.EXTRA_TITLE, name);
                        startActivityForResult(i, REQ_SAVE_FILE);
                    } catch (Throwable t) {
                        pendingSaveBody = null;
                        notifySaveResult(false);
                    }
                }
            });
            return true;
        }
    }

    private void notifySaveResult(final boolean ok) {
        runOnUiThread(new Runnable() {
            public void run() {
                Toast.makeText(MainActivity.this, ok ? "存档已保存" : "没有保存", Toast.LENGTH_SHORT).show();
                if (web != null) {
                    web.evaluateJavascript("window.__onSaveResult&&window.__onSaveResult(" + ok + ")", null);
                }
            }
        });
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == REQ_PICK_FILE) {
            if (pendingFileChooser == null) return;
            Uri[] result = null;
            if (resultCode == RESULT_OK && data != null && data.getData() != null) {
                result = new Uri[]{data.getData()};
            }
            pendingFileChooser.onReceiveValue(result);
            pendingFileChooser = null;
            return;
        }
        if (requestCode == REQ_SAVE_FILE) {
            final String body = pendingSaveBody;
            pendingSaveBody = null;
            final Uri uri = (resultCode == RESULT_OK && data != null) ? data.getData() : null;
            if (uri == null) {
                notifySaveResult(false);
                return;
            }
            if (body == null) {
                notifySaveResult(false);
                return;
            }
            new Thread(new Runnable() {
                public void run() {
                    boolean ok = false;
                    OutputStream os = null;
                    try {
                        os = getContentResolver().openOutputStream(uri, "wt");
                        if (os != null) {
                            os.write(body.getBytes("UTF-8"));
                            os.flush();
                            ok = true;
                        }
                    } catch (Throwable t) {
                        ok = false;
                    } finally {
                        if (os != null) {
                            try { os.close(); } catch (Throwable t) { }
                        }
                    }
                    notifySaveResult(ok);
                }
            }).start();
            return;
        }
        super.onActivityResult(requestCode, resultCode, data);
    }

    @Override
    public void onBackPressed() {
        if (web != null && web.canGoBack()) {
            web.goBack();
            return;
        }
        // 不直接退出：退到后台，避免误触返回键丢掉一轮答题
        moveTaskToBack(true);
    }

    @Override
    protected void onPause() {
        if (web != null) web.onPause();
        super.onPause();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web != null) web.onResume();
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        if (web != null) web.saveState(outState);
    }

    @Override
    protected void onDestroy() {
        if (pendingFileChooser != null) {
            try { pendingFileChooser.onReceiveValue(null); } catch (Throwable t) { }
            pendingFileChooser = null;
        }
        if (tts != null) {
            try {
                tts.stop();
                tts.shutdown();
            } catch (Throwable t) {
            }
            tts = null;
        }
        if (web != null) {
            web.destroy();
            web = null;
        }
        super.onDestroy();
    }
}