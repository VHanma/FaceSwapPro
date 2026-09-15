#!/usr/bin/env python3
"""FaceSwapPro v3.2: $0 Hugging Face ZeroGPU Turbo.

Runs after the v3.1 patch. Replaces the visible paid-RunPod setup with a
user-owned Hugging Face ZeroGPU Space that the Android app creates, configures,
and calls automatically. The only user action left is granting a Hugging Face
write token once; the token is kept in Android Keystore encrypted storage.
"""
from pathlib import Path
import shutil
import sys

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path('neural-upstream').resolve()
JAVA = ROOT / 'app/src/main/java/com/pv/androidfacefusion'
SOURCE_ROOT = Path(__file__).resolve().parent.parent


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding='utf-8')
    if old not in text:
        raise SystemExit(f'Free Turbo anchor missing in {path}: {old[:160]!r}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')


gradle = ROOT / 'app/build.gradle.kts'
replace_once(gradle, 'versionCode = 30100', 'versionCode = 30200')
replace_once(gradle, 'versionName = "3.1.0-alpha-runpod-auto"', 'versionName = "3.2.0-free-zerogpu"')

# Ship the worker source inside the APK so setup never depends on GitHub/raw URLs.
assets = ROOT / 'app/src/main/assets/free_turbo'
assets.mkdir(parents=True, exist_ok=True)
for name in ('app.py', 'requirements.txt', 'README.md'):
    src = SOURCE_ROOT / 'free_turbo' / name
    if not src.exists():
        raise SystemExit(f'Missing Free Turbo worker source: {src}')
    shutil.copy2(src, assets / name)

