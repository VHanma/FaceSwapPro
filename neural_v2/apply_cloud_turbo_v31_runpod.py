#!/usr/bin/env python3
"""FaceSwapPro v3.1: secure one-tap RunPod Turbo provisioning.

Runs after apply_cloud_turbo_v3.py. The Android app provisions an NVIDIA Pod
itself from a RunPod API key, launches the public FaceFusion TensorRT image,
bootstraps our pinned FastAPI controller, stores secrets in Android Keystore,
auto-resumes a stopped Pod before rendering, and schedules a cost-saving stop.
"""
from pathlib import Path
import sys

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path('neural-upstream').resolve()
JAVA = ROOT / 'app/src/main/java/com/pv/androidfacefusion'


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding='utf-8')
    if old not in text:
        raise SystemExit(f'RunPod patch anchor missing in {path}: {old[:160]!r}')
    path.write_text(text.replace(old, new, 1), encoding='utf-8')


gradle = ROOT / 'app/build.gradle.kts'
replace_once(gradle, 'versionCode = 30001', 'versionCode = 30100')
replace_once(gradle, 'versionName = "3.0.0-alpha-cloud-turbo"', 'versionName = "3.1.0-alpha-runpod-auto"')

# ---------------------------------------------------------------------------
# Android Keystore-backed secret storage. Endpoint/pod id may be plain prefs;
# RunPod API key and worker bearer token are encrypted with per-install AES-GCM.
# ---------------------------------------------------------------------------
(JAVA / 'TurboSecureStore.java').write_text(r'''package com.pv.androidfacefusion;

import android.content.Context;
import android.content.SharedPreferences;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;

import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

final class TurboSecureStore {
    private static final String ALIAS = "FaceSwapProTurboSecretsV1";
    private static final String PREFS = "faceswappro_turbo_secure";

    private TurboSecureStore() {}

    private static SecretKey key() throws Exception {
        KeyStore ks = KeyStore.getInstance("AndroidKeyStore");
        ks.load(null);
        if (ks.containsAlias(ALIAS)) {
            return ((KeyStore.SecretKeyEntry) ks.getEntry(ALIAS, null)).getSecretKey();
        }
        KeyGenerator gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
        gen.init(new KeyGenParameterSpec.Builder(ALIAS,
            KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setRandomizedEncryptionRequired(true)
            .build());
        return gen.generateKey();
    }

    static void put(Context context, String name, String value) {
        try {
            if (value == null || value.isEmpty()) {
                context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(name).apply();
                return;
            }
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key());
            byte[] iv = cipher.getIV();
            byte[] encrypted = cipher.doFinal(value.getBytes(StandardCharsets.UTF_8));
            ByteBuffer packed = ByteBuffer.allocate(4 + iv.length + encrypted.length);
            packed.putInt(iv.length).put(iv).put(encrypted);
            String b64 = Base64.encodeToString(packed.array(), Base64.NO_WRAP);
            context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(name, b64).apply();
        } catch (Exception e) {
            throw new IllegalStateException("Secure storage failed", e);
        }
    }

    static String get(Context context, String name) {
        String b64 = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(name, "");
        if (b64 == null || b64.isEmpty()) return "";
        try {
            ByteBuffer packed = ByteBuffer.wrap(Base64.decode(b64, Base64.NO_WRAP));
            int ivLen = packed.getInt();
            if (ivLen < 12 || ivLen > 32 || ivLen > packed.remaining()) return "";
            byte[] iv = new byte[ivLen];
            packed.get(iv);
            byte[] encrypted = new byte[packed.remaining()];
            packed.get(encrypted);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, iv));
            return new String(cipher.doFinal(encrypted), StandardCharsets.UTF_8);
        } catch (Exception e) {
            return "";
        }
    }

    static void remove(Context context, String name) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove(name).apply();
    }
}
''', encoding='utf-8')

