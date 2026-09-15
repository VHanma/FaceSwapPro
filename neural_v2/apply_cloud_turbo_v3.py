#!/usr/bin/env python3
"""FaceSwapPro v3 Turbo Cloud patch.

Runs after v2.0.3 fastload. Video defaults to remote GPU rendering and local
models stay asleep unless the user explicitly selects Local Private.
"""
from pathlib import Path
import sys

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path('neural-upstream').resolve()
JAVA = ROOT / 'app/src/main/java/com/pv/androidfacefusion'


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding='utf-8')
    if old not in text:
        raise SystemExit(f'Cloud Turbo anchor missing in {path}: {old[:140]!r}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')

# Version.
gradle = ROOT / 'app/build.gradle.kts'
replace_once(gradle, 'versionCode = 20004', 'versionCode = 30001')
replace_once(gradle, 'versionName = "2.0.3-alpha-fastload"', 'versionName = "3.0.0-alpha-cloud-turbo"')

# ---------------------------------------------------------------------------
# Native HTTP client. Upload once, poll GPU job, download finished MP4.
# ---------------------------------------------------------------------------
(JAVA / 'CloudTurboClient.java').write_text(r'''package com.pv.androidfacefusion;

import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.net.Uri;

import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/** Client for FaceSwapPro Turbo GPU service. */
public final class CloudTurboClient {
    private static final String PREFS = "faceswappro_turbo";
    private static final String KEY_ENDPOINT = "endpoint";
    private static final String KEY_TOKEN = "token";

    public interface Callback {
        void onStatus(String text, int progress);
    }

    public static final class Config {
        public final String endpoint;
        public final String token;
        Config(String endpoint, String token) {
            this.endpoint = normalize(endpoint);
            this.token = token == null ? "" : token.trim();
        }
        public boolean ready() { return endpoint.startsWith("http://") || endpoint.startsWith("https://"); }
    }

    private CloudTurboClient() {}

    public static Config load(Context context) {
        SharedPreferences p = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        return new Config(p.getString(KEY_ENDPOINT, ""), p.getString(KEY_TOKEN, ""));
    }

    public static void save(Context context, String endpoint, String token) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString(KEY_ENDPOINT, normalize(endpoint))
            .putString(KEY_TOKEN, token == null ? "" : token.trim())
            .apply();
    }

    public static File process(Context context, Bitmap source, Uri videoUri,
                               String quality, Callback callback) throws Exception {
        Config config = load(context);
        if (!config.ready()) throw new Exception("Turbo GPU endpoint is not configured");
        if (callback != null) callback.onStatus("Preparing Turbo upload…", 2);

        File sourceFile = new File(context.getCacheDir(), "turbo_source_" + System.currentTimeMillis() + ".jpg");
        try (FileOutputStream out = new FileOutputStream(sourceFile)) {
            if (!source.compress(Bitmap.CompressFormat.JPEG, 96, out)) {
                throw new Exception("Could not encode source face");
            }
        }

        String boundary = "----FaceSwapPro" + Long.toHexString(System.nanoTime());
        HttpURLConnection conn = open(config.endpoint + "/v1/video-swap", "POST", config);
        conn.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
        conn.setChunkedStreamingMode(512 * 1024);
        conn.setDoOutput(true);
        conn.setConnectTimeout(30_000);
        conn.setReadTimeout(120_000);

        try (OutputStream raw = new BufferedOutputStream(conn.getOutputStream(), 512 * 1024)) {
            writeField(raw, boundary, "quality", quality == null ? "pro" : quality);
            writeFile(raw, boundary, "source", "source.jpg", "image/jpeg", new FileInputStream(sourceFile), callback, 3, 8);
            try (InputStream video = new BufferedInputStream(context.getContentResolver().openInputStream(videoUri), 512 * 1024)) {
                if (video == null) throw new Exception("Could not open target video");
                writeFile(raw, boundary, "target", "target.mp4", "video/mp4", video, callback, 8, 18);
            }
            raw.write(("--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8));
            raw.flush();
        } finally {
            sourceFile.delete();
        }

        int code = conn.getResponseCode();
        String response = readText(code >= 400 ? conn.getErrorStream() : conn.getInputStream());
        conn.disconnect();
        if (code < 200 || code >= 300) throw new Exception("Turbo upload failed HTTP " + code + ": " + response);

        JSONObject initial = new JSONObject(response);
        String jobId = initial.getString("job_id");
        if (callback != null) callback.onStatus("GPU job queued", 20);

        long deadline = System.currentTimeMillis() + 2L * 60L * 60L * 1000L;
        while (System.currentTimeMillis() < deadline) {
            Thread.sleep(750);
            HttpURLConnection poll = open(config.endpoint + "/v1/jobs/" + jobId, "GET", config);
            poll.setConnectTimeout(15_000);
            poll.setReadTimeout(30_000);
            int pc = poll.getResponseCode();
            String body = readText(pc >= 400 ? poll.getErrorStream() : poll.getInputStream());
            poll.disconnect();
            if (pc < 200 || pc >= 300) throw new Exception("Turbo status HTTP " + pc + ": " + body);
            JSONObject j = new JSONObject(body);
            String status = j.optString("status", "running");
            int serverProgress = j.optInt("progress", 25);
            int mapped = Math.max(20, Math.min(94, 20 + (serverProgress * 74 / 100)));
            if (callback != null) callback.onStatus(j.optString("detail", "GPU rendering"), mapped);
            if ("failed".equals(status)) throw new Exception(j.optString("detail", "Turbo render failed"));
            if ("completed".equals(status)) break;
        }

        if (System.currentTimeMillis() >= deadline) throw new Exception("Turbo render timed out");
        if (callback != null) callback.onStatus("Downloading finished MP4…", 95);

        HttpURLConnection result = open(config.endpoint + "/v1/jobs/" + jobId + "/result", "GET", config);
        result.setConnectTimeout(20_000);
        result.setReadTimeout(180_000);
        int rc = result.getResponseCode();
        if (rc != 200) {
            String body = readText(result.getErrorStream());
            result.disconnect();
            throw new Exception("Turbo result HTTP " + rc + ": " + body);
        }
        File out = new File(context.getCacheDir(), "faceswappro_turbo_" + System.currentTimeMillis() + ".mp4");
        try (InputStream in = new BufferedInputStream(result.getInputStream(), 512 * 1024);
             OutputStream os = new BufferedOutputStream(new FileOutputStream(out), 512 * 1024)) {
            byte[] buf = new byte[512 * 1024];
            int n;
            while ((n = in.read(buf)) >= 0) if (n > 0) os.write(buf, 0, n);
        } finally {
            result.disconnect();
        }
        if (!out.exists() || out.length() < 1024) throw new Exception("Turbo result was empty");
        if (callback != null) callback.onStatus("Turbo video ready", 100);
        return out;
    }

    private static HttpURLConnection open(String url, String method, Config config) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setRequestMethod(method);
        c.setRequestProperty("Accept", "application/json, video/mp4, */*");
        if (!config.token.isEmpty()) c.setRequestProperty("Authorization", "Bearer " + config.token);
        return c;
    }

    private static void writeField(OutputStream out, String boundary, String name, String value) throws Exception {
        out.write(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + name + "\"\r\n\r\n" + value + "\r\n").getBytes(StandardCharsets.UTF_8));
    }

    private static void writeFile(OutputStream out, String boundary, String name, String filename,
                                  String mime, InputStream in, Callback cb, int from, int to) throws Exception {
        out.write(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + name + "\"; filename=\"" + filename + "\"\r\nContent-Type: " + mime + "\r\n\r\n").getBytes(StandardCharsets.UTF_8));
        byte[] buf = new byte[512 * 1024];
        int n;
        long total = 0;
        while ((n = in.read(buf)) >= 0) {
            if (n == 0) continue;
            out.write(buf, 0, n);
            total += n;
            if (cb != null && (total % (4L * 1024L * 1024L)) < n) cb.onStatus("Uploading to GPU…", Math.min(to, from + 1));
        }
        in.close();
        out.write("\r\n".getBytes(StandardCharsets.UTF_8));
    }

    private static String readText(InputStream in) throws Exception {
        if (in == null) return "";
        try (InputStream input = in; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] buf = new byte[32 * 1024];
            int n;
            while ((n = input.read(buf)) >= 0) if (n > 0) out.write(buf, 0, n);
            return out.toString(StandardCharsets.UTF_8.name());
        }
    }

    private static String normalize(String endpoint) {
        if (endpoint == null) return "";
        String s = endpoint.trim();
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        return s;
    }
}
''', encoding='utf-8')

