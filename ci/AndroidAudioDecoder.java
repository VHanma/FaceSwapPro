package com.vaanhanma.omniscribe.engine;

import android.content.Context;
import android.media.AudioFormat;
import android.media.MediaCodec;
import android.media.MediaExtractor;
import android.media.MediaFormat;

import java.io.*;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.function.BooleanSupplier;

public final class AndroidAudioDecoder {
    private static final long TIMEOUT_US = 20_000;
    private final Context context;

    public AndroidAudioDecoder(Context context) {
        this.context = context.getApplicationContext();
    }

    public File toPcm16k(File input, File output, BooleanSupplier canceled) throws Exception {
        if (input == null || !input.isFile() || input.length() <= 0) throw new FileNotFoundException("Input audio is missing");
        File parent = output.getParentFile();
        if (parent != null && !parent.exists() && !parent.mkdirs()) throw new IOException("Cannot create PCM folder");
        File tmp = new File(output.getAbsolutePath() + ".android.tmp");
        tmp.delete();

        MediaExtractor extractor = new MediaExtractor();
        MediaCodec codec = null;
        try {
            extractor.setDataSource(input.getAbsolutePath());
            int track = findAudioTrack(extractor);
            if (track < 0) throw new IOException("No decodable audio track found in " + input.getName());
            extractor.selectTrack(track);
            MediaFormat inputFormat = extractor.getTrackFormat(track);
            String mime = inputFormat.getString(MediaFormat.KEY_MIME);
            if (mime == null || !mime.startsWith("audio/")) throw new IOException("Unsupported audio MIME");

            codec = MediaCodec.createDecoderByType(mime);
            codec.configure(inputFormat, null, null, 0);
            codec.start();

            boolean inputDone = false;
            boolean outputDone = false;
            MediaCodec.BufferInfo info = new MediaCodec.BufferInfo();
            OutputState state = null;

            try (BufferedOutputStream os = new BufferedOutputStream(new FileOutputStream(tmp), 256 * 1024)) {
                PcmWriter writer = new PcmWriter(os);
                while (!outputDone) {
                    checkCanceled(canceled);
                    if (!inputDone) {
                        int inIndex = codec.dequeueInputBuffer(TIMEOUT_US);
                        if (inIndex >= 0) {
                            ByteBuffer in = codec.getInputBuffer(inIndex);
                            if (in == null) throw new IOException("Decoder input buffer unavailable");
                            in.clear();
                            int size = extractor.readSampleData(in, 0);
                            if (size < 0) {
                                codec.queueInputBuffer(inIndex, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM);
                                inputDone = true;
                            } else {
                                long pts = Math.max(0, extractor.getSampleTime());
                                codec.queueInputBuffer(inIndex, 0, size, pts, 0);
                                extractor.advance();
                            }
                        }
                    }

                    int outIndex = codec.dequeueOutputBuffer(info, TIMEOUT_US);
                    if (outIndex == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED) {
                        state = OutputState.from(codec.getOutputFormat(), writer);
                    } else if (outIndex >= 0) {
                        if ((info.flags & MediaCodec.BUFFER_FLAG_CODEC_CONFIG) == 0 && info.size > 0) {
                            if (state == null) state = OutputState.from(codec.getOutputFormat(), writer);
                            ByteBuffer out = codec.getOutputBuffer(outIndex);
                            if (out == null) throw new IOException("Decoder output buffer unavailable");
                            ByteBuffer view = out.duplicate().order(ByteOrder.LITTLE_ENDIAN);
                            view.position(info.offset);
                            view.limit(info.offset + info.size);
                            state.consume(view.slice().order(ByteOrder.LITTLE_ENDIAN));
                        }
                        outputDone = (info.flags & MediaCodec.BUFFER_FLAG_END_OF_STREAM) != 0;
                        codec.releaseOutputBuffer(outIndex, false);
                    }
                }
                writer.finish();
            }

            if (!tmp.isFile() || tmp.length() < 2) throw new IOException("Android decoder produced no PCM audio");
            if (output.exists() && !output.delete()) throw new IOException("Could not replace old PCM file");
            if (!tmp.renameTo(output)) copyReplace(tmp, output);
            return output;
        } catch (Throwable t) {
            tmp.delete();
            if (t instanceof InterruptedException) throw (InterruptedException) t;
            if (t instanceof Exception) throw (Exception) t;
            throw new IOException(t);
        } finally {
            try { if (codec != null) codec.stop(); } catch (Throwable ignored) {}
            try { if (codec != null) codec.release(); } catch (Throwable ignored) {}
            try { extractor.release(); } catch (Throwable ignored) {}
        }
    }

    private int findAudioTrack(MediaExtractor extractor) {
        for (int i = 0; i < extractor.getTrackCount(); i++) {
            MediaFormat f = extractor.getTrackFormat(i);
            String mime = f.getString(MediaFormat.KEY_MIME);
            if (mime != null && mime.startsWith("audio/")) return i;
        }
        return -1;
    }

    private void checkCanceled(BooleanSupplier canceled) throws InterruptedException {
        if (Thread.currentThread().isInterrupted() || (canceled != null && canceled.getAsBoolean())) throw new InterruptedException("Audio decode canceled");
    }

