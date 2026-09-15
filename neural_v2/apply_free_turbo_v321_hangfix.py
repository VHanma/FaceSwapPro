#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path('neural-upstream').resolve()
java = root / 'app/src/main/java/com/pv/androidfacefusion'
client = java / 'FreeTurboClient.java'
s = client.read_text(encoding='utf-8')

# v3.2.1 build identity.
gradle = root / 'app/build.gradle.kts'
g = gradle.read_text(encoding='utf-8')
g = g.replace('versionCode = 30200', 'versionCode = 30201', 1)
g = g.replace('versionName = "3.2.0-free-zerogpu"', 'versionName = "3.2.1-free-zerogpu-hangfix"', 1)
gradle.write_text(g, encoding='utf-8')

# Android media metadata is used for automatic video quality selection.
anchor = 'import android.graphics.Bitmap;\nimport android.net.Uri;\n'
if anchor not in s:
    raise SystemExit('FreeTurboClient import anchor missing')
s = s.replace(anchor, 'import android.graphics.Bitmap;\nimport android.media.MediaMetadataRetriever;\nimport android.net.Uri;\n', 1)

# Wake sleeping Spaces explicitly before upload and choose a duration-aware profile.
anchor = '''        String endpoint = HfZeroGpuProvisioner.endpoint(context);\n        if (cb != null) cb.onStatus("Uploading to your Free Turbo worker…", 5);\n\n        File sourceFile'''
replacement = '''        String endpoint = HfZeroGpuProvisioner.endpoint(context);\n        if (cb != null) cb.onStatus("Waking FREE Turbo…", 2);\n        waitForEndpoint(endpoint, token, cb);\n        String effectiveQuality = smartQuality(context, videoUri);\n        if (cb != null) cb.onStatus("Smart profile • " + effectiveQuality, 4);\n        if (cb != null) cb.onStatus("Uploading to your Free Turbo worker…", 5);\n\n        File sourceFile'''
if anchor not in s:
    raise SystemExit('FreeTurboClient wake anchor missing')
s = s.replace(anchor, replacement, 1)

anchor = 'new JSONArray().put(src).put(vid).put(secret).put(quality == null ? "Ultra 512" : quality);'
if anchor not in s:
    raise SystemExit('FreeTurboClient quality anchor missing')
s = s.replace(anchor, 'new JSONArray().put(src).put(vid).put(secret).put(effectiveQuality);', 1)

