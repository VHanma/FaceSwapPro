from pathlib import Path

p = Path('omniscribe/app/src/main/java/com/vaanhanma/omniscribe/service/BatchService.java')
s = p.read_text()

s = s.replace('private FfmpegRunner ff;', 'private FfmpegRunner ff;\n    private AndroidAudioDecoder androidDecoder;')
s = s.replace(
    'store = new JobStore(this); yt = new YtDlpEngine(this); ff = new FfmpegRunner(this);',
    'store = new JobStore(this); yt = new YtDlpEngine(this); ff = new FfmpegRunner(this); androidDecoder = new AndroidAudioDecoder(this);'
)

old = '''            if (j.wantsAudio()) {\n                changed(j, "Preparing audio", 48);\n                if ("original".equals(fmt)) {\n                    String ext = FilesUtil.extension(raw);\n                    FilesUtil.copy(raw, new File(dir, "final_audio." + ext));\n                } else {\n                    File converted = new File(dir, "final_audio." + fmt);\n                    ff.convertAudio(raw, converted, fmt, j.title, j.collection, yt.findArtwork(dir));\n                }\n                store.update(j.id, "PROCESSING", 57, null);\n            }\n\n            if (j.wantsTranscript()) {\n'''
new = '''            if (j.wantsTranscript()) {\n'''
if old not in s:
    raise SystemExit('audio-before-transcript block not found')
s = s.replace(old, new)

old = '''                File pcm = new File(dir, "speech.pcm");\n                changed(j, "Decoding speech", 63);\n                ff.toPcm16k(raw, pcm);\n                if (stop) throw new InterruptedException("Batch stopped");\n'''
new = '''                File pcm = new File(dir, "speech.pcm");\n                changed(j, "Decoding speech with Android", 63);\n                try {\n                    androidDecoder.toPcm16k(raw, pcm, () -> stop || Thread.currentThread().isInterrupted());\n                } catch (InterruptedException e) {\n                    throw e;\n                } catch (Exception androidFailure) {\n                    pcm.delete();\n                    changed(j, "Android decoder fallback", 63);\n                    try {\n                        ff.toPcm16k(raw, pcm);\n                    } catch (Exception ffFailure) {\n                        throw new IOException("Audio decode failed. Android: " + shortError(androidFailure) + " | FFmpeg: " + shortError(ffFailure), ffFailure);\n                    }\n                }\n                if (stop) throw new InterruptedException("Batch stopped");\n'''
if old not in s:
    raise SystemExit('ffmpeg decode block not found')
s = s.replace(old, new)

marker = '''                pcm.delete();\n            }\n\n            if (stop) throw new InterruptedException("Batch stopped");\n            store.setStage(j.id, dir.getAbsolutePath(), "EXPORTING", 95);\n'''
insert = '''                pcm.delete();\n            }\n\n            if (j.wantsAudio()) {\n                changed(j, "Preparing audio", j.wantsTranscript() ? 93 : 48);\n                if ("original".equals(fmt)) {\n                    String ext = FilesUtil.extension(raw);\n                    FilesUtil.copy(raw, new File(dir, "final_audio." + ext));\n                } else {\n                    File converted = new File(dir, "final_audio." + fmt);\n                    try {\n                        ff.convertAudio(raw, converted, fmt, j.title, j.collection, yt.findArtwork(dir));\n                    } catch (InterruptedException e) {\n                        throw e;\n                    } catch (Exception conversionFailure) {\n                        converted.delete();\n                        String ext = FilesUtil.extension(raw);\n                        FilesUtil.copy(raw, new File(dir, "final_audio." + ext));\n                        FilesUtil.writeUtf8(new File(dir, "audio_fallback.txt"),\n                                "Requested " + fmt.toUpperCase(Locale.US) + " conversion failed, so OmniScribe saved the original downloaded audio instead.\\n" + shortError(conversionFailure));\n                        changed(j, "Conversion unavailable • keeping original audio", 94);\n                    }\n                }\n                store.update(j.id, "PROCESSING", j.wantsTranscript() ? 94 : 57, null);\n            }\n\n            if (stop) throw new InterruptedException("Batch stopped");\n            store.setStage(j.id, dir.getAbsolutePath(), "EXPORTING", 95);\n'''
if marker not in s:
    raise SystemExit('audio-after-transcript insertion point not found')
s = s.replace(marker, insert)

marker = '''    private String message(Throwable e) { String m = e.getMessage(); return m == null || m.isBlank() ? e.getClass().getSimpleName() : m; }\n'''
helper = '''    private String shortError(Throwable e) {\n        if (e == null) return "unknown";\n        String m = e.getMessage();\n        if (m == null || m.isBlank()) m = e.getClass().getSimpleName();\n        m = m.replace('\\n', ' ').replace('\\r', ' ').trim();\n        return m.length() > 500 ? m.substring(0, 500) : m;\n    }\n\n    private String message(Throwable e) { String m = e.getMessage(); return m == null || m.isBlank() ? e.getClass().getSimpleName() : m; }\n'''
if marker not in s:
    raise SystemExit('shortError insertion point not found')
s = s.replace(marker, helper)

p.write_text(s)
print('BatchService patched for Android-native decode and resilient audio fallback')