# ---------------------------------------------------------------------------
# RunPod REST control plane. We intentionally use a normal Pod instead of the
# Serverless /run payload because face-swap videos commonly exceed 10 MB.
# Public official FaceFusion TensorRT image means no private registry setup.
# ---------------------------------------------------------------------------
(JAVA / 'RunPodProvisioner.java').write_text(r'''package com.pv.androidfacefusion;

import android.app.AlarmManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.SystemClock;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import android.util.Base64;

final class RunPodProvisioner {
    private static final String API = "https://rest.runpod.io/v1";
    private static final String PREFS = "faceswappro_runpod";
    private static final String KEY_API = "runpod_api_key";
    private static final String KEY_WORKER = "worker_token";
    private static final String KEY_POD = "pod_id";
    private static final String KEY_COST = "cost_hr";
    private static final String IMAGE = "facefusion/facefusion:3.8.3-tensorrt";
    private static final String SERVER_URL = "https://raw.githubusercontent.com/VHanma/FaceSwapPro/6089928d2a083c1b20385fe4b0eaa983332a6832/cloud_turbo/server.py";

    interface Callback { void onStatus(String text, int progress); }

    static final class Result {
        final String podId, endpoint, cost;
        Result(String podId, String endpoint, String cost) {
            this.podId = podId; this.endpoint = endpoint; this.cost = cost;
        }
    }

    private RunPodProvisioner() {}

    static boolean configured(Context c) {
        return !podId(c).isEmpty() && !TurboSecureStore.get(c, KEY_API).isEmpty()
            && !TurboSecureStore.get(c, KEY_WORKER).isEmpty();
    }

    static String podId(Context c) {
        return c.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_POD, "");
    }

    static String cost(Context c) {
        return c.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_COST, "");
    }

    static Result provision(Context context, String apiKey, Callback cb) throws Exception {
        apiKey = apiKey == null ? "" : apiKey.trim();
        if (apiKey.length() < 12) throw new Exception("RunPod API key looks incomplete");
        String existing = podId(context);
        if (!existing.isEmpty()) {
            TurboSecureStore.put(context, KEY_API, apiKey);
            ensureRunning(context, cb);
            return new Result(existing, endpoint(existing), cost(context));
        }

        if (cb != null) cb.onStatus("Creating private Turbo GPU…", 8);
        String workerToken = randomToken();
        String bootstrap = "set -e; mkdir -p /workspace/faceswappro-turbo /workspace/jobs /workspace/trt-cache /workspace/facefusion-assets; "
            + "rm -rf /facefusion/.assets; ln -s /workspace/facefusion-assets /facefusion/.assets; "
            + "python -m pip install -q --disable-pip-version-check fastapi==0.116.1 uvicorn==0.35.0 python-multipart==0.0.20; "
            + "curl -fsSL '" + SERVER_URL + "' -o /workspace/faceswappro-turbo/server.py; "
            + "cd /facefusion; python facefusion.py force-download --download-scope full || true; "
            + "exec python -m uvicorn server:app --app-dir /workspace/faceswappro-turbo --host 0.0.0.0 --port 8000 --workers 1";

        JSONObject body = new JSONObject();
        body.put("name", "FaceSwapPro Turbo");
        body.put("cloudType", "SECURE");
        body.put("computeType", "GPU");
        body.put("gpuCount", 1);
        body.put("gpuTypePriority", "availability");
        body.put("gpuTypeIds", new JSONArray()
            .put("NVIDIA GeForce RTX 4090")
            .put("NVIDIA GeForce RTX 4080 SUPER")
            .put("NVIDIA GeForce RTX 4080")
            .put("NVIDIA GeForce RTX 3090 Ti")
            .put("NVIDIA GeForce RTX 3090")
            .put("NVIDIA RTX A6000")
            .put("NVIDIA RTX A5000")
            .put("NVIDIA L4"));
        body.put("gpuTypePriority", "availability");
        body.put("imageName", IMAGE);
        body.put("containerDiskInGb", 30);
        body.put("volumeInGb", 40);
        body.put("volumeMountPath", "/workspace");
        body.put("volumeEncrypted", true);
        body.put("minVCPUPerGPU", 4);
        body.put("minRAMPerGPU", 16);
        body.put("supportPublicIp", true);
        body.put("interruptible", false);
        body.put("ports", new JSONArray().put("8000/http"));
        body.put("dockerEntrypoint", new JSONArray().put("bash").put("-lc"));
        body.put("dockerStartCmd", new JSONArray().put(bootstrap));
        JSONObject env = new JSONObject();
        env.put("TURBO_API_KEY", workerToken);
        env.put("FACEFUSION_DIR", "/facefusion");
        env.put("TURBO_WORK_DIR", "/workspace/jobs");
        env.put("TURBO_MAX_UPLOAD_MB", "1500");
        env.put("TURBO_JOB_WORKERS", "1");
        env.put("ORT_TENSORRT_ENGINE_CACHE_ENABLE", "1");
        env.put("ORT_TENSORRT_CACHE_PATH", "/workspace/trt-cache");
        body.put("env", env);

        JSONObject created = request("POST", "/pods", apiKey, body);
        String id = created.optString("id", "");
        if (id.isEmpty()) throw new Exception("RunPod did not return a Pod ID: " + created);
        String cost = created.opt("costPerHr") == null ? "" : String.valueOf(created.opt("costPerHr"));
        TurboSecureStore.put(context, KEY_API, apiKey);
        TurboSecureStore.put(context, KEY_WORKER, workerToken);
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
            .putString(KEY_POD, id).putString(KEY_COST, cost).apply();
        CloudTurboClient.save(context, endpoint(id), workerToken);
        waitForHealth(context, id, workerToken, cb);
        scheduleAutoStop(context, 20);
        return new Result(id, endpoint(id), cost);
    }

    static void ensureRunning(Context context, Callback cb) throws Exception {
        if (!configured(context)) return;
        String key = TurboSecureStore.get(context, KEY_API);
        String id = podId(context);
        JSONObject pod = request("GET", "/pods/" + id, key, null);
        String desired = pod.optString("desiredStatus", "");
        if (!"RUNNING".equals(desired)) {
            if (cb != null) cb.onStatus("Starting Turbo GPU…", 3);
            request("POST", "/pods/" + id + "/start", key, null);
        }
        String worker = TurboSecureStore.get(context, KEY_WORKER);
        CloudTurboClient.save(context, endpoint(id), worker);
        waitForHealth(context, id, worker, cb);
        cancelAutoStop(context);
    }

    static void stop(Context context, Callback cb) throws Exception {
        if (!configured(context)) return;
        if (cb != null) cb.onStatus("Stopping Turbo GPU…", 95);
        request("POST", "/pods/" + podId(context) + "/stop",
            TurboSecureStore.get(context, KEY_API), null);
        cancelAutoStop(context);
        if (cb != null) cb.onStatus("Turbo GPU stopped • billing reduced", 100);
    }

    static void forget(Context context) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().clear().apply();
        TurboSecureStore.remove(context, KEY_API);
        TurboSecureStore.remove(context, KEY_WORKER);
        CloudTurboClient.save(context, "", "");
        cancelAutoStop(context);
    }

    static void scheduleAutoStop(Context context, int minutes) {
        Intent i = new Intent(context, TurboAutoStopReceiver.class);
        PendingIntent pi = PendingIntent.getBroadcast(context, 94031, i,
            PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        AlarmManager am = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
        long at = SystemClock.elapsedRealtime() + Math.max(5, minutes) * 60_000L;
        am.setWindow(AlarmManager.ELAPSED_REALTIME_WAKEUP, at, 5 * 60_000L, pi);
    }

    static void cancelAutoStop(Context context) {
        Intent i = new Intent(context, TurboAutoStopReceiver.class);
        PendingIntent pi = PendingIntent.getBroadcast(context, 94031, i,
            PendingIntent.FLAG_NO_CREATE | PendingIntent.FLAG_IMMUTABLE);
        if (pi != null) {
            AlarmManager am = (AlarmManager) context.getSystemService(Context.ALARM_SERVICE);
            am.cancel(pi); pi.cancel();
        }
    }

    private static void waitForHealth(Context context, String id, String worker, Callback cb) throws Exception {
        String url = endpoint(id) + "/health";
        long deadline = System.currentTimeMillis() + 15L * 60L * 1000L;
        int attempt = 0;
        while (System.currentTimeMillis() < deadline) {
            attempt++;
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
                c.setRequestMethod("GET");
                c.setConnectTimeout(8_000); c.setReadTimeout(8_000);
                c.setRequestProperty("Authorization", "Bearer " + worker);
                int code = c.getResponseCode();
                if (code == 200) {
                    c.disconnect();
                    if (cb != null) cb.onStatus("Turbo GPU ready", 100);
                    return;
                }
                c.disconnect();
            } catch (Exception ignored) {}
            if (cb != null) cb.onStatus(attempt < 12 ? "Booting GPU container…" : "Downloading face models once…",
                Math.min(92, 15 + attempt));
            Thread.sleep(5_000L);
        }
        throw new Exception("Turbo GPU started but health check timed out");
    }

    private static JSONObject request(String method, String path, String key, JSONObject body) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(API + path).openConnection();
        c.setRequestMethod(method);
        c.setConnectTimeout(20_000); c.setReadTimeout(90_000);
        c.setRequestProperty("Authorization", "Bearer " + key);
        c.setRequestProperty("Accept", "application/json");
        if (body != null) {
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", "application/json");
            byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
            try (OutputStream out = c.getOutputStream()) { out.write(bytes); }
        }
        int code = c.getResponseCode();
        String text = read(code >= 400 ? c.getErrorStream() : c.getInputStream());
        c.disconnect();
        if (code < 200 || code >= 300) throw new Exception("RunPod HTTP " + code + ": " + text);
        if (text == null || text.trim().isEmpty()) return new JSONObject();
        return new JSONObject(text);
    }

    private static String read(InputStream in) throws Exception {
        if (in == null) return "";
        try (InputStream input = in; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
            byte[] b = new byte[32 * 1024]; int n;
            while ((n = input.read(b)) >= 0) if (n > 0) out.write(b, 0, n);
            return out.toString(StandardCharsets.UTF_8.name());
        }
    }

    private static String randomToken() {
        byte[] b = new byte[32]; new SecureRandom().nextBytes(b);
        return Base64.encodeToString(b, Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING);
    }

    private static String endpoint(String podId) {
        return "https://" + podId + "-8000.proxy.runpod.net";
    }
}
''', encoding='utf-8')

