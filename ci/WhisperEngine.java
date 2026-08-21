package com.vaanhanma.omniscribe.engine;

import org.json.*;
import java.io.*;
import java.util.*;
import java.util.concurrent.atomic.AtomicBoolean;

public class WhisperEngine implements Closeable {
    static { System.loadLibrary("omniscribe_whisper"); }
    private volatile long ctx = 0; private String loadedModel = null;
    private final AtomicBoolean cancelRequested = new AtomicBoolean(false);
    private native long nativeInit(String modelPath);
    private native String nativeTranscribe(long context, float[] samples, int threads, String language);
    private native void nativeCancel(long context);
    private native void nativeFree(long context);

    private synchronized void ensure(String model) throws Exception {
        if (ctx != 0 && model.equals(loadedModel)) return;
        close(); ctx = nativeInit(model); if (ctx == 0) throw new IOException("Whisper model failed to load"); loadedModel = model;
    }

    public interface Progress { void onProgress(float pct); }

    public synchronized List<TranscriptSegment> transcribePcm(File pcm, String model, String language, Progress progress) throws Exception {
        ensure(model); cancelRequested.set(false);
        ArrayList<TranscriptSegment> all = new ArrayList<>();
        final int sampleRate = 16000;
        final long coreSamples = sampleRate * 300L;
        final long overlapSamples = sampleRate * 2L;
        final long totalSamples = pcm.length() / 2L;
        final int threads = Math.max(1, Math.min(6, Runtime.getRuntime().availableProcessors() - 1));

        try (RandomAccessFile raf = new RandomAccessFile(pcm, "r")) {
            for (long coreStart = 0; coreStart < totalSamples; coreStart += coreSamples) {
                if (Thread.currentThread().isInterrupted() || cancelRequested.get()) throw new InterruptedException("Transcription canceled");
                long coreEnd = Math.min(totalSamples, coreStart + coreSamples);
                long readStart = Math.max(0, coreStart - overlapSamples);
                long readEnd = Math.min(totalSamples, coreEnd + overlapSamples);
                int count = (int) Math.min(Integer.MAX_VALUE / 2, readEnd - readStart);
                byte[] bytes = new byte[count * 2];
                raf.seek(readStart * 2L); raf.readFully(bytes);
                float[] samples = new float[count];
                for (int i = 0, j = 0; i < count; i++, j += 2) {
                    short s = (short) ((bytes[j] & 0xff) | (bytes[j + 1] << 8)); samples[i] = s / 32768f;
                }
                String json = nativeTranscribe(ctx, samples, threads, language == null ? "auto" : language);
                if (Thread.currentThread().isInterrupted() || cancelRequested.get()) throw new InterruptedException("Transcription canceled");
                long offsetMs = readStart * 1000L / sampleRate;
                long acceptStartMs = coreStart * 1000L / sampleRate;
                long acceptEndMs = coreEnd * 1000L / sampleRate;
                JSONArray arr = new JSONArray(json);
                for (int i = 0; i < arr.length(); i++) {
                    JSONObject o = arr.getJSONObject(i);
                    long start = offsetMs + o.getLong("start");
                    long end = offsetMs + o.getLong("end");
                    long midpoint = start + Math.max(0, end - start) / 2L;
                    if (midpoint < acceptStartMs || (coreEnd < totalSamples && midpoint >= acceptEndMs)) continue;
                    String text = o.getString("text").trim();
                    if (!text.isEmpty()) all.add(new TranscriptSegment(Math.max(0, start), Math.max(start, end), text));
                }
                if (progress != null) progress.onProgress(totalSamples == 0 ? 1f : Math.min(1f, coreEnd / (float) totalSamples));
            }
        }
        return all;
    }

    public void requestCancel() { cancelRequested.set(true); long c = ctx; if (c != 0) nativeCancel(c); }

    @Override public synchronized void close() {
        if (ctx != 0) { nativeFree(ctx); ctx = 0; loadedModel = null; }
    }
}
