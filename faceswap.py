"""Offline face-swap engine for FaceSwap Pro.

Expression-aware, dependency-light face reenactment for Android. The source
identity is warped onto the target face with image-derived eye and mouth
landmarks, target lighting/detail is reintroduced for natural expressions, and
FFmpeg handles final H.264/AAC MP4 encoding.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Tuple

import cv2
import numpy as np

ProgressCallback = Callable[[str, int], None]
CancelCallback = Callable[[], bool]
Rect = Tuple[int, int, int, int]


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    fps: float
    frame_count: int


def _cascade_path() -> str:
    bundled = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "assets",
        "haarcascade_frontalface_alt2.xml",
    )
    if os.path.exists(bundled):
        return bundled
    fallback_root = getattr(getattr(cv2, "data", None), "haarcascades", "")
    return os.path.join(fallback_root, "haarcascade_frontalface_alt2.xml")


class FaceSwapper:
    """Offline face swapper with expression-aware geometry and soft blending."""

    def __init__(self, detection_width: int = 640) -> None:
        self.detection_width = max(240, int(detection_width))
        cascade_path = _cascade_path()
        self.cascade = cv2.CascadeClassifier(cascade_path) if cascade_path else None
        if self.cascade is not None and self.cascade.empty():
            self.cascade = None
        self._previous_target_points: Optional[np.ndarray] = None
        self._previous_target_rect: Optional[Rect] = None

    def detect_face(
        self, image: np.ndarray, previous: Optional[Rect] = None
    ) -> Optional[Rect]:
        """Return the largest detected face, preferring overlap with the last face."""
        if image is None or image.size == 0:
            return None

        height, width = image.shape[:2]
        scale = min(1.0, self.detection_width / float(max(width, 1)))
        if scale < 1.0:
            small = cv2.resize(
                image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA
            )
        else:
            small = image

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        min_face = max(36, int(min(small.shape[:2]) * 0.10))
        if self.cascade is not None:
            found = self.cascade.detectMultiScale(
                gray,
                scaleFactor=1.08,
                minNeighbors=5,
                minSize=(min_face, min_face),
                flags=cv2.CASCADE_SCALE_IMAGE,
            )
        else:
            found = self._skin_face_candidates(small)
        if len(found) == 0:
            return previous

        inv = 1.0 / scale
        rects = [
            (
                int(round(x * inv)),
                int(round(y * inv)),
                int(round(w * inv)),
                int(round(h * inv)),
            )
            for x, y, w, h in found
        ]

        if previous is None:
            return max(rects, key=lambda r: r[2] * r[3])

        def score(rect: Rect) -> float:
            return self._iou(rect, previous) * 5.0 + (rect[2] * rect[3]) / float(
                width * height
            )

        return max(rects, key=score)

    @staticmethod
    def _skin_face_candidates(image: np.ndarray) -> list[Rect]:
        ycrcb = cv2.cvtColor(image, cv2.COLOR_BGR2YCrCb)
        mask = cv2.inRange(ycrcb, (0, 133, 77), (255, 180, 135))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        height, width = image.shape[:2]
        minimum_area = width * height * 0.008
        candidates: list[Rect] = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if w * h < minimum_area or h <= 0:
                continue
            ratio = w / float(h)
            if 0.55 <= ratio <= 1.55 and y < height * 0.82:
                candidates.append((x, y, w, h))
        return candidates

    @staticmethod
    def _iou(a: Rect, b: Rect) -> float:
        ax, ay, aw, ah = a
        bx, by, bw, bh = b
        x1, y1 = max(ax, bx), max(ay, by)
        x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
        inter = max(0, x2 - x1) * max(0, y2 - y1)
        union = aw * ah + bw * bh - inter
        return inter / union if union else 0.0

    @staticmethod
    def _clip_roi(rect: Rect, shape: Sequence[int]) -> Rect:
        x, y, w, h = rect
        ih, iw = shape[:2]
        x = max(0, min(int(x), iw - 1))
        y = max(0, min(int(y), ih - 1))
        w = max(1, min(int(w), iw - x))
        h = max(1, min(int(h), ih - y))
        return x, y, w, h

    @staticmethod
    def _weighted_dark_center(gray: np.ndarray) -> tuple[float, float, float, float]:
        """Return dark-feature center and spread in ROI coordinates."""
        if gray.size == 0:
            return 0.5, 0.5, 0.25, 0.10
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        dark = 255.0 - blur.astype(np.float32)
        gx = cv2.Sobel(blur, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(blur, cv2.CV_32F, 0, 1, ksize=3)
        score = dark + 0.20 * cv2.magnitude(gx, gy)
        threshold = float(np.percentile(score, 72.0))
        weights = np.where(score >= threshold, score - threshold + 1.0, 0.0)
        total = float(weights.sum())
        h, w = gray.shape[:2]
        if total <= 1e-6:
            return w * 0.5, h * 0.5, w * 0.28, h * 0.18
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        cx = float((xx * weights).sum() / total)
        cy = float((yy * weights).sum() / total)
        sx = float(np.sqrt(max(1.0, (((xx - cx) ** 2) * weights).sum() / total)))
        sy = float(np.sqrt(max(1.0, (((yy - cy) ** 2) * weights).sum() / total)))
        return cx, cy, sx, sy

    def _eye_geometry(
        self, image: np.ndarray, rect: Rect, left: bool
    ) -> tuple[float, float, float, float]:
        x, y, w, h = rect
        fx0, fx1 = (0.10, 0.49) if left else (0.51, 0.90)
        rx = int(x + fx0 * w)
        ry = int(y + 0.20 * h)
        rw = max(4, int((fx1 - fx0) * w))
        rh = max(4, int(0.30 * h))
        rx, ry, rw, rh = self._clip_roi((rx, ry, rw, rh), image.shape)
        roi = image[ry : ry + rh, rx : rx + rw]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        cx, cy, sx, sy = self._weighted_dark_center(gray)
        center_x = rx + cx
        center_y = ry + cy
        half_w = float(np.clip(sx * 1.35, w * 0.055, w * 0.115))
        half_h = float(np.clip(sy * 0.95, h * 0.018, h * 0.065))
        return center_x, center_y, half_w, half_h

    def _mouth_geometry(
        self, image: np.ndarray, rect: Rect
    ) -> tuple[float, float, float, float, float]:
        x, y, w, h = rect
        rx = int(x + 0.16 * w)
        ry = int(y + 0.53 * h)
        rw = max(6, int(0.68 * w))
        rh = max(6, int(0.37 * h))
        rx, ry, rw, rh = self._clip_roi((rx, ry, rw, rh), image.shape)
        roi = image[ry : ry + rh, rx : rx + rw]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY).astype(np.float32)
        b, g, r = cv2.split(roi.astype(np.float32))
        redness = np.maximum(0.0, r - 0.5 * (g + b))
        dark = 255.0 - gray
        score = dark * 0.72 + redness * 0.85
        score = cv2.GaussianBlur(score, (5, 5), 0)
        threshold = float(np.percentile(score, 76.0))
        mask = (score >= threshold).astype(np.uint8) * 255
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        best = None
        best_score = -1.0
        for contour in contours:
            bx, by, bw, bh = cv2.boundingRect(contour)
            area = float(bw * bh)
            if area <= 0 or bw < rw * 0.12:
                continue
            center_bias = 1.0 - abs((bx + bw * 0.5) / rw - 0.5)
            lower_bias = (by + bh * 0.5) / max(1.0, rh)
            candidate_score = area * (0.8 + center_bias) * (0.7 + lower_bias)
            if candidate_score > best_score:
                best_score = candidate_score
                best = (bx, by, bw, bh)

        if best is None:
            cx = rx + rw * 0.5
            cy = ry + rh * 0.54
            half_w = w * 0.20
            half_h = h * 0.035
        else:
            bx, by, bw, bh = best
            cx = rx + bx + bw * 0.5
            cy = ry + by + bh * 0.5
            half_w = float(np.clip(bw * 0.72, w * 0.14, w * 0.29))
            half_h = float(np.clip(bh * 0.72, h * 0.025, h * 0.12))

        mx0 = max(0, int(cx - half_w * 0.60))
        mx1 = min(image.shape[1], int(cx + half_w * 0.60))
        my0 = max(0, int(cy - half_h * 0.65))
        my1 = min(image.shape[0], int(cy + half_h * 0.65))
        center_patch = cv2.cvtColor(image[my0:my1, mx0:mx1], cv2.COLOR_BGR2GRAY)
        if center_patch.size:
            darkness = float(1.0 - center_patch.mean() / 255.0)
        else:
            darkness = 0.25
        openness = float(np.clip((half_h / max(1.0, h * 0.10)) * (0.7 + darkness), 0.18, 1.25))
        return cx, cy, half_w, half_h, openness

    def _landmarks(self, image: np.ndarray, rect: Rect) -> np.ndarray:
        """Image-derived landmarks that react to blinking, smiling and talking."""
        x, y, w, h = rect
        ih, iw = image.shape[:2]
        lex, ley, lew, leh = self._eye_geometry(image, rect, True)
        rex, rey, rew, reh = self._eye_geometry(image, rect, False)
        mx, my, mw, mh, openness = self._mouth_geometry(image, rect)

        pts = [
            (x + 0.18*w, y + 0.12*h), (x + 0.35*w, y + 0.045*h),
            (x + 0.50*w, y + 0.020*h), (x + 0.65*w, y + 0.045*h),
            (x + 0.82*w, y + 0.12*h), (x + 0.055*w, y + 0.29*h),
            (x + 0.025*w, y + 0.49*h), (x + 0.085*w, y + 0.70*h),
            (x + 0.23*w, y + 0.88*h), (x + 0.50*w, y + 0.985*h),
            (x + 0.77*w, y + 0.88*h), (x + 0.915*w, y + 0.70*h),
            (x + 0.975*w, y + 0.49*h), (x + 0.945*w, y + 0.29*h),
            (lex - 1.10*lew, ley - 1.55*leh), (lex, ley - 1.75*leh),
            (lex + 1.10*lew, ley - 1.55*leh),
            (rex - 1.10*rew, rey - 1.55*reh), (rex, rey - 1.75*reh),
            (rex + 1.10*rew, rey - 1.55*reh),
            (lex - lew, ley), (lex - 0.45*lew, ley - leh),
            (lex + 0.45*lew, ley - leh), (lex + lew, ley),
            (lex + 0.45*lew, ley + leh), (lex - 0.45*lew, ley + leh),
            (rex - rew, rey), (rex - 0.45*rew, rey - reh),
            (rex + 0.45*rew, rey - reh), (rex + rew, rey),
            (rex + 0.45*rew, rey + reh), (rex - 0.45*rew, rey + reh),
            (x + 0.50*w, y + 0.36*h), (x + 0.46*w, y + 0.55*h),
            (x + 0.54*w, y + 0.55*h), (x + 0.50*w, y + 0.64*h),
            (mx - mw, my), (mx - 0.55*mw, my - mh), (mx, my - 1.05*mh),
            (mx + 0.55*mw, my - mh), (mx + mw, my),
            (mx + 0.55*mw, my + mh*openness), (mx, my + 1.05*mh*openness),
            (mx - 0.55*mw, my + mh*openness), (mx, my),
        ]
        array = np.asarray(pts, dtype=np.float32)
        array[:, 0] = np.clip(array[:, 0], 0, iw - 1)
        array[:, 1] = np.clip(array[:, 1], 0, ih - 1)
        return array

    def _smooth_target_points(self, points: np.ndarray, rect: Rect) -> np.ndarray:
        if (
            self._previous_target_points is None
            or self._previous_target_points.shape != points.shape
            or self._previous_target_rect is None
            or self._iou(rect, self._previous_target_rect) < 0.20
        ):
            smooth = points
        else:
            smooth = self._previous_target_points * 0.58 + points * 0.42
        self._previous_target_points = smooth.copy()
        self._previous_target_rect = rect
        return smooth

    @staticmethod
    def _color_match(source: np.ndarray, target: np.ndarray) -> np.ndarray:
        source_lab = cv2.cvtColor(source, cv2.COLOR_BGR2LAB).astype(np.float32)
        target_lab = cv2.cvtColor(target, cv2.COLOR_BGR2LAB).astype(np.float32)
        output = source_lab.copy()
        for channel in range(3):
            src_mean, src_std = cv2.meanStdDev(source_lab[:, :, channel])
            tgt_mean, tgt_std = cv2.meanStdDev(target_lab[:, :, channel])
            src_mean_f = float(src_mean.ravel()[0])
            src_std_f = max(float(src_std.ravel()[0]), 1.0)
            tgt_mean_f = float(tgt_mean.ravel()[0])
            tgt_std_f = max(float(tgt_std.ravel()[0]), 1.0)
            ratio = float(np.clip(tgt_std_f / src_std_f, 0.55, 1.80))
            output[:, :, channel] = (output[:, :, channel] - src_mean_f) * ratio + tgt_mean_f
        return cv2.cvtColor(np.clip(output, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)

    @staticmethod
    def _warp_triangle(
        source: np.ndarray,
        destination: np.ndarray,
        source_triangle: np.ndarray,
        destination_triangle: np.ndarray,
    ) -> None:
        src_rect = cv2.boundingRect(np.float32([source_triangle]))
        dst_rect = cv2.boundingRect(np.float32([destination_triangle]))
        if min(src_rect[2], src_rect[3], dst_rect[2], dst_rect[3]) <= 1:
            return
        sx, sy, sw, sh = src_rect
        dx, dy, dw, dh = dst_rect
        src_crop = source[sy : sy + sh, sx : sx + sw]
        dst_crop = destination[dy : dy + dh, dx : dx + dw]
        if src_crop.size == 0 or dst_crop.size == 0:
            return
        src_local = np.float32([(p[0] - sx, p[1] - sy) for p in source_triangle])
        dst_local = np.float32([(p[0] - dx, p[1] - dy) for p in destination_triangle])
        transform = cv2.getAffineTransform(src_local, dst_local)
        warped = cv2.warpAffine(
            src_crop, transform, (dw, dh), flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REFLECT_101,
        )
        mask = np.zeros((dh, dw, 3), dtype=np.float32)
        cv2.fillConvexPoly(mask, np.int32(dst_local), (1.0, 1.0, 1.0), lineType=cv2.LINE_AA)
        mixed = dst_crop.astype(np.float32) * (1.0 - mask) + warped.astype(np.float32) * mask
        destination[dy : dy + dh, dx : dx + dw] = np.clip(mixed, 0, 255).astype(np.uint8)

    @staticmethod
    def _triangle_indices(points: np.ndarray, shape: Sequence[int]) -> list[tuple[int, int, int]]:
        height, width = shape[:2]
        subdivision = cv2.Subdiv2D((0, 0, width, height))
        for point in points:
            try:
                subdivision.insert((float(point[0]), float(point[1])))
            except cv2.error:
                pass
        triangles: list[tuple[int, int, int]] = []
        p32 = points.astype(np.float32)
        for raw in subdivision.getTriangleList():
            triangle_points = np.array([[raw[0], raw[1]], [raw[2], raw[3]], [raw[4], raw[5]]], dtype=np.float32)
            indices: list[int] = []
            for triangle_point in triangle_points:
                distances = np.linalg.norm(p32 - triangle_point, axis=1)
                index = int(np.argmin(distances))
                if distances[index] <= 3.5:
                    indices.append(index)
            if len(indices) == 3 and len(set(indices)) == 3:
                candidate = tuple(indices)
                if candidate not in triangles:
                    triangles.append(candidate)
        return triangles

    @staticmethod
    def _soft_face_mask(points: np.ndarray, shape: Sequence[int], face_width: int) -> np.ndarray:
        mask = np.zeros(shape[:2], dtype=np.uint8)
        hull = cv2.convexHull(np.rint(points[:14]).astype(np.int32))
        cv2.fillConvexPoly(mask, hull, 255, lineType=cv2.LINE_AA)
        erode_px = max(1, int(face_width * 0.018))
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (erode_px * 2 + 1, erode_px * 2 + 1))
        mask = cv2.erode(mask, kernel, iterations=1)
        blur = max(9, int(face_width * 0.11))
        if blur % 2 == 0:
            blur += 1
        return cv2.GaussianBlur(mask, (blur, blur), 0)

    @staticmethod
    def _ellipse_mask(shape: Sequence[int], center: tuple[float, float], axes: tuple[float, float], blur: int) -> np.ndarray:
        mask = np.zeros(shape[:2], dtype=np.uint8)
        cx, cy = int(round(center[0])), int(round(center[1]))
        ax = max(1, int(round(axes[0])))
        ay = max(1, int(round(axes[1])))
        cv2.ellipse(mask, (cx, cy), (ax, ay), 0, 0, 360, 255, -1, lineType=cv2.LINE_AA)
        blur = max(3, blur | 1)
        return cv2.GaussianBlur(mask, (blur, blur), 0)

    def _restore_expression_detail(
        self, swapped: np.ndarray, target: np.ndarray, points: np.ndarray, rect: Rect
    ) -> np.ndarray:
        """Transfer target eyelid/mouth/crease detail without pasting target skin."""
        x, y, w, h = rect
        out = swapped.astype(np.float32)
        target_f = target.astype(np.float32)
        low = cv2.GaussianBlur(target_f, (0, 0), sigmaX=max(1.2, w * 0.012))
        detail = target_f - low

        masks = []
        for start in (20, 26):
            eye = points[start : start + 6]
            ex, ey = eye.mean(axis=0)
            ew = max(2.0, float(np.ptp(eye[:, 0])) * 0.70)
            eh = max(2.0, float(np.ptp(eye[:, 1])) * 1.10)
            masks.append(self._ellipse_mask(target.shape, (ex, ey), (ew, eh), max(5, int(w * 0.035))))

        mouth_pts = points[36:44]
        mx, my = mouth_pts.mean(axis=0)
        mw = max(3.0, float(np.ptp(mouth_pts[:, 0])) * 0.65)
        mh = max(2.0, float(np.ptp(mouth_pts[:, 1])) * 0.75)
        mouth_mask = self._ellipse_mask(target.shape, (mx, my), (mw, mh), max(7, int(w * 0.045)))
        masks.append(mouth_mask)

        crease_mask = np.zeros(target.shape[:2], dtype=np.uint8)
        brow_poly = np.rint(points[14:20]).astype(np.int32)
        if len(brow_poly) >= 3:
            cv2.fillConvexPoly(crease_mask, cv2.convexHull(brow_poly), 180, lineType=cv2.LINE_AA)
        cheek_y = int(y + h * 0.58)
        cv2.ellipse(crease_mask, (int(x + w*0.30), cheek_y), (max(2,int(w*0.16)), max(2,int(h*0.10))), 0, 0, 360, 90, -1)
        cv2.ellipse(crease_mask, (int(x + w*0.70), cheek_y), (max(2,int(w*0.16)), max(2,int(h*0.10))), 0, 0, 360, 90, -1)
        blur = max(5, int(w * 0.045)) | 1
        crease_mask = cv2.GaussianBlur(crease_mask, (blur, blur), 0)

        detail_alpha = crease_mask.astype(np.float32) / 255.0 * 0.28
        for m in masks[:2]:
            detail_alpha = np.maximum(detail_alpha, m.astype(np.float32) / 255.0 * 0.58)
        detail_alpha = np.maximum(detail_alpha, mouth_mask.astype(np.float32) / 255.0 * 0.72)
        out += detail * detail_alpha[:, :, None]

        face_gray = cv2.cvtColor(target, cv2.COLOR_BGR2GRAY)
        face_patch = face_gray[max(0,y):min(target.shape[0],y+h), max(0,x):min(target.shape[1],x+w)]
        threshold = float(np.percentile(face_patch, 28)) if face_patch.size else 70.0
        interior = (face_gray < threshold).astype(np.float32)
        interior *= mouth_mask.astype(np.float32) / 255.0
        interior = cv2.GaussianBlur(interior, (5, 5), 0)
        mouth_mix = np.clip(interior * 0.42, 0.0, 0.42)[:, :, None]
        out = out * (1.0 - mouth_mix) + target_f * mouth_mix
        return np.clip(out, 0, 255).astype(np.uint8)

    @staticmethod
    def _transfer_target_lighting(swapped: np.ndarray, target: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """Use target low-frequency luminance so the new face belongs in the shot."""
        s_lab = cv2.cvtColor(swapped, cv2.COLOR_BGR2LAB).astype(np.float32)
        t_lab = cv2.cvtColor(target, cv2.COLOR_BGR2LAB).astype(np.float32)
        target_l = cv2.GaussianBlur(t_lab[:, :, 0], (0, 0), 9.0)
        source_l = cv2.GaussianBlur(s_lab[:, :, 0], (0, 0), 9.0)
        delta = np.clip(target_l - source_l, -35.0, 35.0)
        alpha = (mask.astype(np.float32) / 255.0) * 0.62
        s_lab[:, :, 0] = np.clip(s_lab[:, :, 0] + delta * alpha, 0, 255)
        return cv2.cvtColor(s_lab.astype(np.uint8), cv2.COLOR_LAB2BGR)

    def swap_face(
        self,
        source_image: np.ndarray,
        target_frame: np.ndarray,
        source_rect: Optional[Rect] = None,
        target_rect: Optional[Rect] = None,
    ) -> tuple[np.ndarray, Optional[Rect]]:
        if source_rect is None:
            source_rect = self.detect_face(source_image)
        if source_rect is None:
            raise ValueError("No face detected in the source photo")

        target_rect = self.detect_face(target_frame, previous=target_rect)
        if target_rect is None:
            return target_frame.copy(), None

        source_points = self._landmarks(source_image, source_rect)
        target_points = self._smooth_target_points(self._landmarks(target_frame, target_rect), target_rect)

        sx, sy, sw, sh = source_rect
        tx, ty, tw, th = target_rect
        source_crop = source_image[sy : sy + sh, sx : sx + sw]
        target_crop = target_frame[ty : ty + th, tx : tx + tw]
        color_source = source_image.copy()
        if source_crop.size and target_crop.size:
            resized_target = cv2.resize(target_crop, (sw, sh), interpolation=cv2.INTER_AREA)
            color_source[sy : sy + sh, sx : sx + sw] = self._color_match(source_crop, resized_target)

        result = target_frame.copy()
        for i1, i2, i3 in self._triangle_indices(target_points, target_frame.shape):
            self._warp_triangle(
                color_source,
                result,
                source_points[[i1, i2, i3]],
                target_points[[i1, i2, i3]],
            )

        soft_mask = self._soft_face_mask(target_points, target_frame.shape, tw)
        result = self._transfer_target_lighting(result, target_frame, soft_mask)
        result = self._restore_expression_detail(result, target_frame, target_points, target_rect)

        alpha = (soft_mask.astype(np.float32) / 255.0)[:, :, None]
        feathered = np.clip(
            result.astype(np.float32) * alpha
            + target_frame.astype(np.float32) * (1.0 - alpha),
            0,
            255,
        ).astype(np.uint8)
        clone_mask = np.where(soft_mask > 32, 255, 0).astype(np.uint8)
        center = (int(tx + tw / 2), int(ty + th / 2))
        center = (
            min(max(center[0], 1), target_frame.shape[1] - 2),
            min(max(center[1], 1), target_frame.shape[0] - 2),
        )
        try:
            blended = cv2.seamlessClone(
                feathered, target_frame, clone_mask, center, cv2.MIXED_CLONE
            )
            blended = self._restore_expression_detail(
                blended, target_frame, target_points, target_rect
            )
        except cv2.error:
            blended = feathered
        return blended, target_rect

    @staticmethod
    def _ffmpeg_binary() -> str:
        return shutil.which("ffmpeg") or "ffmpeg"

    @classmethod
    def _open_ffmpeg_writer(
        cls, output_path: str, source_video: str, info: VideoInfo
    ) -> subprocess.Popen:
        ffmpeg = cls._ffmpeg_binary()
        command = [
            ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24",
            "-s:v", f"{info.width}x{info.height}", "-r", f"{info.fps:.6f}",
            "-i", "pipe:0", "-i", source_video,
            "-map", "0:v:0", "-map", "1:a:0?",
            "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", "-shortest", output_path,
        ]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "FFmpeg is missing from this APK. Rebuild with ffmpeg,av_codecs."
            ) from exc
        if process.stdin is None:
            process.kill()
            raise RuntimeError("FFmpeg encoder pipe could not be opened")
        return process

    @staticmethod
    def _finish_ffmpeg(process: subprocess.Popen) -> tuple[int, str]:
        if process.stdin is not None and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass
        timed_out = False
        try:
            code = process.wait(timeout=120)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            code = process.wait()
        stderr = b""
        if process.stderr is not None:
            try:
                stderr = process.stderr.read()
            except OSError:
                stderr = b""
        if timed_out:
            return code or 1, "FFmpeg timed out while finalizing the MP4"
        return code, stderr.decode("utf-8", errors="replace").strip()

    def process_video(
        self,
        source_path: str,
        video_path: str,
        output_path: str,
        progress_cb: Optional[ProgressCallback] = None,
        cancel_cb: Optional[CancelCallback] = None,
    ) -> tuple[bool, str]:
        source = cv2.imread(source_path, cv2.IMREAD_COLOR)
        if source is None:
            return False, "The source photo could not be read"
        source_rect = self.detect_face(source)
        if source_rect is None:
            return False, "No face found in the source photo. Use a clear front-facing photo."

        capture = cv2.VideoCapture(video_path)
        if not capture.isOpened():
            return False, "The selected video could not be opened"
        first_ok, first_frame = capture.read()
        if (
            not first_ok
            or first_frame is None
            or first_frame.size == 0
            or first_frame.ndim < 2
        ):
            capture.release()
            return False, "No video frames were decoded"

        height, width = first_frame.shape[:2]
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if width <= 0 or height <= 0:
            capture.release()
            return False, "The decoded video frame has invalid dimensions"
        if not np.isfinite(fps) or fps <= 1.0 or fps > 240.0:
            fps = 30.0
        total = max(total, 1)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        try:
            encoder = self._open_ffmpeg_writer(
                output_path, video_path, VideoInfo(width, height, fps, total)
            )
        except Exception as exc:
            capture.release()
            return False, str(exc)

        if progress_cb:
            progress_cb(
                "Face detected. Expression-aware blending and MP4 encoding...", 1
            )

        self._previous_target_points = None
        self._previous_target_rect = None
        target_rect: Optional[Rect] = None
        processed = 0
        cancelled = False
        encoder_error: Optional[str] = None

        try:
            frame = first_frame
            while True:
                if cancel_cb and cancel_cb():
                    cancelled = True
                    break
                try:
                    swapped, target_rect = self.swap_face(
                        source,
                        frame,
                        source_rect=source_rect,
                        target_rect=target_rect,
                    )
                except Exception:
                    swapped = frame
                    target_rect = None
                    self._previous_target_points = None
                    self._previous_target_rect = None

                try:
                    if encoder.stdin is None:
                        raise BrokenPipeError("FFmpeg frame pipe closed unexpectedly")
                    encoder.stdin.write(
                        np.ascontiguousarray(swapped, dtype=np.uint8).tobytes()
                    )
                except (BrokenPipeError, OSError) as exc:
                    encoder_error = f"FFmpeg stopped while encoding: {exc}"
                    break

                processed += 1
                if progress_cb and (
                    processed == 1
                    or processed % max(1, total // 100) == 0
                ):
                    percent = min(99, max(1, int(processed * 100 / total)))
                    progress_cb(
                        f"Expression-aware frame {processed} of about {total}",
                        percent,
                    )

                ok, frame = capture.read()
                if not ok:
                    break
        finally:
            capture.release()

        code, stderr = self._finish_ffmpeg(encoder)
        if cancelled:
            try:
                os.remove(output_path)
            except OSError:
                pass
            return False, "Cancelled"
        if encoder_error:
            try:
                os.remove(output_path)
            except OSError:
                pass
            if stderr:
                encoder_error += f" ({stderr[-500:]})"
            return False, encoder_error
        if processed == 0:
            try:
                os.remove(output_path)
            except OSError:
                pass
            return False, "No video frames were decoded"
        if code != 0:
            try:
                os.remove(output_path)
            except OSError:
                pass
            detail = stderr[-700:] if stderr else f"exit code {code}"
            return False, f"FFmpeg could not finalize the MP4: {detail}"
        if not os.path.exists(output_path) or os.path.getsize(output_path) < 2048:
            try:
                os.remove(output_path)
            except OSError:
                pass
            return False, "The output video was not created correctly"
        if progress_cb:
            progress_cb(
                "Expression-aware face swap complete. Audio preserved when available.",
                100,
            )
        return True, output_path


def process_video(
    source_path: str,
    video_path: str,
    output_path: str,
    progress_cb: Optional[ProgressCallback] = None,
    cancel_cb: Optional[CancelCallback] = None,
) -> tuple[bool, str]:
    return FaceSwapper().process_video(
        source_path,
        video_path,
        output_path,
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
    )