(JAVA / 'TurboAutoStopReceiver.java').write_text(r'''package com.pv.androidfacefusion;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

public class TurboAutoStopReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) {
        final PendingResult pending = goAsync();
        Context app = context.getApplicationContext();
        new Thread(() -> {
            try { RunPodProvisioner.stop(app, null); }
            catch (Throwable ignored) {}
            finally { pending.finish(); }
        }, "TurboAutoStop").start();
    }
}
''', encoding='utf-8')

# Encrypt the Cloud Turbo worker token instead of plain SharedPreferences.
client = JAVA / 'CloudTurboClient.java'
c = client.read_text(encoding='utf-8')
c = c.replace(
    'return new Config(p.getString(KEY_ENDPOINT, ""), p.getString(KEY_TOKEN, ""));',
    'return new Config(p.getString(KEY_ENDPOINT, ""), TurboSecureStore.get(context, KEY_TOKEN));', 1)
c = c.replace(
    'context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()\n            .putString(KEY_ENDPOINT, normalize(endpoint))\n            .putString(KEY_TOKEN, token == null ? "" : token.trim())\n            .apply();',
    'context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()\n            .putString(KEY_ENDPOINT, normalize(endpoint)).apply();\n        TurboSecureStore.put(context, KEY_TOKEN, token == null ? "" : token.trim());', 1)