(JAVA / 'HfZeroGpuProvisioner.java').write_text(r'''package com.pv.androidfacefusion;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Base64;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.Locale;

/** Creates and repairs the user's $0 Hugging Face ZeroGPU Space. */
final class HfZeroGpuProvisioner {
    private static final String HUB = "https://huggingface.co";
    private static final String PREFS = "faceswappro_hf_freeturbo";
    private static final String KEY_HF = "hf_write_token";
    private static final String KEY_SECRET = "hf_worker_secret";
    private static final String KEY_SPACE = "hf_space_id";
    private static final String KEY_ENDPOINT = "hf_space_endpoint";
    private static final String SPACE_NAME = "FaceSwapPro-FreeTurbo";

    interface Callback { void onStatus(String text, int progress); }

    static final class Result {
        final String spaceId, endpoint;
        Result(String spaceId, String endpoint) { this.spaceId = spaceId; this.endpoint = endpoint; }
    }

    private HfZeroGpuProvisioner() {}

    static boolean configured(Context c) {
        return !token(c).isEmpty() && !workerSecret(c).isEmpty() && !endpoint(c).isEmpty();
    }

    static boolean tokenExists(Context c) { return !token(c).isEmpty(); }
    static String token(Context c) { return TurboSecureStore.get(c, KEY_HF); }
    static String workerSecret(Context c) { return TurboSecureStore.get(c, KEY_SECRET); }
    static String spaceId(Context c) { return prefs(c).getString(KEY_SPACE, ""); }
    static String endpoint(Context c) { return prefs(c).getString(KEY_ENDPOINT, ""); }

    static Result provision(Context context, String token, Callback cb) throws Exception {
        token = token == null ? "" : token.trim();
        if (!token.startsWith("hf_") || token.length() < 20) {
            throw new Exception("That does not look like a Hugging Face token");
        }
        if (cb != null) cb.onStatus("Checking Hugging Face permission…", 4);
        JSONObject who = requestJson("GET", HUB + "/api/whoami-v2", token, null, null, true);
        String username = who.optString("name", "").trim();
        if (username.isEmpty()) throw new Exception("Hugging Face did not return your username");
        String repo = username + "/" + SPACE_NAME;
        String secret = workerSecret(context);
        if (secret.isEmpty()) secret = randomSecret();

        if (cb != null) cb.onStatus("Creating your FREE Turbo Space…", 10);
        JSONObject create = new JSONObject();
        create.put("name", SPACE_NAME);
        create.put("type", "space");
        create.put("sdk", "gradio");
        create.put("private", false);
        int createCode = requestCode("POST", HUB + "/api/repos/create", token, create.toString(), "application/json");
        if (!(createCode >= 200 && createCode < 300) && createCode != 409) {
            throw new Exception("Space creation failed HTTP " + createCode + ". Make sure the token has WRITE permission.");
        }

        if (cb != null) cb.onStatus("Locking the worker to this app…", 18);
        JSONObject secretBody = new JSONObject();
        secretBody.put("key", "FSP_WORKER_SECRET");
        secretBody.put("value", secret);
        int secCode = requestCode("POST", HUB + "/api/spaces/" + repo + "/secrets", token,
            secretBody.toString(), "application/json");
        if (secCode < 200 || secCode >= 300) {
            // Update can fail if it already exists; try DELETE then recreate.
            requestCode("DELETE", HUB + "/api/spaces/" + repo + "/secrets/FSP_WORKER_SECRET", token, null, null);
            secCode = requestCode("POST", HUB + "/api/spaces/" + repo + "/secrets", token,
                secretBody.toString(), "application/json");
            if (secCode < 200 || secCode >= 300) throw new Exception("Could not secure the Free Turbo worker (HTTP " + secCode + ")");
        }

        if (cb != null) cb.onStatus("Uploading the GPU worker…", 28);
        commitWorker(context, repo, token);

        if (cb != null) cb.onStatus("Switching to FREE ZeroGPU…", 38);
        JSONObject hw = new JSONObject(); hw.put("flavor", "zero-a10g");
        int hwCode = requestCode("POST", HUB + "/api/spaces/" + repo + "/hardware", token,
            hw.toString(), "application/json");
        if (hwCode < 200 || hwCode >= 300) {
            throw new Exception("ZeroGPU was not granted (HTTP " + hwCode + "). Your free account must be eligible for ZeroGPU.");
        }

        String endpoint = deriveEndpoint(username, SPACE_NAME);
        TurboSecureStore.put(context, KEY_HF, token);
        TurboSecureStore.put(context, KEY_SECRET, secret);
        prefs(context).edit().putString(KEY_SPACE, repo).putString(KEY_ENDPOINT, endpoint).apply();
        waitUntilReady(repo, endpoint, token, cb);
        return new Result(repo, endpoint);
    }

    static Result repair(Context context, Callback cb) throws Exception {
        String t = token(context);
        if (t.isEmpty()) throw new Exception("Free Turbo permission is missing");
        return provision(context, t, cb);
    }

    static void forget(Context context) {
        TurboSecureStore.remove(context, KEY_HF);
        TurboSecureStore.remove(context, KEY_SECRET);
        prefs(context).edit().clear().apply();
    }

    private static void commitWorker(Context context, String repo, String token) throws Exception {
        String[] names = {"README.md", "requirements.txt", "app.py"};
        StringBuilder ndjson = new StringBuilder();
        JSONObject header = new JSONObject();
        header.put("key", "header");
        JSONObject hv = new JSONObject();
        hv.put("summary", "Install FaceSwapPro Free Turbo worker");
        hv.put("description", "Managed automatically by FaceSwapPro Android");
        header.put("value", hv);
        ndjson.append(header).append('\n');
        for (String name : names) {
            byte[] bytes = readAsset(context, "free_turbo/" + name);
            JSONObject op = new JSONObject(); op.put("key", "file");
            JSONObject v = new JSONObject();
            v.put("content", Base64.encodeToString(bytes, Base64.NO_WRAP));
            v.put("path", name); v.put("encoding", "base64");
            op.put("value", v);
            ndjson.append(op).append('\n');
        }
        int code = requestCode("POST", HUB + "/api/spaces/" + repo + "/commit/main", token,
            ndjson.toString(), "application/x-ndjson");
        if (code < 200 || code >= 300) throw new Exception("Worker upload failed HTTP " + code);
    }

    private static byte[] readAsset(Context context, String path) throws Exception {
        try (InputStream in = context.getAssets().open(path); ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] b = new byte[32 * 1024]; int n;
            while ((n = in.read(b)) >= 0) if (n > 0) out.write(b, 0, n);
            return out.toByteArray();
        }
    }

    private static void waitUntilReady(String repo, String endpoint, String token, Callback cb) throws Exception {
        long deadline = System.currentTimeMillis() + 20L * 60L * 1000L;
        int attempt = 0;
        while (System.currentTimeMillis() < deadline) {
            attempt++;
            try {
                JSONObject rt = requestJson("GET", HUB + "/api/spaces/" + repo + "/runtime", token, null, null, true);
                String stage = rt.optString("stage", "");
                String hardware = rt.optString("hardware", rt.optString("requestedHardware", ""));
                if (cb != null) cb.onStatus(
                    stage.contains("BUILD") ? "Building Free Turbo once…" : "Starting ZeroGPU worker…",
                    Math.min(88, 42 + attempt));
                if (stage.startsWith("RUNNING")) {
                    HttpURLConnection c = (HttpURLConnection) new URL(endpoint + "/gradio_api/info").openConnection();
                    c.setRequestProperty("Authorization", "Bearer " + token);
                    c.setConnectTimeout(8_000); c.setReadTimeout(10_000);
                    int code = c.getResponseCode(); c.disconnect();
                    if (code == 200) {
                        if (cb != null) cb.onStatus("FREE TURBO READY • $0", 100);
                        return;
                    }
                }
            } catch (Exception ignored) {}
            Thread.sleep(5_000L);
        }
        throw new Exception("Free Turbo Space is still building. Tap SET UP FREE TURBO again to re-check it.");
    }

    private static JSONObject requestJson(String method, String url, String token, String body,
                                          String contentType, boolean fail) throws Exception {
        HttpURLConnection c = open(method, url, token, body, contentType);
        int code = c.getResponseCode();
        String text = read(code >= 400 ? c.getErrorStream() : c.getInputStream()); c.disconnect();
        if (fail && (code < 200 || code >= 300)) throw new Exception("Hugging Face HTTP " + code + ": " + text);
        return text.trim().isEmpty() ? new JSONObject() : new JSONObject(text);
    }

    private static int requestCode(String method, String url, String token, String body, String contentType) throws Exception {
        HttpURLConnection c = open(method, url, token, body, contentType);
        int code = c.getResponseCode();
        InputStream in = code >= 400 ? c.getErrorStream() : c.getInputStream();
        if (in != null) try { while (in.read() >= 0) {} } finally { in.close(); }
        c.disconnect(); return code;
    }

    private static HttpURLConnection open(String method, String url, String token, String body, String contentType) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setRequestMethod(method); c.setConnectTimeout(20_000); c.setReadTimeout(120_000);
        c.setRequestProperty("Authorization", "Bearer " + token);
        c.setRequestProperty("Accept", "application/json, */*");
        if (body != null) {
            c.setDoOutput(true); c.setRequestProperty("Content-Type", contentType == null ? "application/json" : contentType);
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            try (OutputStream out = c.getOutputStream()) { out.write(bytes); }
        }
        return c;
    }

    private static String read(InputStream in) throws Exception {
        if (in == null) return "";
        try (InputStream input = in; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] b = new byte[32 * 1024]; int n;
            while ((n = input.read(b)) >= 0) if (n > 0) out.write(b, 0, n);
            return out.toString(StandardCharsets.UTF_8.name());
        }
    }

    private static String randomSecret() {
        byte[] b = new byte[32]; new SecureRandom().nextBytes(b);
        return Base64.encodeToString(b, Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING);
    }

    private static String deriveEndpoint(String username, String name) {
        String sub = (username + "-" + name).toLowerCase(Locale.US).replaceAll("[^a-z0-9-]", "-");
        while (sub.contains("--")) sub = sub.replace("--", "-");
        return "https://" + sub + ".hf.space";
    }

    private static SharedPreferences prefs(Context c) { return c.getSharedPreferences(PREFS, Context.MODE_PRIVATE); }
}
''', encoding='utf-8')