# ---------------------------------------------------------------------------
# Turn the existing video screen into Cloud Turbo (default) + Local Private.
# Crucially, remove warmModels() from onCreate so opening video mode is instant.
# ---------------------------------------------------------------------------
activity = JAVA / 'VideoSwapActivity.java'
s = activity.read_text(encoding='utf-8')
s = s.replace('import android.os.Bundle;\n', 'import android.os.Bundle;\nimport android.app.AlertDialog;\nimport android.widget.EditText;\n', 1)
s = s.replace('    private Button pickSource, pickVideo, render;\n',
              '    private Button pickSource, pickVideo, render;\n    private Button modeTurbo, modeLocal, configureTurbo;\n    private boolean cloudMode = true;\n', 1)
s = s.replace('        setupPickers();\n        warmModels();', '        setupPickers();', 1)

ui_anchor = '''        root.addView(sub, subLp);\n\n        sourcePreview = new ImageView(this);'''
ui_insert = '''        root.addView(sub, subLp);\n\n        modeTurbo = new Button(this);\n        modeTurbo.setText("⚡ Turbo Cloud GPU");\n        root.addView(modeTurbo, fullButtonLp());\n        modeLocal = new Button(this);\n        modeLocal.setText("🔒 Local Private (slow)");\n        root.addView(modeLocal, fullButtonLp());\n        configureTurbo = new Button(this);\n        configureTurbo.setText("Configure Turbo GPU");\n        root.addView(configureTurbo, fullButtonLp());\n\n        sourcePreview = new ImageView(this);'''
if ui_anchor not in s: raise SystemExit('Video UI insertion anchor missing')
s = s.replace(ui_anchor, ui_insert, 1)