process_anchor = '''        Config config = load(context);\n        if (!config.ready()) throw new Exception("Turbo GPU endpoint is not configured");'''
process_new = '''        if (RunPodProvisioner.configured(context)) {\n            RunPodProvisioner.ensureRunning(context, (text, pct) -> {\n                if (callback != null) callback.onStatus(text, Math.min(5, pct));\n            });\n        }\n        Config config = load(context);\n        if (!config.ready()) throw new Exception("Turbo GPU endpoint is not configured");'''
if process_anchor not in c: raise SystemExit('CloudTurboClient process anchor missing')
c = c.replace(process_anchor, process_new, 1)
client.write_text(c, encoding='utf-8')

# Video UI: automatic provisioning first, manual endpoint remains as Advanced.
activity = JAVA / 'VideoSwapActivity.java'
s = activity.read_text(encoding='utf-8')
s = s.replace('import android.widget.EditText;\n', 'import android.widget.EditText;\nimport android.text.InputType;\nimport android.content.Intent;\n', 1)
s = s.replace('    private Button modeTurbo, modeLocal, configureTurbo;\n',
              '    private Button modeTurbo, modeLocal, configureTurbo, stopTurbo;\n', 1)
s = s.replace('configureTurbo.setText("Configure Turbo GPU");\n        root.addView(configureTurbo, fullButtonLp());',
              'configureTurbo.setText("AUTO SETUP TURBO GPU");\n        root.addView(configureTurbo, fullButtonLp());\n        stopTurbo = new Button(this);\n        stopTurbo.setText("STOP GPU / SAVE MONEY");\n        root.addView(stopTurbo, fullButtonLp());', 1)