# The old implementation could stay alive forever because Gradio heartbeats reset
# the socket read timeout. Add a real wall-clock deadline and useful heartbeat text.
old = '''    private static String pollSse(String endpoint, String token, String event, Callback cb) throws Exception {\n        String[] paths = {"/gradio_api/call/swap_video/" + event, "/gradio_api/call/v2/swap_video/" + event};\n        Exception last = null;\n        for (String path : paths) {\n            try {\n                HttpURLConnection c = (HttpURLConnection) new URL(endpoint + path).openConnection();\n                c.setRequestProperty("Authorization", "Bearer " + token);\n                c.setRequestProperty("Accept", "text/event-stream");\n                c.setConnectTimeout(20_000); c.setReadTimeout(360_000);\n                int code = c.getResponseCode();\n                if (code < 200 || code >= 300) {\n                    String body = read(c.getErrorStream()); c.disconnect();\n                    throw new Exception("ZeroGPU queue HTTP " + code + ": " + body);\n                }\n                try (BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream(), StandardCharsets.UTF_8))) {\n                    String line, eventType = "";\n                    while ((line = r.readLine()) != null) {\n                        if (line.startsWith("event:")) eventType = line.substring(6).trim();\n                        else if (line.startsWith("data:")) {\n                            String d = line.substring(5).trim();\n                            if ("error".equals(eventType)) throw new Exception("ZeroGPU error: " + d);\n                            if ("complete".equals(eventType)) {\n                                c.disconnect(); return extractResultUrl(d);\n                            }\n                            if (cb != null && ("generating".equals(eventType) || "progress".equals(eventType)))\n                                cb.onStatus("FREE GPU rendering…", 65);\n                        }\n                    }\n                } finally { c.disconnect(); }\n            } catch (Exception e) { last = e; }\n        }\n        throw last == null ? new Exception("Free Turbo queue ended unexpectedly") : last;\n    }\n'''
new = '''    private static String pollSse(String endpoint, String token, String event, Callback cb) throws Exception {\n        final long started = System.currentTimeMillis();\n        final long hardDeadline = started + 7L * 60L * 1000L;\n        String[] paths = {"/gradio_api/call/swap_video/" + event, "/gradio_api/call/v2/swap_video/" + event};\n        Exception last = null;\n        for (String path : paths) {\n            if (System.currentTimeMillis() >= hardDeadline) break;\n            try {\n                HttpURLConnection c = (HttpURLConnection) new URL(endpoint + path).openConnection();\n                c.setRequestProperty("Authorization", "Bearer " + token);\n                c.setRequestProperty("Accept", "text/event-stream");\n                c.setConnectTimeout(20_000); c.setReadTimeout(45_000);\n                int code = c.getResponseCode();\n                if (code < 200 || code >= 300) {\n                    String body = read(c.getErrorStream()); c.disconnect();\n                    throw new Exception("ZeroGPU queue HTTP " + code + ": " + body);\n                }\n                try (BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream(), StandardCharsets.UTF_8))) {\n                    String line, eventType = "";\n                    while ((line = r.readLine()) != null) {\n                        long now = System.currentTimeMillis();\n                        if (now >= hardDeadline) {\n                            throw new Exception("Free Turbo timed out after 7 minutes. The free GPU queue or daily quota is likely busy/exhausted. Try again later or use Local Private.");\n                        }\n                        if (line.startsWith("event:")) {\n                            eventType = line.substring(6).trim();\n                            if ("heartbeat".equals(eventType) && cb != null) {\n                                long sec = Math.max(1L, (now - started) / 1000L);\n                                int pct = (int)Math.min(88L, 30L + sec / 8L);\n                                cb.onStatus(sec < 45 ? "Waiting for FREE GPU…" : "FREE GPU working • " + sec + "s", pct);\n                            }\n                        } else if (line.startsWith("data:")) {\n                            String d = line.substring(5).trim();\n                            if ("error".equals(eventType)) {\n                                throw new Exception("ZeroGPU render error: " + d);\n                            }\n                            if ("complete".equals(eventType)) {\n                                c.disconnect(); return extractResultUrl(d);\n                            }\n                            if (cb != null && ("generating".equals(eventType) || "progress".equals(eventType))) {\n                                cb.onStatus("FREE GPU rendering…", 65);\n                            }\n                        }\n                    }\n                } finally { c.disconnect(); }\n            } catch (java.net.SocketTimeoutException e) {\n                last = e;\n                if (cb != null) cb.onStatus("Reconnecting to FREE GPU job…", 58);\n            } catch (Exception e) {\n                last = e;\n                if (e.getMessage() != null && (e.getMessage().contains("ZeroGPU render error") || e.getMessage().contains("timed out after 7 minutes"))) throw e;\n            }\n        }\n        if (System.currentTimeMillis() >= hardDeadline) {\n            throw new Exception("Free Turbo timed out after 7 minutes. Use Local Private or try again after the ZeroGPU quota resets.");\n        }\n        throw last == null ? new Exception("Free Turbo queue ended unexpectedly") : last;\n    }\n'''
if old not in s:
    raise SystemExit('pollSse anchor missing')
s = s.replace(old, new, 1)

