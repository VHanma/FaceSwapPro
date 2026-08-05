"""FaceSwap Pro APEX neural engine.

This is the quality-first path. It replaces heuristic triangle identity transfer
with a 256px neural identity generator, real 5-point alignment, semantic face
parsing, temporal stabilization, target-lighting adaptation and optional 512px
neural restoration. Video/audio encoding stays in the proven Android FFmpeg
pipeline from the existing engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import cv2
import numpy as np

from apex_models import is_model_ready, paths_for
from apex_ort import run1, run2
from faceswap import FaceSwapper as AndroidVideoFaceSwapper

Rect = Tuple[int, int, int, int]


# FaceFusion/ArcFace normalized alignment templates. Keeping exact normalized
# coordinates avoids the loose rectangle warping that made v1.x look pasted on.
ARC_112_V1 = np.array(
    [
        [0.35473214, 0.45658929],
        [0.64526786, 0.45658929],
        [0.50000000, 0.61154464],
        [0.37913393, 0.77687500],
        [0.62086607, 0.77687500],
    ],
    dtype=np.float32,
)
ARC_112_V2 = np.array(
    [
        [0.34191607, 0.46157411],
        [0.65653393, 0.45983393],
        [0.50022500, 0.64050536],
        [0.37097589, 0.82469196],
        [0.63151696, 0.82325089],
    ],
    dtype=np.float32,
)


@dataclass
class Detection:
    rect: Rect
    landmarks: np.ndarray
    score: float


class ApexFaceSwapper(AndroidVideoFaceSwapper):
    """Neural GHOST identity swap with pro-grade compositing."""

    def __init__(self) -> None:
        super().__init__()
        pack = "ultra" if is_model_ready("gfpgan") else "pro"
        self.models = paths_for(pack)
        self.ultra_restore = "gfpgan" in self.models

        if not hasattr(cv2, "FaceDetectorYN_create"):
            raise RuntimeError(
                "This OpenCV build lacks YuNet FaceDetectorYN. Rebuild the Apex APK "
                "with the current OpenCV recipe."
            )
        self.detector = cv2.FaceDetectorYN_create(
            self.models["yunet"], "", (320, 320), 0.72, 0.30, 5000
        )

        self._source_embedding: Optional[np.ndarray] = None
        self._prev_target_gray: Optional[np.ndarray] = None
        self._prev_landmarks: Optional[np.ndarray] = None
        self._prev_target_rect: Optional[Rect] = None
        self._prev_apex_crop: Optional[np.ndarray] = None

    @staticmethod
    def _iou(a: Rect, b: Rect) -> float:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        x1, y1 = max(ax, bx), max(ay, by)
        x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        union = aw * ah + bw * bh - inter
        return inter / union if union > 0 else 0.0

    def _detect_yunet(
        self, image: np.ndarray, previous: Optional[Rect] = None
    ) -> Optional[Detection]:
        if image is None or image.size == 0:
            return None
        h, w = image.shape[:2]
        self.detector.setInputSize((w, h))
        try:
            _, faces = self.detector.detect(image)
        except cv2.error:
            return None
        if faces is None or len(faces) == 0:
            return None

        candidates: list[Detection] = []
        for raw in np.asarray(faces):
            x, y, fw, fh = raw[:4]
            # YuNet emits right-eye, left-eye, nose, right-mouth, left-mouth.
            # APEX uses the standard left/right ArcFace landmark order.
            lm = np.array(
                [
                    [raw[6], raw[7]],
                    [raw[4], raw[5]],
                    [raw[8], raw[9]],
                    [raw[12], raw[13]],
                    [raw[10], raw[11]],
                ],
                dtype=np.float32,
            )
            rect = (
                max(0, int(round(x))),
                max(0, int(round(y))),
                max(2, int(round(fw))),
                max(2, int(round(fh))),
            )
            score = float(raw[14]) if len(raw) > 14 else 1.0
            candidates.append(Detection(rect, lm, score))

        if previous is None:
            return max(candidates, key=lambda d: d.score * d.rect[2] * d.rect[3])
        return max(
            candidates,
            key=lambda d: self._iou(d.rect, previous) * 7.0
            + d.score
            + (d.rect[2] * d.rect[3]) / float(max(1, w * h)),
        )

    def detect_face(
        self, image: np.ndarray, previous: Optional[Rect] = None
    ) -> Optional[Rect]:
        detection = self._detect_yunet(image, previous)
        return detection.rect if detection is not None else previous

    @staticmethod
    def _estimate_matrix(
        landmarks: np.ndarray,
        template: np.ndarray,
        size: int,
    ) -> np.ndarray:
        destination = template * np.array([size, size], dtype=np.float32)
        matrix = cv2.estimateAffinePartial2D(
            landmarks.astype(np.float32),
            destination,
            method=cv2.RANSAC,
            ransacReprojThreshold=max(3.0, size * 0.035),
        )[0]
        if matrix is None:
            matrix = cv2.getAffineTransform(
                landmarks[[0, 1, 2]].astype(np.float32),
                destination[[0, 1, 2]].astype(np.float32),
            )
        return matrix.astype(np.float32)

    @classmethod
    def _align(
        cls,
        image: np.ndarray,
        landmarks: np.ndarray,
        template: np.ndarray,
        size: int,
    ) -> tuple[np.ndarray, np.ndarray]:
        matrix = cls._estimate_matrix(landmarks, template, size)
        interpolation = cv2.INTER_AREA if max(image.shape[:2]) > size * 2 else cv2.INTER_CUBIC
        crop = cv2.warpAffine(
            image,
            matrix,
            (size, size),
            flags=interpolation,
            borderMode=cv2.BORDER_REPLICATE,
        )
        return crop, matrix

    @staticmethod
    def _preprocess_arcface(crop: np.ndarray) -> np.ndarray:
        data = crop.astype(np.float32) / 127.5 - 1.0
        data = data[:, :, ::-1].transpose(2, 0, 1)
        return np.expand_dims(data, 0).astype(np.float32)

    @staticmethod
    def _preprocess_ghost(crop: np.ndarray) -> np.ndarray:
        data = crop[:, :, ::-1].astype(np.float32) / 255.0
        data = (data - 0.5) / 0.5
        return np.expand_dims(data.transpose(2, 0, 1), 0).astype(np.float32)

    @staticmethod
    def _normalize_ghost(output: np.ndarray) -> np.ndarray:
        data = output[0].transpose(1, 2, 0)
        data = np.clip(data * 0.5 + 0.5, 0.0, 1.0)
        return np.rint(data[:, :, ::-1] * 255.0).astype(np.uint8)

    def _identity_embedding(
        self, source_image: np.ndarray, detection: Detection
    ) -> np.ndarray:
        if self._source_embedding is not None:
            return self._source_embedding

        crop, _ = self._align(source_image, detection.landmarks, ARC_112_V2, 112)
        arc_input = self._preprocess_arcface(crop)
        embedding = run1(
            self.models["arcface"],
            "input",
            arc_input,
            (1, 3, 112, 112),
            (1, 512),
        )
        converted = run1(
            self.models["ghost_converter"],
            "input",
            embedding.reshape(1, 512),
            (1, 512),
        ).reshape(1, -1)
        if converted.shape[1] < 128:
            raise RuntimeError("GHOST embedding converter returned invalid identity data")
        self._source_embedding = np.ascontiguousarray(converted, dtype=np.float32)
        return self._source_embedding

    def _neural_swap(self, crop256: np.ndarray, source_embedding: np.ndarray) -> np.ndarray:
        target = self._preprocess_ghost(crop256)
        output = run2(
            self.models["ghost"],
            "source",
            source_embedding,
            source_embedding.shape,
            "target",
            target,
            (1, 3, 256, 256),
            (1, 3, 256, 256),
        )
        return self._normalize_ghost(output)

    def _semantic_mask(self, aligned_target512: np.ndarray) -> np.ndarray:
        if "parser" not in self.models:
            mask = np.zeros((512, 512), dtype=np.float32)
            cv2.ellipse(mask, (256, 266), (202, 226), 0, 0, 360, 1.0, -1, cv2.LINE_AA)
            return cv2.GaussianBlur(mask, (0, 0), 9.0)

        data = aligned_target512[:, :, ::-1].astype(np.float32) / 255.0
        data -= np.array([0.485, 0.456, 0.406], dtype=np.float32)
        data /= np.array([0.229, 0.224, 0.225], dtype=np.float32)
        data = np.expand_dims(data.transpose(2, 0, 1), 0)
        logits = run1(
            self.models["parser"],
            "input",
            data,
            (1, 3, 512, 512),
            (1, 19, 512, 512),
        )[0]
        classes = np.argmax(logits, axis=0)
        # Preserve target hair/background/glasses. Swap only actual face regions.
        wanted = np.array([1, 2, 3, 4, 5, 10, 11, 12, 13], dtype=np.int32)
        mask = np.isin(classes, wanted).astype(np.float32)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.dilate(mask, kernel, iterations=1)
        mask = cv2.GaussianBlur(mask, (0, 0), 6.5)
        return np.clip(mask, 0.0, 1.0)

    @staticmethod
    def _match_lighting(swapped: np.ndarray, target: np.ndarray, mask: np.ndarray) -> np.ndarray:
        swap_lab = cv2.cvtColor(swapped, cv2.COLOR_BGR2LAB).astype(np.float32)
        target_lab = cv2.cvtColor(target, cv2.COLOR_BGR2LAB).astype(np.float32)
        sigma = max(5.0, swapped.shape[0] * 0.035)
        source_low = cv2.GaussianBlur(swap_lab[:, :, 0], (0, 0), sigma)
        target_low = cv2.GaussianBlur(target_lab[:, :, 0], (0, 0), sigma)
        delta = np.clip(target_low - source_low, -42.0, 42.0)
        swap_lab[:, :, 0] = np.clip(
            swap_lab[:, :, 0] + delta * mask * 0.78, 0, 255
        )

        # Low-strength chroma adaptation keeps identity while matching the shot.
        for channel in (1, 2):
            src = swap_lab[:, :, channel]
            tgt = target_lab[:, :, channel]
            swap_lab[:, :, channel] = src * (1.0 - mask * 0.16) + tgt * (mask * 0.16)
        return cv2.cvtColor(swap_lab.astype(np.uint8), cv2.COLOR_LAB2BGR)

    def _restore512(self, swapped512: np.ndarray) -> np.ndarray:
        if not self.ultra_restore:
            return swapped512
        data = swapped512[:, :, ::-1].astype(np.float32) / 255.0
        data = (data - 0.5) / 0.5
        data = np.expand_dims(data.transpose(2, 0, 1), 0)
        try:
            output = run1(
                self.models["gfpgan"],
                "input",
                data,
                (1, 3, 512, 512),
                (1, 3, 512, 512),
            )[0]
            restored = np.clip(output.transpose(1, 2, 0), -1.0, 1.0)
            restored = np.rint((restored + 1.0) * 127.5).astype(np.uint8)[:, :, ::-1]
            # Restoration is deliberately blended rather than allowed to
            # repaint identity wholesale.
            return cv2.addWeighted(restored, 0.68, swapped512, 0.32, 0.0)
        except Exception:
            return swapped512

    def _temporal_stabilize(self, current: np.ndarray, target: np.ndarray) -> np.ndarray:
        if self._prev_apex_crop is None or self._prev_apex_crop.shape != current.shape:
            self._prev_apex_crop = current.copy()
            return current

        # Small motion-aware blend removes neural shimmer without freezing lips.
        gray = cv2.cvtColor(target, cv2.COLOR_BGR2GRAY)
        previous_gray = None
        if self._prev_target_gray is not None:
            previous_gray = cv2.resize(
                self._prev_target_gray, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_AREA
            )
        if previous_gray is None or previous_gray.shape != gray.shape:
            self._prev_apex_crop = current.copy()
            return current
        try:
            flow = cv2.calcOpticalFlowFarneback(
                previous_gray, gray, None, 0.5, 3, 15, 3, 5, 1.1, 0
            )
            h, w = gray.shape
            gx, gy = np.meshgrid(
                np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32)
            )
            warped = cv2.remap(
                self._prev_apex_crop,
                gx - flow[:, :, 0],
                gy - flow[:, :, 1],
                cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REFLECT_101,
            )
            motion = cv2.magnitude(flow[:, :, 0], flow[:, :, 1])
            motion = cv2.GaussianBlur(motion, (0, 0), 1.2)
            # Moving lips/eyes favor the new frame. Static skin gets more history.
            current_weight = np.clip(0.58 + motion / 7.0, 0.58, 0.92)[:, :, None]
            stabilized = np.clip(
                current.astype(np.float32) * current_weight
                + warped.astype(np.float32) * (1.0 - current_weight),
                0,
                255,
            ).astype(np.uint8)
            self._prev_apex_crop = stabilized.copy()
            return stabilized
        except cv2.error:
            self._prev_apex_crop = current.copy()
            return current

    def _track_when_detector_misses(self, frame: np.ndarray) -> Optional[Detection]:
        if (
            self._prev_target_gray is None
            or self._prev_landmarks is None
            or self._prev_target_rect is None
        ):
            return None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        try:
            next_points, status, _ = cv2.calcOpticalFlowPyrLK(
                self._prev_target_gray,
                gray,
                self._prev_landmarks.reshape(-1, 1, 2).astype(np.float32),
                None,
                winSize=(31, 31),
                maxLevel=3,
                criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
            )
            if next_points is None or status is None or int(status.sum()) < 4:
                return None
            new_landmarks = next_points.reshape(-1, 2)
            delta = np.median(new_landmarks - self._prev_landmarks, axis=0)
            x, y, w, h = self._prev_target_rect
            rect = (int(x + delta[0]), int(y + delta[1]), w, h)
            return Detection(rect, new_landmarks.astype(np.float32), 0.60)
        except cv2.error:
            return None

    def _target_detection(
        self, frame: np.ndarray, previous: Optional[Rect]
    ) -> Optional[Detection]:
        detection = self._detect_yunet(frame, previous)
        if detection is None:
            detection = self._track_when_detector_misses(frame)
        if detection is None:
            return None

        if (
            self._prev_landmarks is not None
            and self._prev_target_rect is not None
            and self._iou(detection.rect, self._prev_target_rect) > 0.18
        ):
            detection.landmarks = (
                self._prev_landmarks * 0.28 + detection.landmarks * 0.72
            ).astype(np.float32)
        return detection

    @staticmethod
    def _paste(
        frame: np.ndarray,
        crop: np.ndarray,
        mask: np.ndarray,
        matrix: np.ndarray,
    ) -> np.ndarray:
        h, w = frame.shape[:2]
        inverse = cv2.invertAffineTransform(matrix)
        pasted = cv2.warpAffine(
            crop,
            inverse,
            (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REFLECT_101,
        )
        pasted_mask = cv2.warpAffine(
            mask.astype(np.float32),
            inverse,
            (w, h),
            flags=cv2.INTER_LINEAR,
        )
        pasted_mask = cv2.GaussianBlur(np.clip(pasted_mask, 0, 1), (0, 0), 2.4)
        alpha = pasted_mask[:, :, None]
        return np.clip(
            pasted.astype(np.float32) * alpha
            + frame.astype(np.float32) * (1.0 - alpha),
            0,
            255,
        ).astype(np.uint8)

    def swap_face(
        self,
        source_image: np.ndarray,
        target_frame: np.ndarray,
        source_rect: Optional[Rect] = None,
        target_rect: Optional[Rect] = None,
    ) -> tuple[np.ndarray, Optional[Rect]]:
        source_detection = self._detect_yunet(source_image, source_rect)
        if source_detection is None:
            raise ValueError("No clear face detected in the source photo")
        source_embedding = self._identity_embedding(source_image, source_detection)

        target_detection = self._target_detection(target_frame, target_rect)
        if target_detection is None:
            self._prev_target_gray = cv2.cvtColor(target_frame, cv2.COLOR_BGR2GRAY)
            self._prev_apex_crop = None
            return target_frame.copy(), None

        crop256, matrix256 = self._align(
            target_frame, target_detection.landmarks, ARC_112_V1, 256
        )
        swapped256 = self._neural_swap(crop256, source_embedding)

        # Compose at 512 when restoration/parser are present. This keeps edge
        # quality when the face occupies more than 256 pixels in a 1080p frame.
        crop512, matrix512 = self._align(
            target_frame, target_detection.landmarks, ARC_112_V1, 512
        )
        swapped512 = cv2.resize(swapped256, (512, 512), interpolation=cv2.INTER_LANCZOS4)
        mask512 = self._semantic_mask(crop512)
        swapped512 = self._match_lighting(swapped512, crop512, mask512)
        swapped512 = self._restore512(swapped512)
        swapped512 = self._temporal_stabilize(swapped512, crop512)

        # Preserve a controlled amount of real target micro-expression texture.
        detail = crop512.astype(np.float32) - cv2.GaussianBlur(
            crop512.astype(np.float32), (0, 0), 1.35
        )
        expression_weight = (mask512 * 0.12)[:, :, None]
        swapped512 = np.clip(
            swapped512.astype(np.float32) + detail * expression_weight,
            0,
            255,
        ).astype(np.uint8)

        result = self._paste(target_frame, swapped512, mask512, matrix512)

        self._prev_target_gray = cv2.cvtColor(target_frame, cv2.COLOR_BGR2GRAY)
        self._prev_landmarks = target_detection.landmarks.copy()
        self._prev_target_rect = target_detection.rect
        return result, target_detection.rect


def process_video(
    source_path: str,
    video_path: str,
    output_path: str,
    progress_cb=None,
    cancel_cb=None,
):
    """API used by the Kivy UI for Apex mode."""
    try:
        engine = ApexFaceSwapper()
    except Exception as exc:
        return False, f"APEX engine could not start: {exc}"
    return engine.process_video(
        source_path,
        video_path,
        output_path,
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
    )
