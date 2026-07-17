"""Offline face-swap engine for FaceSwap Pro.

Uses OpenCV only. A source face is warped onto the largest face found in each
video frame, then blended with seamlessClone. No server, account, or API key.
"""

from __future__ import annotations

import os
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
    """Fast, dependency-light face swapping suitable for an Android build."""

    def __init__(self, detection_width: int = 640) -> None:
        self.detection_width = max(240, int(detection_width))
        cascade_path = _cascade_path()
        self.cascade = cv2.CascadeClassifier(cascade_path) if cascade_path else None
        if self.cascade is not None and self.cascade.empty():
            self.cascade = None

    def detect_face(self, image: np.ndarray, previous: Optional[Rect] = None) -> Optional[Rect]:
        """Return the largest detected face, preferring overlap with the last face."""
        if image is None or image.size == 0:
            return None

        height, width = image.shape[:2]
        scale = min(1.0, self.detection_width / float(max(width, 1)))
        if scale < 1.0:
            small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
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
            return self._iou(rect, previous) * 5.0 + (rect[2] * rect[3]) / float(width * height)

        return max(rects, key=score)

    @staticmethod
    def _skin_face_candidates(image: np.ndarray) -> list[Rect]:
        """Model-free backup used only when the bundled cascade is unavailable."""
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
    def _landmarks(rect: Rect, shape: Sequence[int]) -> np.ndarray:
        """Stable pseudo-landmarks derived from a detected face rectangle."""
        x, y, w, h = rect
        image_h, image_w = shape[:2]

        normalized = np.array(
            [
                (0.18, 0.12), (0.35, 0.04), (0.50, 0.01), (0.65, 0.04), (0.82, 0.12),
                (0.05, 0.28), (0.02, 0.48), (0.08, 0.70), (0.22, 0.88),
                (0.50, 0.98),
                (0.78, 0.88), (0.92, 0.70), (0.98, 0.48), (0.95, 0.28),
                (0.25, 0.30), (0.40, 0.30), (0.60, 0.30), (0.75, 0.30),
                (0.30, 0.40), (0.70, 0.40),
                (0.50, 0.36), (0.50, 0.56), (0.41, 0.62), (0.59, 0.62),
                (0.34, 0.73), (0.50, 0.70), (0.66, 0.73), (0.50, 0.82),
            ],
            dtype=np.float32,
        )
        pts = np.empty_like(normalized)
        pts[:, 0] = x + normalized[:, 0] * w
        pts[:, 1] = y + normalized[:, 1] * h
        pts[:, 0] = np.clip(pts[:, 0], 0, image_w - 1)
        pts[:, 1] = np.clip(pts[:, 1], 0, image_h - 1)
        return np.rint(pts).astype(np.int32)

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
            output[:, :, channel] = (
                (output[:, :, channel] - src_mean_f) * (tgt_std_f / src_std_f)
                + tgt_mean_f
            )
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
            src_crop,
            transform,
            (dw, dh),
            flags=cv2.INTER_LINEAR,
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
        for raw in subdivision.getTriangleList():
            triangle_points = np.array(
                [[raw[0], raw[1]], [raw[2], raw[3]], [raw[4], raw[5]]], dtype=np.float32
            )
            indices: list[int] = []
            for triangle_point in triangle_points:
                distances = np.linalg.norm(points.astype(np.float32) - triangle_point, axis=1)
                index = int(np.argmin(distances))
                if distances[index] <= 3.0:
                    indices.append(index)
            if len(indices) == 3 and len(set(indices)) == 3:
                candidate = tuple(indices)
                if candidate not in triangles:
                    triangles.append(candidate)
        return triangles

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

        source_points = self._landmarks(source_rect, source_image.shape)
        target_points = self._landmarks(target_rect, target_frame.shape)

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

        hull = cv2.convexHull(target_points)
        mask = np.zeros(target_frame.shape[:2], dtype=np.uint8)
        cv2.fillConvexPoly(mask, hull, 255, lineType=cv2.LINE_AA)
        mask = cv2.GaussianBlur(mask, (11, 11), 0)
        center = (int(tx + tw / 2), int(ty + th / 2))
        center = (
            min(max(center[0], 1), target_frame.shape[1] - 2),
            min(max(center[1], 1), target_frame.shape[0] - 2),
        )
        try:
            blended = cv2.seamlessClone(result, target_frame, mask, center, cv2.NORMAL_CLONE)
        except cv2.error:
            alpha = (mask.astype(np.float32) / 255.0)[:, :, None]
            blended = np.clip(result * alpha + target_frame * (1.0 - alpha), 0, 255).astype(np.uint8)
        return blended, target_rect

    @staticmethod
    def _open_writer(path: str, info: VideoInfo) -> cv2.VideoWriter:
        codec_candidates = ("mp4v", "avc1", "H264")
        for codec in codec_candidates:
            writer = cv2.VideoWriter(
                path,
                cv2.VideoWriter_fourcc(*codec),
                info.fps,
                (info.width, info.height),
            )
            if writer.isOpened():
                return writer
            writer.release()
        raise RuntimeError("This Android OpenCV build could not create an MP4 video")

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

        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if width <= 0 or height <= 0:
            capture.release()
            return False, "The selected video has invalid dimensions"
        if not np.isfinite(fps) or fps <= 1.0:
            fps = 30.0
        total = max(total, 1)

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        try:
            writer = self._open_writer(output_path, VideoInfo(width, height, fps, total))
        except Exception as exc:
            capture.release()
            return False, str(exc)

        if progress_cb:
            progress_cb("Face detected. Processing video on this phone...", 1)

        target_rect: Optional[Rect] = None
        processed = 0
        try:
            while True:
                if cancel_cb and cancel_cb():
                    return False, "Cancelled"
                ok, frame = capture.read()
                if not ok:
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
                writer.write(swapped)
                processed += 1
                if progress_cb and (processed == 1 or processed % max(1, total // 100) == 0):
                    percent = min(99, max(1, int(processed * 100 / total)))
                    progress_cb(f"Processing frame {processed} of about {total}", percent)
        finally:
            capture.release()
            writer.release()

        if processed == 0:
            try:
                os.remove(output_path)
            except OSError:
                pass
            return False, "No video frames were decoded"
        if not os.path.exists(output_path) or os.path.getsize(output_path) < 1024:
            return False, "The output video was not created correctly"
        if progress_cb:
            progress_cb("Face swap complete", 100)
        return True, output_path


def process_video(
    source_path: str,
    video_path: str,
    output_path: str,
    progress_cb: Optional[ProgressCallback] = None,
    cancel_cb: Optional[CancelCallback] = None,
) -> tuple[bool, str]:
    """Module-level API used by the Kivy UI."""
    return FaceSwapper().process_video(
        source_path,
        video_path,
        output_path,
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
    )