# Add robust wake/preflight and automatic quality selection before uploadFiles().
anchor = '    private static JSONArray uploadFiles(Context context, String endpoint, String token, File source,\n'
helpers = r'''    private static void waitForEndpoint(String endpoint, String token, Callback cb) throws Exception {
        long deadline = System.currentTimeMillis() + 150_000L;
        int attempt = 0;
        String last = "";
        while (System.currentTimeMillis() < deadline) {
            attempt++;
            HttpURLConnection c = null;
            try {
                c = (HttpURLConnection) new URL(endpoint + "/gradio_api/info").openConnection();
                c.setRequestProperty("Authorization", "Bearer " + token);
                c.setConnectTimeout(10_000); c.setReadTimeout(15_000);
                int code = c.getResponseCode();
                if (code == 200) {
                    c.disconnect();
                    if (cb != null) cb.onStatus("FREE Turbo awake", 3);
                    return;
                }
                last = "HTTP " + code;
            } catch (Exception e) {
                last = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
            } finally {
                if (c != null) c.disconnect();
            }
            if (cb != null) cb.onStatus(attempt < 5 ? "Waking FREE Turbo…" : "Free worker is still starting…", 2);
            Thread.sleep(4_000L);
        }
        throw new Exception("Free Turbo did not wake up. Tap SET UP FREE TURBO → CHECK / REPAIR, then try again. " + last);
    }

    private static String smartQuality(Context context, Uri videoUri) {
        MediaMetadataRetriever mmr = new MediaMetadataRetriever();
        try {
            mmr.setDataSource(context, videoUri);
            String d = mmr.extractMetadata(MediaMetadataRetriever.METADATA_KEY_DURATION);
            long ms = d == null ? 0L : Long.parseLong(d);
            // 512 is excellent for short clips. Longer video defaults to 224 so a
            // free-account render has a realistic chance of finishing inside the
            // ZeroGPU allowance instead of sitting in the queue until timeout.
            return ms > 0L && ms <= 8_000L ? "Ultra 512" : "Fast 224";
        } catch (Exception ignored) {
            return "Fast 224";
        } finally {
            try { mmr.release(); } catch (Exception ignored) {}
        }
    }

''' + anchor
if anchor not in s:
    raise SystemExit('uploadFiles insertion anchor missing')
s = s.replace(anchor, helpers, 1)

client.write_text(s, encoding='utf-8')

# Strengthen the embedded worker for modern PyTorch and shorten the hard worker
# timeout slightly below the ZeroGPU lease so errors make it back to Android.
asset = root / 'app/src/main/assets/free_turbo/app.py'
w = asset.read_text(encoding='utf-8')
w = w.replace("_patch(SIMSWAP / 'test_video_swapsingle.py', 'app.prepare(ctx_id= 0', 'app.prepare(ctx_id= -1')",
              "_patch(SIMSWAP / 'test_video_swapsingle.py', 'app.prepare(ctx_id= 0', 'app.prepare(ctx_id= -1')\n        _patch(SIMSWAP / 'models/base_model.py', 'torch.load(save_path)', 'torch.load(save_path, weights_only=True)')", 1)
w = w.replace('@spaces.GPU(duration=300)', '@spaces.GPU(duration=285)', 1)
w = w.replace('timeout=290,', 'timeout=275,', 1)
asset.write_text(w, encoding='utf-8')

checks = {
    gradle: ['versionCode = 30201', '3.2.1-free-zerogpu-hangfix'],
    client: ['waitForEndpoint', 'smartQuality', 'timed out after 7 minutes', 'Reconnecting to FREE GPU job'],
    asset: ['@spaces.GPU(duration=285)', 'torch.load(save_path, weights_only=True)', 'timeout=275'],
}
for path, needles in checks.items():
    body = path.read_text(encoding='utf-8')
    for needle in needles:
        if needle not in body:
            raise SystemExit(f'v3.2.1 guard missing in {path}: {needle}')

print('FREE_TURBO_V321_HANGFIX_OK')
print('fixes=Space wake preflight + smart video profile + absolute SSE deadline + heartbeat status + reconnect + modern torch load')