(JAVA / 'FreeTurboClient.java').write_text(r'''package com.pv.androidfacefusion;

import android.content.Context;
import android.graphics.Bitmap;
import android.net.Uri;

import org.json.JSONArray;
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
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

/** Direct authenticated Gradio/ZeroGPU client. */
final class FreeTurboClient {
    interface Callback { void onStatus(String text, int progress); }
    private FreeTurboClient() {}

    static File process(Context context, Bitmap source, Uri videoUri, String quality, Callback cb) throws Exception {
        if (!HfZeroGpuProvisioner.configured(context)) throw new Exception("Free Turbo is not set up yet");
        String token = HfZeroGpuProvisioner.token(context);
        String secret = HfZeroGpuProvisioner.workerSecret(context);
        String endpoint = HfZeroGpuProvisioner.endpoint(context);
        if (cb != null) cb.onStatus("Uploading to your Free Turbo worker…", 5);

        File sourceFile = new File(context.getCacheDir(), "free_turbo_source_" + System.currentTimeMillis() + ".jpg");
        try (FileOutputStream out = new FileOutputStream(sourceFile)) {
            if (!source.compress(Bitmap.CompressFormat.JPEG, 96, out)) throw new Exception("Could not encode source face");
        }
        JSONArray uploaded;
        try {
            uploaded = uploadFiles(context, endpoint, token, sourceFile, videoUri, cb);
        } finally {
            sourceFile.delete();
        }
        if (uploaded.length() < 2) throw new Exception("Free Turbo upload returned incomplete file data");
        String srcPath = uploaded.getString(0), vidPath = uploaded.getString(1);

        JSONObject src = fileData(srcPath, "source.jpg");
        JSONObject vid = fileData(vidPath, "target.mp4");
        JSONArray data = new JSONArray().put(src).put(vid).put(secret).put(quality == null ? "Ultra 512" : quality);
        JSONObject payload = new JSONObject().put("data", data);
        if (cb != null) cb.onStatus("Entering ZeroGPU queue…", 20);

        JSONObject submitted;
        try {
            submitted = postJson(endpoint + "/gradio_api/call/swap_video", token, payload);
        } catch (Exception first) {
            submitted = postJson(endpoint + "/gradio_api/call/v2/swap_video", token, payload);
        }
        String event = submitted.optString("event_id", "");
        if (event.isEmpty()) throw new Exception("Free Turbo did not return a job ID: " + submitted);
        if (cb != null) cb.onStatus("FREE GPU rendering…", 28);

        String resultUrl = pollSse(endpoint, token, event, cb);
        if (resultUrl.isEmpty()) throw new Exception("Free Turbo completed without a video URL");
        if (!resultUrl.startsWith("http")) {
            if (resultUrl.startsWith("/")) resultUrl = endpoint + resultUrl;
            else resultUrl = endpoint + "/gradio_api/file=" + URLEncoder.encode(resultUrl, "UTF-8");
        }
        if (cb != null) cb.onStatus("Downloading finished video…", 94);
        File out = new File(context.getCacheDir(), "FaceSwapPro_FreeTurbo_" + System.currentTimeMillis() + ".mp4");
        download(resultUrl, token, out);
        if (!out.exists() || out.length() < 1024) throw new Exception("Downloaded Free Turbo result was empty");
        if (cb != null) cb.onStatus("FREE TURBO COMPLETE", 100);
        return out;
    }

    private static JSONArray uploadFiles(Context context, String endpoint, String token, File source,
                                         Uri videoUri, Callback cb) throws Exception {
        String boundary = "----FSPFreeTurbo" + Long.toHexString(System.nanoTime());
        HttpURLConnection c = (HttpURLConnection) new URL(endpoint + "/gradio_api/upload").openConnection();
        c.setRequestMethod("POST"); c.setDoOutput(true); c.setChunkedStreamingMode(512 * 1024);
        c.setConnectTimeout(30_000); c.setReadTimeout(180_000);
        c.setRequestProperty("Authorization", "Bearer " + token);
        c.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
        try (OutputStream out = new BufferedOutputStream(c.getOutputStream(), 512 * 1024)) {
            writeUpload(out, boundary, "source.jpg", "image/jpeg", new FileInputStream(source));
            InputStream video = context.getContentResolver().openInputStream(videoUri);
            if (video == null) throw new Exception("Could not open target video");
            writeUpload(out, boundary, "target.mp4", "video/mp4", new BufferedInputStream(video, 512 * 1024));
            out.write(("--" + boundary + "--\r\n").getBytes(StandardCharsets.UTF_8));
        }
        int code = c.getResponseCode();
        String text = read(code >= 400 ? c.getErrorStream() : c.getInputStream()); c.disconnect();
        if (code < 200 || code >= 300) throw new Exception("Free Turbo upload HTTP " + code + ": " + text);
        if (cb != null) cb.onStatus("Upload complete", 18);
        return new JSONArray(text);
    }

    private static void writeUpload(OutputStream out, String boundary, String filename, String mime, InputStream in) throws Exception {
        out.write(("--" + boundary + "\r\nContent-Disposition: form-data; name=\"files\"; filename=\"" + filename + "\"\r\nContent-Type: " + mime + "\r\n\r\n").getBytes(StandardCharsets.UTF_8));
        try (InputStream input = in) {
            byte[] b = new byte[512 * 1024]; int n;
            while ((n = input.read(b)) >= 0) if (n > 0) out.write(b, 0, n);
        }
        out.write("\r\n".getBytes(StandardCharsets.UTF_8));
    }

    private static JSONObject fileData(String path, String name) throws Exception {
        JSONObject meta = new JSONObject().put("_type", "gradio.FileData");
        return new JSONObject().put("path", path).put("orig_name", name).put("meta", meta);
    }

    private static JSONObject postJson(String url, String token, JSONObject payload) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setRequestMethod("POST"); c.setDoOutput(true); c.setConnectTimeout(20_000); c.setReadTimeout(60_000);
        c.setRequestProperty("Authorization", "Bearer " + token);
        c.setRequestProperty("Content-Type", "application/json");
        try (OutputStream out = c.getOutputStream()) { out.write(payload.toString().getBytes(StandardCharsets.UTF_8)); }
        int code = c.getResponseCode(); String text = read(code >= 400 ? c.getErrorStream() : c.getInputStream()); c.disconnect();
        if (code < 200 || code >= 300) throw new Exception("ZeroGPU submit HTTP " + code + ": " + text);
        return new JSONObject(text);
    }

    private static String pollSse(String endpoint, String token, String event, Callback cb) throws Exception {
        String[] paths = {"/gradio_api/call/swap_video/" + event, "/gradio_api/call/v2/swap_video/" + event};
        Exception last = null;
        for (String path : paths) {
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(endpoint + path).openConnection();
                c.setRequestProperty("Authorization", "Bearer " + token);
                c.setRequestProperty("Accept", "text/event-stream");
                c.setConnectTimeout(20_000); c.setReadTimeout(360_000);
                int code = c.getResponseCode();
                if (code < 200 || code >= 300) {
                    String body = read(c.getErrorStream()); c.disconnect();
                    throw new Exception("ZeroGPU queue HTTP " + code + ": " + body);
                }
                try (BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream(), StandardCharsets.UTF_8))) {
                    String line, eventType = "";
                    while ((line = r.readLine()) != null) {
                        if (line.startsWith("event:")) eventType = line.substring(6).trim();
                        else if (line.startsWith("data:")) {
                            String d = line.substring(5).trim();
                            if ("error".equals(eventType)) throw new Exception("ZeroGPU error: " + d);
                            if ("complete".equals(eventType)) {
                                c.disconnect(); return extractResultUrl(d);
                            }
                            if (cb != null && ("generating".equals(eventType) || "progress".equals(eventType)))
                                cb.onStatus("FREE GPU rendering…", 65);
                        }
                    }
                } finally { c.disconnect(); }
            } catch (Exception e) { last = e; }
        }
        throw last == null ? new Exception("Free Turbo queue ended unexpectedly") : last;
    }

    private static String extractResultUrl(String data) throws Exception {
        JSONArray arr = new JSONArray(data);
        if (arr.length() == 0 || arr.isNull(0)) return "";
        Object first = arr.get(0);
        if (first instanceof String) return (String) first;
        if (first instanceof JSONObject) {
            JSONObject o = (JSONObject) first;
            String url = o.optString("url", "");
            return url.isEmpty() ? o.optString("path", "") : url;
        }
        return "";
    }

    private static void download(String url, String token, File out) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setRequestProperty("Authorization", "Bearer " + token);
        c.setConnectTimeout(20_000); c.setReadTimeout(240_000);
        int code = c.getResponseCode();
        if (code != 200) { String text = read(c.getErrorStream()); c.disconnect(); throw new Exception("Result download HTTP " + code + ": " + text); }
        try (InputStream in = new BufferedInputStream(c.getInputStream(), 512 * 1024);
             OutputStream os = new BufferedOutputStream(new FileOutputStream(out), 512 * 1024)) {
            byte[] b = new byte[512 * 1024]; int n;
            while ((n = in.read(b)) >= 0) if (n > 0) os.write(b, 0, n);
        } finally { c.disconnect(); }
    }

    private static String read(InputStream in) throws Exception {
        if (in == null) return "";
        try (InputStream input = in; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] b = new byte[32 * 1024]; int n;
            while ((n = input.read(b)) >= 0) if (n > 0) out.write(b, 0, n);
            return out.toString(StandardCharsets.UTF_8.name());
        }
    }
}
''', encoding='utf-8')