s = s.replace('configureTurbo.setOnClickListener(v -> showTurboConfig());',
              'configureTurbo.setOnClickListener(v -> showRunPodSetup());\n        stopTurbo.setOnClickListener(v -> stopTurboGpu());', 1)
s = s.replace('            showTurboConfig();\n            Toast.makeText(this, "Enter your GPU endpoint once, then tap Turbo Render", Toast.LENGTH_LONG).show();',
              '            showRunPodSetup();\n            Toast.makeText(this, "Set up Turbo once, then tap Render", Toast.LENGTH_LONG).show();', 1)
s = s.replace('                Uri saved = saveToMovies(temp);\n                runOnUiThread(() -> {',
              '                Uri saved = saveToMovies(temp);\n                RunPodProvisioner.scheduleAutoStop(this, 20);\n                runOnUiThread(() -> {', 1)

manual_anchor = '    private void showTurboConfig() {\n'
auto_methods = r'''    private void showRunPodSetup() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        int p = dp(16); box.setPadding(p, p, p, 0);
        EditText key = new EditText(this);
        key.setHint("RunPod API key");
        key.setSingleLine(true);
        key.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        box.addView(key);
        TextView note = new TextView(this);
        note.setText("One-time setup. FaceSwapPro creates a Secure Cloud NVIDIA Pod, encrypts the RunPod key with Android Keystore, installs the Turbo API automatically, and stops the GPU about 20 minutes after your last finished render. RunPod billing must already be enabled.");
        note.setPadding(0, dp(10), 0, 0);
        box.addView(note);
        AlertDialog dialog = new AlertDialog.Builder(this)
            .setTitle(RunPodProvisioner.configured(this) ? "Turbo GPU already linked" : "One-tap Turbo setup")
            .setView(box)
            .setPositiveButton("BUILD TURBO", null)
            .setNeutralButton("ADVANCED", (d, w) -> showTurboConfig())
            .setNegativeButton("Cancel", null)
            .create();
        dialog.setOnShowListener(x -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            String apiKey = key.getText().toString().trim();
            if (apiKey.isEmpty() && RunPodProvisioner.configured(this)) {
                dialog.dismiss();
                status.setText("Turbo already configured");
                return;
            }
            if (apiKey.length() < 12) {
                key.setError("Paste your RunPod API key");
                return;
            }
            dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(false);
            status.setText("Creating secure Turbo GPU…");
            progress.setProgress(2);
            executor.execute(() -> {
                try {
                    RunPodProvisioner.Result result = RunPodProvisioner.provision(this, apiKey,
                        (text, pct) -> runOnUiThread(() -> { status.setText(text); progress.setProgress(pct); }));
                    runOnUiThread(() -> {
                        dialog.dismiss(); progress.setProgress(100);
                        String price = result.cost == null || result.cost.isEmpty() ? "" : " • $" + result.cost + "/hr while running";
                        status.setText("Turbo linked" + price + " • auto-stop armed");
                        Toast.makeText(this, "Turbo GPU ready", Toast.LENGTH_LONG).show();
                    });
                } catch (Exception e) {
                    runOnUiThread(() -> {
                        dialog.getButton(AlertDialog.BUTTON_POSITIVE).setEnabled(true);
                        status.setText("Turbo setup failed: " + e.getMessage());
                        Toast.makeText(this, "Turbo setup failed", Toast.LENGTH_LONG).show();
                    });
                }
            });
        }));
        dialog.show();
    }

    private void stopTurboGpu() {
        if (!RunPodProvisioner.configured(this)) {
            Toast.makeText(this, "Turbo GPU is not linked yet", Toast.LENGTH_SHORT).show();
            return;
        }
        stopTurbo.setEnabled(false);
        executor.execute(() -> {
            try {
                RunPodProvisioner.stop(this, (text, pct) -> runOnUiThread(() -> {
                    status.setText(text); progress.setProgress(pct);
                }));
            } catch (Exception e) {
                runOnUiThread(() -> status.setText("GPU stop failed: " + e.getMessage()));
            } finally {
                runOnUiThread(() -> stopTurbo.setEnabled(true));
            }
        });
    }

''' + manual_anchor
if manual_anchor not in s: raise SystemExit('showTurboConfig anchor missing')
s = s.replace(manual_anchor, auto_methods, 1)
activity.write_text(s, encoding='utf-8')

