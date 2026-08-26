package com.vaanhanma.omniscribe.engine;

import android.content.Context;
import java.io.*;
import java.util.*;
import java.util.concurrent.ConcurrentHashMap;

public class FfmpegRunner {
    private final Context c;
    private final Set<Process> active = ConcurrentHashMap.newKeySet();

    public FfmpegRunner(Context context) { c = context.getApplicationContext(); }
    private String ffmpeg() { return new File(c.getApplicationInfo().nativeLibraryDir, "libffmpeg.so").getAbsolutePath(); }

    private File packagesRoot() { return new File(c.getNoBackupFilesDir(), "youtubedl-android/packages"); }
    private File ffmpegLibDir() { return new File(packagesRoot(), "ffmpeg/usr/lib"); }
    private File pythonLibDir() { return new File(packagesRoot(), "python/usr/lib"); }

    /**
     * youtubedl-android 0.18.1's FFmpeg package can contain C++ libraries such as
     * librubberband.so without carrying libc++_shared.so in the FFmpeg archive itself.
     * The matching Termux runtime is present in the Python package. Put a private copy
     * beside FFmpeg's libraries when needed, then also expose every valid lookup path.
     */
    private void ensureFfmpegCppRuntime(File ffmpegLib, File pythonLib) {
        try {
            File dst = new File(ffmpegLib, "libc++_shared.so");
            if (dst.isFile() && dst.length() > 0) return;
            File src = new File(pythonLib, "libc++_shared.so");
            if (!src.isFile() || src.length() <= 0) return;
            if (!ffmpegLib.exists()) ffmpegLib.mkdirs();
            File tmp = new File(ffmpegLib, "libc++_shared.so.tmp");
            try (InputStream in = new BufferedInputStream(new FileInputStream(src));
                 OutputStream out = new BufferedOutputStream(new FileOutputStream(tmp))) {
                byte[] buf = new byte[256 * 1024];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            }
            if (!tmp.renameTo(dst)) {
                try (InputStream in = new BufferedInputStream(new FileInputStream(tmp));
                     OutputStream out = new BufferedOutputStream(new FileOutputStream(dst))) {
                    byte[] buf = new byte[256 * 1024];
                    int n;
                    while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
                }
                tmp.delete();
            }
            dst.setReadable(true, true);
        } catch (Throwable ignored) {
            // The packaged APK runtime and expanded LD_LIBRARY_PATH remain fallbacks.
        }
    }

    private int run(List<String> args) throws Exception {
        ArrayList<String> cmd = new ArrayList<>(); cmd.add(ffmpeg()); cmd.addAll(args);
        ProcessBuilder pb = new ProcessBuilder(cmd); pb.redirectErrorStream(true);

        File ffmpegLib = ffmpegLibDir();
        File pythonLib = pythonLibDir();
        ensureFfmpegCppRuntime(ffmpegLib, pythonLib);

        // Search the FFmpeg dependency set first, then the matching Termux C++ runtime
        // shipped with Python, then the APK's packaged native libraries.
        String nativeLib = c.getApplicationInfo().nativeLibraryDir;
        pb.environment().put("LD_LIBRARY_PATH",
                ffmpegLib.getAbsolutePath() + ":" + pythonLib.getAbsolutePath() + ":" + nativeLib);

        Process p = pb.start(); active.add(p); StringBuilder out = new StringBuilder();
        try (BufferedReader br = new BufferedReader(new InputStreamReader(p.getInputStream()))) {
            String line;
            while ((line = br.readLine()) != null) {
                if (Thread.currentThread().isInterrupted()) { destroy(p); throw new InterruptedException("FFmpeg canceled"); }
                if (out.length() < 256_000) out.append(line).append('\n');
            }
            int code = p.waitFor();
            if (Thread.currentThread().isInterrupted()) throw new InterruptedException("FFmpeg canceled");
            if (code != 0) throw new IOException("FFmpeg failed: " + out);
            return code;
        } finally { active.remove(p); if (p.isAlive()) destroy(p); }
    }

    private void destroy(Process p) {
        try { p.destroy(); } catch (Throwable ignored) {}
        try { if (p.isAlive()) p.destroyForcibly(); } catch (Throwable ignored) {}
    }

    public void cancelAll() { for (Process p : new ArrayList<>(active)) destroy(p); }

    public File toPcm16k(File input, File output) throws Exception {
        run(Arrays.asList("-hide_banner", "-loglevel", "error", "-y", "-i", input.getAbsolutePath(), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", output.getAbsolutePath()));
        return output;
    }

    public File convertAudio(File input, File output, String fmt, String title, String collection, File artwork) throws Exception {
        if (artwork != null && artwork.isFile() && supportsArtwork(fmt)) {
            try { return convert(input, output, fmt, title, collection, artwork); }
            catch (Exception artFailure) { if (artFailure instanceof InterruptedException) throw artFailure; output.delete(); }
        }
        return convert(input, output, fmt, title, collection, null);
    }

    private File convert(File input, File output, String fmt, String title, String collection, File artwork) throws Exception {
        ArrayList<String> a = new ArrayList<>(Arrays.asList("-hide_banner", "-loglevel", "error", "-y", "-i", input.getAbsolutePath()));
        if (artwork != null) { a.add("-i"); a.add(artwork.getAbsolutePath()); a.addAll(Arrays.asList("-map", "0:a:0", "-map", "1:v:0")); }
        else a.add("-vn");

        switch (fmt.toLowerCase(Locale.US)) {
            case "mp3": a.addAll(Arrays.asList("-c:a", "libmp3lame", "-q:a", "2")); break;
            case "m4a": a.addAll(Arrays.asList("-c:a", "aac", "-b:a", "192k")); break;
            case "opus": a.addAll(Arrays.asList("-c:a", "libopus", "-b:a", "160k")); break;
            case "flac": a.addAll(Arrays.asList("-c:a", "flac")); break;
            case "wav": a.addAll(Arrays.asList("-c:a", "pcm_s16le")); break;
            default: a.addAll(Arrays.asList("-c:a", "copy"));
        }
        if (artwork != null) {
            a.addAll(Arrays.asList("-c:v", "mjpeg", "-disposition:v:0", "attached_pic"));
            if ("mp3".equalsIgnoreCase(fmt)) a.addAll(Arrays.asList("-id3v2_version", "3"));
        }
        if (title != null) { a.add("-metadata"); a.add("title=" + title); }
        if (collection != null) { a.add("-metadata"); a.add("album=" + collection); }
        a.add(output.getAbsolutePath()); run(a); return output;
    }

    private boolean supportsArtwork(String fmt) {
        String x = fmt == null ? "" : fmt.toLowerCase(Locale.US);
        return x.equals("mp3") || x.equals("m4a") || x.equals("flac");
    }
}