activity = JAVA / 'VideoSwapActivity.java'
s = activity.read_text(encoding='utf-8')
s = s.replace('title.setText("FaceSwap Pro • TURBO VIDEO");', 'title.setText("FaceSwap Pro • FREE TURBO VIDEO");')
s = s.replace('sub.setText("Turbo Cloud sends the source + video once to a GPU worker for web-class speed and quality. Local Private stays fully on-device but is slower.");',
              'sub.setText("FREE Turbo uses your personal Hugging Face ZeroGPU allowance. $0 GPU rental. Local Private stays offline as fallback.");')
s = s.replace('modeTurbo.setText("⚡ Turbo Cloud GPU");', 'modeTurbo.setText("⚡ FREE TURBO • $0");')
s = s.replace('configureTurbo.setText("AUTO SETUP TURBO GPU");', 'configureTurbo.setText("SET UP FREE TURBO");')
s = s.replace('stopTurbo.setText("STOP GPU / SAVE MONEY");', 'stopTurbo.setText("PAID GPU DISABLED");\n        stopTurbo.setVisibility(android.view.View.GONE);')
s = s.replace('status.setText("Turbo Cloud selected • phone AI is asleep");', 'status.setText("FREE Turbo selected • no paid GPU");')
s = s.replace('status.setText("Turbo Cloud selected • GPU rendering");', 'status.setText("FREE Turbo selected • ZeroGPU rendering");')
s = s.replace('configureTurbo.setOnClickListener(v -> showRunPodSetup());\n        stopTurbo.setOnClickListener(v -> stopTurboGpu());',
              'configureTurbo.setOnClickListener(v -> showFreeTurboSetup());\n        stopTurbo.setOnClickListener(v -> {});')