    private static void copyReplace(File src, File dst) throws IOException {
        try (InputStream in = new BufferedInputStream(new FileInputStream(src)); OutputStream out = new BufferedOutputStream(new FileOutputStream(dst))) {
            byte[] b = new byte[256 * 1024]; int n;
            while ((n = in.read(b)) > 0) out.write(b, 0, n);
        }
        src.delete();
    }

    private static final class OutputState {
        final int sampleRate;
        final int channels;
        final int encoding;
        final Resampler resampler;

        OutputState(int sampleRate, int channels, int encoding, PcmWriter writer) throws IOException {
            if (sampleRate <= 0 || channels <= 0) throw new IOException("Invalid decoder PCM format");
            this.sampleRate = sampleRate;
            this.channels = channels;
            this.encoding = encoding;
            this.resampler = new Resampler(sampleRate, writer);
        }

        static OutputState from(MediaFormat f, PcmWriter writer) throws IOException {
            int sr = f.containsKey(MediaFormat.KEY_SAMPLE_RATE) ? f.getInteger(MediaFormat.KEY_SAMPLE_RATE) : 48000;
            int ch = f.containsKey(MediaFormat.KEY_CHANNEL_COUNT) ? f.getInteger(MediaFormat.KEY_CHANNEL_COUNT) : 2;
            int enc = f.containsKey(MediaFormat.KEY_PCM_ENCODING) ? f.getInteger(MediaFormat.KEY_PCM_ENCODING) : AudioFormat.ENCODING_PCM_16BIT;
            return new OutputState(sr, ch, enc, writer);
        }

        void consume(ByteBuffer b) throws IOException {
            int bytesPerSample = bytesPerSample(encoding);
            int frameBytes = bytesPerSample * channels;
            int frames = b.remaining() / frameBytes;
            for (int frame = 0; frame < frames; frame++) {
                float mono = 0f;
                for (int c = 0; c < channels; c++) mono += readSample(b, encoding);
                mono /= channels;
                resampler.accept(clamp(mono));
            }
        }

        private static int bytesPerSample(int enc) throws IOException {
            if (enc == AudioFormat.ENCODING_PCM_8BIT) return 1;
            if (enc == AudioFormat.ENCODING_PCM_16BIT) return 2;
            if (enc == AudioFormat.ENCODING_PCM_FLOAT) return 4;
            if (enc == AudioFormat.ENCODING_PCM_24BIT_PACKED) return 3;
            if (enc == AudioFormat.ENCODING_PCM_32BIT) return 4;
            throw new IOException("Unsupported decoder PCM encoding: " + enc);
        }

        private static float readSample(ByteBuffer b, int enc) throws IOException {
            if (enc == AudioFormat.ENCODING_PCM_8BIT) return ((b.get() & 0xff) - 128) / 128f;
            if (enc == AudioFormat.ENCODING_PCM_16BIT) return b.getShort() / 32768f;
            if (enc == AudioFormat.ENCODING_PCM_FLOAT) return b.getFloat();
            if (enc == AudioFormat.ENCODING_PCM_24BIT_PACKED) {
                int x = (b.get() & 0xff) | ((b.get() & 0xff) << 8) | ((b.get() & 0xff) << 16);
                if ((x & 0x800000) != 0) x |= 0xff000000;
                return x / 8388608f;
            }
            if (enc == AudioFormat.ENCODING_PCM_32BIT) return b.getInt() / 2147483648f;
            throw new IOException("Unsupported decoder PCM encoding: " + enc);
        }

        private static float clamp(float v) { return v < -1f ? -1f : Math.min(1f, v); }
    }

    private static final class Resampler {
        private final double step;
        private final PcmWriter writer;
        private long inputIndex = 0;
        private double nextOutputPosition = 0.0;
        private float previous = 0f;

        Resampler(int sourceRate, PcmWriter writer) {
            this.step = sourceRate / 16000.0;
            this.writer = writer;
        }

        void accept(float sample) throws IOException {
            if (inputIndex == 0) {
                previous = sample;
                writer.write(sample);
                nextOutputPosition = step;
                inputIndex = 1;
                return;
            }
            while (nextOutputPosition <= inputIndex + 1e-9) {
                double fraction = nextOutputPosition - (inputIndex - 1);
                float out = previous + (float) fraction * (sample - previous);
                writer.write(out);
                nextOutputPosition += step;
            }
            previous = sample;
            inputIndex++;
        }
    }

    private static final class PcmWriter {
        private final OutputStream out;
        private final byte[] buffer = new byte[64 * 1024];
        private int used = 0;

        PcmWriter(OutputStream out) { this.out = out; }

        void write(float value) throws IOException {
            float v = value < -1f ? -1f : Math.min(1f, value);
            int q = Math.round(v * 32767f);
            if (q < -32768) q = -32768;
            if (q > 32767) q = 32767;
            if (used + 2 > buffer.length) flush();
            buffer[used++] = (byte) (q & 0xff);
            buffer[used++] = (byte) ((q >>> 8) & 0xff);
        }

        void finish() throws IOException { flush(); out.flush(); }
        private void flush() throws IOException { if (used > 0) { out.write(buffer, 0, used); used = 0; } }
    }
}