s = s.replace('title.setText("Neural Video Swap • FAST");', 'title.setText("FaceSwap Pro • TURBO VIDEO");')
s = s.replace('sub.setText("Fast mobile neural video: one neural pass per frame, dominant-face tracking, up to 12 fps and 1280px working resolution. Photos still use Ultra512. Original audio is preserved.");',
              'sub.setText("Turbo Cloud sends the source + video once to a GPU worker for web-class speed and quality. Local Private stays fully on-device but is slower.");')
s = s.replace('render.setText("Render FAST neural video");', 'render.setText("Render TURBO GPU video");')
s = s.replace('status.setText("Loading neural engine...");', 'status.setText("Turbo Cloud selected • phone AI is asleep");')

listener_anchor = '        render.setOnClickListener(v -> renderVideo());\n'
listener_new = '''        render.setOnClickListener(v -> renderVideo());\n        modeTurbo.setOnClickListener(v -> {\n            cloudMode = true;\n            render.setText("Render TURBO GPU video");\n            status.setText("Turbo Cloud selected • GPU rendering");\n            updateRenderEnabled();\n        });\n        modeLocal.setOnClickListener(v -> {\n            cloudMode = false;\n            render.setText("Render LOCAL private video");\n            status.setText("Local Private selected • models load only after Render");\n            updateRenderEnabled();\n        });\n        configureTurbo.setOnClickListener(v -> showTurboConfig());\n'''
if listener_anchor not in s: raise SystemExit('Video listener anchor missing')
s = s.replace(listener_anchor, listener_new, 1)

# Keep warmModels method available for legacy code, but it is never called on open.
old_enable = '        render.setEnabled(sourceBitmap != null && targetVideoUri != null && NeuralCore.isReady());'
s = s.replace(old_enable, '        render.setEnabled(sourceBitmap != null && targetVideoUri != null);', 1)

render_head = '''    private void renderVideo() {\n        final Bitmap source = sourceBitmap;\n        final Uri video = targetVideoUri;\n        if (source == null || video == null) return;'''
render_new = '''    private void renderVideo() {\n        if (cloudMode) {\n            renderCloudVideo();\n            return;\n        }\n        renderLocalVideo();\n    }\n\n    private void renderLocalVideo() {\n        final Bitmap source = sourceBitmap;\n        final Uri video = targetVideoUri;\n        if (source == null || video == null) return;'''
if render_head not in s: raise SystemExit('renderVideo anchor missing')
s = s.replace(render_head, render_new, 1)