# Register cost-saving auto-stop receiver.
manifest = ROOT / 'app/src/main/AndroidManifest.xml'
m = manifest.read_text(encoding='utf-8')
app_close = '    </application>'
receiver = '''        <receiver\n            android:name=".TurboAutoStopReceiver"\n            android:exported="false" />\n'''
if app_close not in m: raise SystemExit('manifest application close missing')
m = m.replace(app_close, receiver + app_close, 1)
manifest.write_text(m, encoding='utf-8')

checks = {
    gradle: ['versionCode = 30100', 'versionName = "3.1.0-alpha-runpod-auto"'],
    JAVA / 'TurboSecureStore.java': ['AndroidKeyStore', 'AES/GCM/NoPadding'],
    JAVA / 'RunPodProvisioner.java': ['rest.runpod.io/v1', 'facefusion/facefusion:3.8.3-tensorrt', 'volumeEncrypted', 'scheduleAutoStop'],
    JAVA / 'TurboAutoStopReceiver.java': ['goAsync()', 'RunPodProvisioner.stop'],
    client: ['RunPodProvisioner.ensureRunning', 'TurboSecureStore.get'],
    activity: ['AUTO SETUP TURBO GPU', 'BUILD TURBO', 'STOP GPU / SAVE MONEY'],
    manifest: ['.TurboAutoStopReceiver'],
}
for path, needles in checks.items():
    body = path.read_text(encoding='utf-8')
    for needle in needles:
        if needle not in body:
            raise SystemExit(f'Missing v3.1 guard in {path}: {needle}')

print('OMEGA_CLOUD_TURBO_V31_RUNPOD_OK')
print('setup=RunPod API key -> secure Pod create -> bootstrap -> health -> auto resume/stop')
