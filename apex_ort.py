"""Thin Python side of the Android ONNX Runtime bridge used by Apex mode."""

from __future__ import annotations

from typing import Sequence

import numpy as np
from kivy.utils import platform


class ApexOrtError(RuntimeError):
    pass


def _bridge_class():
    if platform != "android":
        raise ApexOrtError("Apex ONNX Runtime is available in the Android APK")

    # Explicit JNI signatures avoid ambiguous reflection and make primitive
    # arrays deterministic across PyJNIus/Android versions.
    from jnius import JavaClass, JavaStaticMethod, MetaJavaClass

    class _Bridge(JavaClass, metaclass=MetaJavaClass):
        __javaclass__ = "org/vaan/faceswap/apex/ApexOrtBridge"
        run1 = JavaStaticMethod(
            "(Ljava/lang/String;Ljava/lang/String;[F[J)[F"
        )
        run2 = JavaStaticMethod(
            "(Ljava/lang/String;Ljava/lang/String;[F[JLjava/lang/String;[F[J)[F"
        )
        describe = JavaStaticMethod("(Ljava/lang/String;)Ljava/lang/String;")
        clear = JavaStaticMethod("()V")

    return _Bridge


def _flat(values: np.ndarray) -> list[float]:
    # PyJNIus converts a Python numeric sequence to Java float[]. Keeping this
    # conversion in one place makes a future direct-buffer bridge easy.
    return np.ascontiguousarray(values, dtype=np.float32).reshape(-1).tolist()


def run1(
    model_path: str,
    input_name: str,
    values: np.ndarray,
    shape: Sequence[int],
    output_shape: Sequence[int] | None = None,
) -> np.ndarray:
    try:
        raw = _bridge_class().run1(
            str(model_path),
            str(input_name),
            _flat(values),
            [int(v) for v in shape],
        )
    except Exception as exc:
        raise ApexOrtError(f"ONNX inference failed for {model_path}: {exc}") from exc

    output = np.asarray(raw, dtype=np.float32)
    if output_shape is not None:
        expected = int(np.prod(tuple(output_shape)))
        if output.size != expected:
            raise ApexOrtError(
                f"Unexpected ONNX output size {output.size}; expected {expected}"
            )
        output = output.reshape(tuple(int(v) for v in output_shape))
    return output


def run2(
    model_path: str,
    input_name_a: str,
    values_a: np.ndarray,
    shape_a: Sequence[int],
    input_name_b: str,
    values_b: np.ndarray,
    shape_b: Sequence[int],
    output_shape: Sequence[int] | None = None,
) -> np.ndarray:
    try:
        raw = _bridge_class().run2(
            str(model_path),
            str(input_name_a),
            _flat(values_a),
            [int(v) for v in shape_a],
            str(input_name_b),
            _flat(values_b),
            [int(v) for v in shape_b],
        )
    except Exception as exc:
        raise ApexOrtError(f"ONNX inference failed for {model_path}: {exc}") from exc

    output = np.asarray(raw, dtype=np.float32)
    if output_shape is not None:
        expected = int(np.prod(tuple(output_shape)))
        if output.size != expected:
            raise ApexOrtError(
                f"Unexpected ONNX output size {output.size}; expected {expected}"
            )
        output = output.reshape(tuple(int(v) for v in output_shape))
    return output


def describe(model_path: str) -> str:
    try:
        return str(_bridge_class().describe(str(model_path)))
    except Exception as exc:
        raise ApexOrtError(f"Could not inspect ONNX model: {exc}") from exc


def clear_sessions() -> None:
    if platform != "android":
        return
    try:
        _bridge_class().clear()
    except Exception:
        pass