# Replace cloud render precheck + engine.
s = s.replace('''        CloudTurboClient.Config config = CloudTurboClient.load(this);\n        if (!config.ready()) {\n            showRunPodSetup();\n            Toast.makeText(this, "Set up Turbo once, then tap Render", Toast.LENGTH_LONG).show();\n            return;\n        }''', '''        if (!HfZeroGpuProvisioner.configured(this)) {\n            showFreeTurboSetup();\n            Toast.makeText(this, "Set up FREE Turbo once, then tap Render", Toast.LENGTH_LONG).show();\n            return;\n        }''')
s = s.replace('temp = CloudTurboClient.process(this, source, video, "pro", (text, pct) ->',
              'temp = FreeTurboClient.process(this, source, video, "Ultra 512", (text, pct) ->')
s = s.replace('                RunPodProvisioner.scheduleAutoStop(this, 20);\n', '')
s = s.replace('status.setText("Turbo complete • saved to Movies/FaceSwapPro");', 'status.setText("FREE Turbo complete • saved to Movies/FaceSwapPro");')
s = s.replace('Toast.makeText(this, "Turbo GPU video saved", Toast.LENGTH_LONG).show();', 'Toast.makeText(this, "FREE Turbo video saved", Toast.LENGTH_LONG).show();')