save_anchor = '    private Uri saveToMovies(File temp) throws Exception {\n'
cloud_methods = r'''    private void renderCloudVideo() {
        final Bitmap source = sourceBitmap;
        final Uri video = targetVideoUri;
        if (source == null || video == null) return;
        CloudTurboClient.Config config = CloudTurboClient.load(this);
        if (!config.ready()) {
            showTurboConfig();
            Toast.makeText(this, "Enter your GPU endpoint once, then tap Turbo Render", Toast.LENGTH_LONG).show();
            return;
        }
        setControls(false);
        progress.setProgress(0);
        status.setText("Uploading once to Turbo GPU…");
        executor.execute(() -> {
            File temp = null;
            try {
                temp = CloudTurboClient.process(this, source, video, "pro", (text, pct) ->
                    runOnUiThread(() -> { progress.setProgress(pct); status.setText(text); }));
                Uri saved = saveToMovies(temp);
                runOnUiThread(() -> {
                    progress.setProgress(100);
                    status.setText("Turbo complete • saved to Movies/FaceSwapPro");
                    Toast.makeText(this, "Turbo GPU video saved", Toast.LENGTH_LONG).show();
                    setControls(true);
                });
            } catch (Exception e) {
                runOnUiThread(() -> {
                    status.setText("Turbo failed: " + e.getMessage());
                    Toast.makeText(this, "Turbo render failed", Toast.LENGTH_LONG).show();
                    setControls(true);
                });
            } finally {
                if (temp != null && temp.exists()) temp.delete();
            }
        });
    }

    private void showTurboConfig() {
        CloudTurboClient.Config current = CloudTurboClient.load(this);
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        int p = dp(16);
        box.setPadding(p, p, p, 0);
        EditText endpoint = new EditText(this);
        endpoint.setHint("https://your-gpu-worker.example.com");
        endpoint.setText(current.endpoint);
        box.addView(endpoint);
        EditText token = new EditText(this);
        token.setHint("Turbo API key (optional on private worker)");
        token.setText(current.token);
        box.addView(token);
        new AlertDialog.Builder(this)
            .setTitle("Turbo GPU")
            .setMessage("Premium-site speed comes from a remote NVIDIA GPU. Enter the worker URL once. Media is uploaded only when Turbo mode is used.")
            .setView(box)
            .setPositiveButton("Save", (d, w) -> {
                CloudTurboClient.save(this, endpoint.getText().toString(), token.getText().toString());
                status.setText(CloudTurboClient.load(this).ready()
                    ? "Turbo GPU configured • ready" : "Turbo endpoint still missing");
                updateRenderEnabled();
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

''' + save_anchor
if save_anchor not in s: raise SystemExit('saveToMovies anchor missing')
s = s.replace(save_anchor, cloud_methods, 1)

# setControls must not require local model readiness in cloud mode.
s = s.replace('render.setEnabled(enabled && sourceBitmap != null && targetVideoUri != null && NeuralCore.isReady());',
              'render.setEnabled(enabled && sourceBitmap != null && targetVideoUri != null);')
activity.write_text(s, encoding='utf-8')

checks = {
    gradle: ['versionCode = 30001', 'versionName = "3.0.0-alpha-cloud-turbo"'],
    JAVA / 'CloudTurboClient.java': ['/v1/video-swap', '/v1/jobs/', 'Authorization'],
    activity: ['Turbo Cloud GPU', 'renderCloudVideo()', 'showTurboConfig()', 'Local Private'],
}
for path, needles in checks.items():
    body = path.read_text(encoding='utf-8')
    for needle in needles:
        if needle not in body:
            raise SystemExit(f'Missing Cloud Turbo guard in {path}: {needle}')

body = activity.read_text(encoding='utf-8')
oncreate = body[body.index('protected void onCreate'):body.index('private void buildUi')]
if 'warmModels();' in oncreate:
    raise SystemExit('Video screen still eagerly loads local models')

print('OMEGA_CLOUD_TURBO_V3_OK')
print('video default=remote GPU; local inference=explicit fallback only')
