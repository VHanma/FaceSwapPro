"""Omega temporal face-swap upgrades for FaceSwap Pro.

This module deliberately stays dependency-light for python-for-android: NumPy +
OpenCV only. It layers motion tracking, adaptive landmark fusion, temporal
reprojection, target micro-detail recovery, source-detail enhancement and
failure recovery on top of the existing expression-aware engine.
"""

from __future__ import annotations

from typing import Optional, Sequence

import cv2
import numpy as np

from faceswap_engine import FaceSwapper as BaseFaceSwapper, Rect


class OmegaFaceSwapper(BaseFaceSwapper):
    """High-quality temporal extension of the packaged FaceSwap Pro engine."""

    def __init__(self, detection_width: int = 768) -> None:
        super().__init__(detection_width=detection_width)
        self._omega_prev_gray: Optional[np.ndarray] = None
        self._omega_prev_points: Optional[np.ndarray] = None
        self._omega_prev_rect: Optional[Rect] = None
        self._omega_prev_render: Optional[np.ndarray] = None
        self._omega_frame_index = 0
        self._omega_lost_frames = 0
        self._omega_source_key = None
        self._omega_source_points: Optional[np.ndarray] = None
        self._omega_source_image: Optional[np.ndarray] = None
        self._omega_source_rect: Optional[Rect] = None
        self._omega_detect_interval = 4

    def _omega_reset_temporal(self) -> None:
        self._omega_prev_gray = None
        self._omega_prev_points = None
        self._omega_prev_rect = None
        self._omega_prev_render = None
        self._omega_frame_index = 0
        self._omega_lost_frames = 0
        self._previous_target_points = None
        self._previous_target_rect = None

    @staticmethod
    def _omega_gray(image: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return cv2.GaussianBlur(gray, (3, 3), 0)

    @staticmethod
    def _omega_rect_from_points(points: np.ndarray, shape: Sequence[int]) -> Rect:
        ih, iw = shape[:2]
        face = points[:14] if len(points) >= 14 else points
        x0, y0 = np.min(face, axis=0)
        x1, y1 = np.max(face, axis=0)
        width = max(12.0, float(x1 - x0))
        height = max(12.0, float(y1 - y0))
        pad_x = width * 0.055
        pad_y = height * 0.035
        x0 = max(0.0, x0 - pad_x)
        y0 = max(0.0, y0 - pad_y)
        x1 = min(float(iw - 1), x1 + pad_x)
        y1 = min(float(ih - 1), y1 + pad_y)
        return (
            int(round(x0)),
            int(round(y0)),
            max(1, int(round(x1 - x0))),
            max(1, int(round(y1 - y0))),
        )

    def _omega_track_points(
        self, gray: np.ndarray
    ) -> tuple[Optional[np.ndarray], float]:
        if self._omega_prev_gray is None or self._omega_prev_points is None:
            return None, 0.0
        previous = self._omega_prev_points.astype(np.float32).reshape(-1, 1, 2)
        try:
            current, status, _error = cv2.calcOpticalFlowPyrLK(
                self._omega_prev_gray,
                gray,
                previous,
                None,
                winSize=(25, 25),
                maxLevel=3,
                criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 24, 0.01),
                flags=cv2.OPTFLOW_LK_GET_MIN_EIGENVALS,
                minEigThreshold=1e-4,
            )
        except cv2.error:
            return None, 0.0
        if current is None or status is None:
            return None, 0.0
        current = current.reshape(-1, 2)
        good = status.reshape(-1).astype(bool)
        if len(current) != len(previous):
            return None, 0.0
        valid_ratio = float(np.mean(good)) if len(good) else 0.0
        if valid_ratio < 0.55:
            return None, valid_ratio

        tracked = self._omega_prev_points.copy().astype(np.float32)
        tracked[good] = current[good]
        delta = tracked - self._omega_prev_points
        median_delta = np.median(delta[good], axis=0) if np.any(good) else np.zeros(2)
        residual = np.linalg.norm(delta - median_delta, axis=1)
        face_w = max(24.0, float(np.ptp(self._omega_prev_points[:14, 0])))
        outlier = residual > face_w * 0.18
        tracked[outlier] = self._omega_prev_points[outlier] + median_delta
        confidence = valid_ratio * float(np.mean(~outlier))
        return tracked, confidence

    @staticmethod
    def _omega_motion(
        previous: Optional[np.ndarray], current: np.ndarray, face_width: float
    ) -> float:
        if previous is None or previous.shape != current.shape:
            return 1.0
        displacement = np.linalg.norm(current - previous, axis=1)
        return float(np.median(displacement) / max(24.0, face_width))

    def _omega_geometry(
        self, frame: np.ndarray, hint_rect: Optional[Rect]
    ) -> tuple[Optional[np.ndarray], Optional[Rect], float]:
        gray = self._omega_gray(frame)
        old_points = self._omega_prev_points.copy() if self._omega_prev_points is not None else None
        tracked, track_conf = self._omega_track_points(gray)

        force_detect = (
            self._omega_prev_rect is None
            or self._omega_frame_index % self._omega_detect_interval == 0
            or track_conf < 0.72
            or self._omega_lost_frames > 0
        )
        detected = None
        measured = None
        rect_hint = hint_rect or self._omega_prev_rect
        if force_detect:
            detected = super().detect_face(frame, previous=rect_hint)
            if detected is not None:
                measured = super()._landmarks(frame, detected)

        if measured is not None and tracked is not None and tracked.shape == measured.shape:
            tracked_weight = float(np.clip(0.18 + 0.34 * track_conf, 0.18, 0.50))
            points = measured * (1.0 - tracked_weight) + tracked * tracked_weight
            rect = detected
            self._omega_lost_frames = 0
        elif measured is not None:
            points = measured
            rect = detected
            self._omega_lost_frames = 0
        elif tracked is not None and track_conf >= 0.62 and self._omega_lost_frames < 8:
            points = tracked
            rect = self._omega_rect_from_points(points, frame.shape)
            self._omega_lost_frames += 1
        else:
            self._omega_prev_gray = gray
            self._omega_lost_frames += 1
            self._omega_frame_index += 1
            return None, None, 1.0

        assert points is not None and rect is not None
        face_w = max(24.0, float(rect[2]))
        motion = self._omega_motion(old_points, points, face_w)

        if old_points is not None and old_points.shape == points.shape:
            fresh = float(np.clip(0.30 + motion * 3.4, 0.30, 0.78))
            points = old_points * (1.0 - fresh) + points * fresh

        points[:, 0] = np.clip(points[:, 0], 0, frame.shape[1] - 1)
        points[:, 1] = np.clip(points[:, 1], 0, frame.shape[0] - 1)
        rect = self._omega_rect_from_points(points, frame.shape)

        self._omega_prev_gray = gray
        self._omega_prev_points = points.copy()
        self._omega_prev_rect = rect
        self._omega_frame_index += 1
        return points, rect, motion

    @staticmethod
    def _omega_enhance_source(image: np.ndarray, rect: Rect) -> np.ndarray:
        out = image.copy()
        x, y, w, h = rect
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(image.shape[1], x + w)
        y1 = min(image.shape[0], y + h)
        roi = out[y0:y1, x0:x1]
        if roi.size == 0:
            return out

        lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=1.45, tileGridSize=(6, 6))
        l2 = clahe.apply(l)
        boosted = cv2.cvtColor(cv2.merge((l2, a, b)), cv2.COLOR_LAB2BGR)
        low = cv2.GaussianBlur(boosted, (0, 0), 1.15)
        detail = boosted.astype(np.float32) - low.astype(np.float32)
        enhanced = boosted.astype(np.float32) + detail * 0.32
        out[y0:y1, x0:x1] = np.clip(enhanced, 0, 255).astype(np.uint8)
        return out

    def _omega_prepare_source(
        self, source_image: np.ndarray, source_rect: Rect
    ) -> tuple[np.ndarray, np.ndarray]:
        key = (id(source_image), source_image.shape, source_rect)
        if self._omega_source_key != key or self._omega_source_points is None:
            enhanced = self._omega_enhance_source(source_image, source_rect)
            self._omega_source_key = key
            self._omega_source_image = enhanced
            self._omega_source_rect = source_rect
            self._omega_source_points = super()._landmarks(enhanced, source_rect)
        assert self._omega_source_image is not None
        assert self._omega_source_points is not None
        return self._omega_source_image, self._omega_source_points

    @staticmethod
    def _omega_local_color_match(
        source: np.ndarray, target: np.ndarray
    ) -> np.ndarray:
        matched = BaseFaceSwapper._color_match(source, target)
        s_lab = cv2.cvtColor(matched, cv2.COLOR_BGR2LAB).astype(np.float32)
        t_lab = cv2.cvtColor(target, cv2.COLOR_BGR2LAB).astype(np.float32)
        s_l = s_lab[:, :, 0]
        t_l = t_lab[:, :, 0]
        s_med = float(np.median(s_l))
        t_med = float(np.median(t_l))
        delta = float(np.clip(t_med - s_med, -22.0, 22.0))
        s_lab[:, :, 0] = np.clip(s_l + delta * 0.55, 0, 255)
        return cv2.cvtColor(s_lab.astype(np.uint8), cv2.COLOR_LAB2BGR)

    @staticmethod
    def _omega_reproject_previous(
        previous_render: Optional[np.ndarray],
        previous_points: Optional[np.ndarray],
        current_points: np.ndarray,
        shape: Sequence[int],
    ) -> Optional[np.ndarray]:
        if (
            previous_render is None
            or previous_points is None
            or previous_points.shape != current_points.shape
        ):
            return None
        stable_idx = np.array(
            [0, 2, 4, 6, 8, 10, 12, 14, 16, 17, 19, 32, 33, 34, 35],
            dtype=np.int32,
        )
        stable_idx = stable_idx[stable_idx < len(current_points)]
        if len(stable_idx) < 5:
            return None
        try:
            matrix, inliers = cv2.estimateAffinePartial2D(
                previous_points[stable_idx],
                current_points[stable_idx],
                method=cv2.RANSAC,
                ransacReprojThreshold=3.0,
                maxIters=100,
                confidence=0.98,
                refineIters=8,
            )
        except cv2.error:
            return None
        if matrix is None:
            return None
        if inliers is not None and float(np.mean(inliers)) < 0.45:
            return None
        h, w = shape[:2]
        try:
            return cv2.warpAffine(
                previous_render,
                matrix,
                (w, h),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REFLECT_101,
            )
        except cv2.error:
            return None

    @staticmethod
    def _omega_microdetail(
        image: np.ndarray,
        target: np.ndarray,
        mask: np.ndarray,
        rect: Rect,
    ) -> np.ndarray:
        _x, _y, w, _h = rect
        out = image.astype(np.float32)
        blur_sigma = max(0.9, min(2.2, w * 0.006))
        image_low = cv2.GaussianBlur(out, (0, 0), blur_sigma)
        image_detail = out - image_low
        target_f = target.astype(np.float32)
        target_low = cv2.GaussianBlur(target_f, (0, 0), blur_sigma * 1.2)
        target_detail = target_f - target_low

        alpha = (mask.astype(np.float32) / 255.0)[:, :, None]
        enhanced = out + image_detail * 0.18 * alpha + target_detail * 0.16 * alpha

        k = max(3, int(w * 0.035)) | 1
        inner = cv2.erode(
            mask,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)),
            iterations=1,
        )
        inner_a = (inner.astype(np.float32) / 255.0)[:, :, None]
        mixed = out * (1.0 - inner_a) + enhanced * inner_a
        return np.clip(mixed, 0, 255).astype(np.uint8)

    def swap_face(
        self,
        source_image: np.ndarray,
        target_frame: np.ndarray,
        source_rect: Optional[Rect] = None,
        target_rect: Optional[Rect] = None,
    ) -> tuple[np.ndarray, Optional[Rect]]:
        if source_image is None or source_image.size == 0:
            raise ValueError("Source photo is empty")
        if target_frame is None or target_frame.size == 0:
            return target_frame, None

        if source_rect is None:
            source_rect = super().detect_face(source_image)
        if source_rect is None:
            raise ValueError("No face detected in the source photo")

        old_points = (
            self._omega_prev_points.copy()
            if self._omega_prev_points is not None
            else None
        )
        old_render = self._omega_prev_render
        target_points, resolved_rect, motion = self._omega_geometry(
            target_frame, target_rect
        )
        if target_points is None or resolved_rect is None:
            self._omega_prev_render = target_frame.copy()
            return target_frame.copy(), self._omega_prev_rect

        source_prepared, source_points = self._omega_prepare_source(
            source_image, source_rect
        )
        sx, sy, sw, sh = source_rect
        tx, ty, tw, th = resolved_rect
        source_crop = source_prepared[sy : sy + sh, sx : sx + sw]
        target_crop = target_frame[ty : ty + th, tx : tx + tw]
        color_source = source_prepared.copy()
        if source_crop.size and target_crop.size:
            resized_target = cv2.resize(
                target_crop, (sw, sh), interpolation=cv2.INTER_AREA
            )
            color_source[sy : sy + sh, sx : sx + sw] = self._omega_local_color_match(
                source_crop, resized_target
            )

        result = target_frame.copy()
        for i1, i2, i3 in super()._triangle_indices(
            target_points, target_frame.shape
        ):
            super()._warp_triangle(
                color_source,
                result,
                source_points[[i1, i2, i3]],
                target_points[[i1, i2, i3]],
            )

        soft_mask = super()._soft_face_mask(
            target_points, target_frame.shape, tw
        )
        result = super()._transfer_target_lighting(
            result, target_frame, soft_mask
        )
        result = super()._restore_expression_detail(
            result, target_frame, target_points, resolved_rect
        )

        alpha = (soft_mask.astype(np.float32) / 255.0)[:, :, None]
        feathered = np.clip(
            result.astype(np.float32) * alpha
            + target_frame.astype(np.float32) * (1.0 - alpha),
            0,
            255,
        ).astype(np.uint8)

        clone_mask = np.where(soft_mask > 38, 255, 0).astype(np.uint8)
        center = (
            min(max(int(tx + tw / 2), 1), target_frame.shape[1] - 2),
            min(max(int(ty + th / 2), 1), target_frame.shape[0] - 2),
        )
        try:
            current = cv2.seamlessClone(
                feathered,
                target_frame,
                clone_mask,
                center,
                cv2.MIXED_CLONE,
            )
        except cv2.error:
            current = feathered

        history = self._omega_reproject_previous(
            old_render, old_points, target_points, target_frame.shape
        )
        if history is not None:
            history_weight = float(np.clip(0.30 - motion * 1.9, 0.06, 0.30))
            face_a = alpha * history_weight
            current = np.clip(
                current.astype(np.float32) * (1.0 - face_a)
                + history.astype(np.float32) * face_a,
                0,
                255,
            ).astype(np.uint8)

        current = super()._restore_expression_detail(
            current, target_frame, target_points, resolved_rect
        )
        current = self._omega_microdetail(
            current, target_frame, soft_mask, resolved_rect
        )

        self._omega_prev_render = current.copy()
        return current, resolved_rect

    def process_video(self, *args, **kwargs):
        self._omega_reset_temporal()
        return super().process_video(*args, **kwargs)