# Replace the visible one-tap RunPod setup method. Leave the legacy stop/manual
# methods compiled but unreachable, preserving backwards source compatibility.
start = s.index('    private void showRunPodSetup() {')
end = s.index('    private void stopTurboGpu() {', start)
free_method = r'''    private void showFreeTurboSetup() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        int p = dp(16); box.setPadding(p, p, p, 0);

        TextView steps = new TextView(this);
        steps.setText("ONLY ONCE:\n1. Tap OPEN HUGGING FACE\n2. Make a WRITE token named FaceSwapPro\n3. Copy it\n4. Come back and tap PASTE TOKEN\n5. Tap BUILD FREE TURBO\n\nNo RunPod. No card. No paid GPU.");
        box.addView(steps);

        Button open = new Button(this); open.setText("OPEN HUGGING FACE TOKEN PAGE"); box.addView(open);
        EditText key = new EditText(this); key.setHint("hf_... token"); key.setSingleLine(true);
        key.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD); box.addView(key);
        Button paste = new Button(this); paste.setText("PASTE TOKEN FROM CLIPBOARD"); box.addView(paste);

        open.setOnClickListener(v -> {
            try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("https://huggingface.co/settings/tokens"))); }
            catch (Exception e) { Toast.makeText(this, "Open huggingface.co/settings/tokens", Toast.LENGTH_LONG).show(); }
        });
        paste.setOnClickListener(v -> {
            android.content.ClipboardManager cm = (android.content.ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
            if (cm != null && cm.hasPrimaryClip() && cm.getPrimaryClip() != null && cm.getPrimaryClip().getItemCount() > 0) {
                CharSequence t = cm.getPrimaryClip().getItemAt(0).coerceToText(this);
                key.setText(t == null ? "" : t.toString().trim());
            } else Toast.makeText(this, "Copy the Hugging Face token first", Toast.LENGTH_SHORT).show();
        });

        AlertDialog dialog = new AlertDialog.Builder(this)
            .setTitle(HfZeroGpuProvisioner.configured(this) ? "FREE Turbo is linked" : "Set up FREE Turbo")
            .setView(box)
            .setPositiveButton(HfZeroGpuProvisioner.configured(this) ? "CHECK / REPAIR" : "BUILD FREE TURBO", null)
            .setNegativeButton("Close", null)
            .create();
        dialog.setOnShowListener(x -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            String token = key.getText().toString().trim();
            if (token.isEmpty() && HfZeroGpuProvisioner.configured(this)) {
                dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(false);
                status.setText("Checking FREE Turbo…");
                executor.execute(() -> {
                    try {
                        HfZeroGpuProvisioner.repair(this, (text, pct) -> runOnUiThread(() -> { status.setText(text); progress.setProgress(pct); }));
                        runOnUiThread(() -> { dialog.dismiss(); status.setText("FREE TURBO READY • $0"); progress.setProgress(100); });
                    } catch (Exception e) {
                        runOnUiThread(() -> { dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(true); status.setText("Free Turbo check: " + e.getMessage()); });
                    }
                });
                return;
            }
            if (!token.startsWith("hf_") || token.length() < 20) { key.setError("Paste the hf_ token you copied"); return; }
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(false);
            status.setText("Building your FREE ZeroGPU worker…"); progress.setProgress(2);
            executor.execute(() -> {
                try {
                    HfZeroGpuProvisioner.provision(this, token, (text, pct) -> runOnUiThread(() -> { status.setText(text); progress.setProgress(pct); }));
                    runOnUiThread(() -> {
                        dialog.dismiss(); progress.setProgress(100); status.setText("FREE TURBO READY • $0");
                        Toast.makeText(this, "Free Turbo is ready", Toast.LENGTH_LONG).show();
                    });
                } catch (Exception e) {
                    runOnUiThread(() -> {
                        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(true);
                        status.setText("Free Turbo setup: " + e.getMessage());
                        Toast.makeText(this, "Free Turbo setup needs attention", Toast.LENGTH_LONG).show();
                    });
                }
            });
        }));
        dialog.show();
    }

'''
s = s[:start] + free_method + s[end:]
activity.write_text(s, encoding='utf-8')

checks = {
    gradle: ['versionCode = 30200', 'versionName = "3.2.0-free-zerogpu"'],
    JAVA / 'HfZeroGpuProvisioner.java': ['zero-a10g', '/api/repos/create', '/commit/main', 'FSP_WORKER_SECRET'],
    JAVA / 'FreeTurboClient.java': ['/gradio_api/upload', '/gradio_api/call/swap_video', 'Authorization'],
    activity: ['SET UP FREE TURBO', 'FREE TURBO READY • $0', 'PAID GPU DISABLED', 'HfZeroGpuProvisioner.provision'],
    assets / 'app.py': ['@spaces.GPU', 'swap_video', 'simswap_512_beta.pth'],
}
for path, needles in checks.items():
    body = path.read_text(encoding='utf-8')
    for needle in needles:
        if needle not in body:
            raise SystemExit(f'Missing v3.2 Free Turbo guard in {path}: {needle}')

print('OMEGA_FREE_TURBO_V32_OK')
print('visible cloud path=Hugging Face ZeroGPU; RunPod UI disabled; local private fallback preserved')
