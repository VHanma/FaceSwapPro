package org.vaan.faceswap.apex;

import ai.onnxruntime.OnnxTensor;
import ai.onnxruntime.OnnxValue;
import ai.onnxruntime.OrtEnvironment;
import ai.onnxruntime.OrtException;
import ai.onnxruntime.OrtSession;

import java.nio.FloatBuffer;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Small JNI-friendly ONNX Runtime bridge for the Python/Kivy layer.
 *
 * The Python side passes flat float arrays and shapes. Sessions are cached so a
 * multi-thousand-frame video does not reopen 500 MB models for every frame.
 * NNAPI is requested when the device/ORT build supports it and ORT CPU remains
 * the automatic fallback.
 */
public final class ApexOrtBridge {
    private static final OrtEnvironment ENV = OrtEnvironment.getEnvironment();
    private static final ConcurrentHashMap<String, OrtSession> SESSIONS = new ConcurrentHashMap<>();

    private ApexOrtBridge() {}

    private static OrtSession getSession(String modelPath) throws OrtException {
        OrtSession existing = SESSIONS.get(modelPath);
        if (existing != null) {
            return existing;
        }

        synchronized (SESSIONS) {
            existing = SESSIONS.get(modelPath);
            if (existing != null) {
                return existing;
            }

            OrtSession.SessionOptions options = new OrtSession.SessionOptions();
            options.setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT);
            int cores = Math.max(1, Runtime.getRuntime().availableProcessors());
            options.setIntraOpNumThreads(Math.max(1, Math.min(4, cores - 1)));
            options.setInterOpNumThreads(1);
            try {
                options.addNnapi();
            } catch (Throwable ignored) {
                // CPU execution remains available on devices where NNAPI cannot
                // load a particular graph or this ORT build lacks the provider.
            }

            OrtSession created = ENV.createSession(modelPath, options);
            SESSIONS.put(modelPath, created);
            return created;
        }
    }

    private static float[] copyFirstOutput(OrtSession.Result result) throws OrtException {
        if (result.size() < 1) {
            throw new OrtException("ONNX model returned no outputs");
        }
        OnnxValue value = result.get(0);
        if (!(value instanceof OnnxTensor)) {
            throw new OrtException("ONNX first output is not a tensor");
        }
        FloatBuffer buffer = ((OnnxTensor) value).getFloatBuffer();
        if (buffer == null) {
            throw new OrtException("ONNX first output cannot be represented as float32");
        }
        buffer.rewind();
        float[] output = new float[buffer.remaining()];
        buffer.get(output);
        return output;
    }

    public static float[] run1(
            String modelPath,
            String inputName,
            float[] input,
            long[] shape) throws OrtException {
        OrtSession session = getSession(modelPath);
        try (OnnxTensor tensor = OnnxTensor.createTensor(ENV, FloatBuffer.wrap(input), shape)) {
            Map<String, OnnxTensor> inputs = new HashMap<>();
            inputs.put(inputName, tensor);
            try (OrtSession.Result result = session.run(inputs)) {
                return copyFirstOutput(result);
            }
        }
    }

    public static float[] run2(
            String modelPath,
            String inputNameA,
            float[] inputA,
            long[] shapeA,
            String inputNameB,
            float[] inputB,
            long[] shapeB) throws OrtException {
        OrtSession session = getSession(modelPath);
        try (
            OnnxTensor tensorA = OnnxTensor.createTensor(ENV, FloatBuffer.wrap(inputA), shapeA);
            OnnxTensor tensorB = OnnxTensor.createTensor(ENV, FloatBuffer.wrap(inputB), shapeB)
        ) {
            Map<String, OnnxTensor> inputs = new HashMap<>();
            inputs.put(inputNameA, tensorA);
            inputs.put(inputNameB, tensorB);
            try (OrtSession.Result result = session.run(inputs)) {
                return copyFirstOutput(result);
            }
        }
    }

    public static String describe(String modelPath) throws OrtException {
        OrtSession session = getSession(modelPath);
        return "inputs=" + session.getInputInfo().keySet().toString()
                + "; outputs=" + session.getOutputInfo().keySet().toString();
    }

    public static void clear() {
        synchronized (SESSIONS) {
            for (OrtSession session : SESSIONS.values()) {
                try {
                    session.close();
                } catch (Throwable ignored) {}
            }
            SESSIONS.clear();
        }
    }
}
