"""Expression-aware runtime for FaceSwap Pro.

This package intentionally shadows the legacy faceswap.py module. It loads that
stable OpenCV implementation as a base, then adds expression transfer so the
source identity follows the target video's mouth, eyes, eyebrows, cheeks and
jaw instead of behaving like a rigid paper mask.

Still fully offline. No server, login, API key or upload.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from typing import Optional, Sequence

import cv2
import numpy as np


# Load the existing, already-working engine under a private module name. Python
# prefers this faceswap package over the sibling faceswap.py, so the UI can keep
# using `from faceswap import process_video` without any main.py changes.
_BASE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "faceswap.py",
)
_SPEC = importlib.util.spec_from_file_location("_faceswappro_legacy_engine", _BASE_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError("Could not load the FaceSwap Pro base engine")

_BASE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BASE
_SPEC.loader.exec_module(_BASE)

BaseFaceSwapper = _BASE.FaceSwapper
ProgressCallback = _BASE.ProgressCallback
CancelCallback = _BASE.CancelCallback
Rect = _BASE.Rect


class ExpressionFaceSwapper(BaseFaceSwapper):
    """OpenCV-only expression-aware layer on top of the stable base swapper."""

    def __init__(
        self,
        detection_width: int = 640,
        expression_size: int = 192,
    ) -> None:
        super().__init__(detection_width=detection_width)
        self.expression_size = max(128, min(320, int(expression_size)))
        self._prev_target_gray: Optional[np.ndarray] = None
        self._prev_swapped_small: Optional[np.ndarray] = None
        self._prev_rect: Optional[Rect] = None

    def _reset_expression_state(self) -> None:
        self._prev_target_gray = None
        self._prev_swapped_small = None
        self._prev_rect = None

    @staticmethod
    def _clip_rect(rect: Rect, shape: Sequence[int]) -> Rect:
        x, y, w, h = rect
        image_h, image_w = shape[:2]
        x = max(0, min(int(x), max(0, image_w - 2)))
        y = max(0, min(int(y), max(0, image_h - 2)))
        w = max(2, min(int(w), image_w - x))
        h = max(2, min(int(h), image_h - y))
        return x, y, w, h

    @staticmethod
    def _smooth_rect(previous: Rect, current: Rect) -> Rect:
        # Removes detector jitter while keeping real head motion responsive.
        current_weight = 0.68
        old_weight = 1.0 - current_weight
        return tuple(
            int(round(previous[i] * old_weight + current[i] * current_weight))
            for i in range(4)
        )

    def detect_face(
        self,
        image: np.ndarray,
        previous: Optional[Rect] = None,
    ) -> Optional[Rect]:
        rect = super().detect_face(image, previous=previous)
        if rect is None:
            return None
        if previous is not None and self._iou(rect, previous) >= 0.18:
            rect = self._smooth_rect(previous, rect)
        return self._clip_rect(rect, image.shape)

    @staticmethod
    def _ellipse_mask(
        shape: Sequence[int],
        specs: list[tuple[float, float, float, float, int]],
    ) -> np.ndarray:
        h, w = shape[:2]
        mask = np.zeros((h, w), dtype=np.uint8)

        for cx, cy, rx, ry, value in specs:
            center = (int(round(cx * w)), int(round(cy * h)))
            axes = (
                max(1, int(round(rx * w))),
                max(1, int(round(ry * h))),
            )
            cv2.ellipse(
                mask,
                center,
                axes,
                0,
                0,
                360,
                int(value),
                -1,
                cv2.LINE_AA,
            )

        return mask

    def _expression_mask(self, shape: Sequence[int]) -> np.ndarray:
        """Soft zones where real target expression detail must stay alive."""
        h, w = shape[:2]
        mask = self._ellipse_mask(
            shape,
            [
                # Eyebrows: raises, drops and asymmetric brow motion.
                (0.31, 0.285, 0.17, 0.075, 235),
                (0.69, 0.285, 0.17, 0.075, 235),
                # Eyes/eyelids: blinks, squints, widening.
                (0.31, 0.405, 0.16, 0.105, 255),
                (0.69, 0.405, 0.16, 0.105, 255),
                # Nose and nearby cheeks: scrunches and smile creases.
                (0.50, 0.555, 0.15, 0.16, 180),
                (0.37, 0.565, 0.10, 0.10, 120),
                (0.63, 0.565, 0.10, 0.10, 120),
                # Mouth/jaw: speech, open mouth, smiles, frowns, jaw drop.
                (0.50, 0.755, 0.31, 0.17, 255),
                (0.50, 0.865, 0.22, 0.10, 150),
            ],
        )

        blur = max(5, int(round(min(h, w) * 0.055)))
        if blur % 2 == 0:
            blur += 1
        return cv2.GaussianBlur(mask, (blur, blur), 0)

    def _face_blend_mask(self, shape: Sequence[int]) -> np.ndarray:
        """Feather expression changes so no rectangular crop edge appears."""
        h, w = shape[:2]
        mask = self._ellipse_mask(
            shape,
            [(0.50, 0.52, 0.47, 0.49, 255)],
        )
        blur = max(7, int(round(min(h, w) * 0.065)))
        if blur % 2 == 0:
            blur += 1
        return cv2.GaussianBlur(mask, (blur, blur), 0)

    def _transfer_expression_details(
        self,
        swapped_face: np.ndarray,
        target_face: np.ndarray,
    ) -> np.ndarray:
        """Restore expression structure without restoring the whole target face.

        Most visible movement of lips, eyelids, eyebrows and wrinkles lives in
        luminance. We transfer that locally while keeping source-face color
        dominant, so expressions move but the identity does not simply revert.
        """
        if swapped_face.size == 0 or target_face.size == 0:
            return swapped_face

        if target_face.shape[:2] != swapped_face.shape[:2]:
            target_face = cv2.resize(
                target_face,
                (swapped_face.shape[1], swapped_face.shape[0]),
                interpolation=cv2.INTER_LINEAR,
            )

        try:
            swap_lab = cv2.cvtColor(swapped_face, cv2.COLOR_BGR2LAB)
            target_lab = cv2.cvtColor(target_face, cv2.COLOR_BGR2LAB)
            expression_lab = swap_lab.copy()

            # Real opening/closing, brow position, eye shape and skin creases.
            expression_lab[:, :, 0] = target_lab[:, :, 0]

            # Small chroma carry prevents dead/plastic lips and eyelids while
            # leaving the source image's coloring strongly dominant.
            for channel in (1, 2):
                expression_lab[:, :, channel] = np.clip(
                    swap_lab[:, :, channel].astype(np.float32) * 0.84
                    + target_lab[:, :, channel].astype(np.float32) * 0.16,
                    0,
                    255,
                ).astype(np.uint8)

            expression_face = cv2.cvtColor(
                expression_lab,
                cv2.COLOR_LAB2BGR,
            )
        except cv2.error:
            expression_face = target_face

        alpha = (
            self._expression_mask(swapped_face.shape).astype(np.float32)
            / 255.0
        )[:, :, None]
        alpha *= 0.88

        return np.clip(
            swapped_face.astype(np.float32) * (1.0 - alpha)
            + expression_face.astype(np.float32) * alpha,
            0,
            255,
        ).astype(np.uint8)

    def _follow_expression_motion(
        self,
        swapped_face: np.ndarray,
        target_face: np.ndarray,
        rect: Rect,
    ) -> np.ndarray:
        """Move source texture with target mouth/eye/brow/jaw optical flow.

        Every detected face is normalized to a small work canvas. This removes
        most whole-head translation/scale, leaving dense optical flow focused
        on local expression changes. Only one small previous frame is retained.
        """
        if swapped_face.size == 0 or target_face.size == 0:
            return swapped_face

        size = self.expression_size
        target_small = cv2.resize(
            target_face,
            (size, size),
            interpolation=cv2.INTER_AREA,
        )
        swapped_small = cv2.resize(
            swapped_face,
            (size, size),
            interpolation=cv2.INTER_LINEAR,
        )
        gray = cv2.cvtColor(target_small, cv2.COLOR_BGR2GRAY)

        history_ok = (
            self._prev_target_gray is not None
            and self._prev_swapped_small is not None
            and self._prev_rect is not None
            and self._iou(rect, self._prev_rect) >= 0.12
        )

        if not history_ok:
            self._prev_target_gray = gray.copy()
            self._prev_swapped_small = swapped_small.copy()
            self._prev_rect = rect
            return swapped_face

        try:
            flow = cv2.calcOpticalFlowFarneback(
                self._prev_target_gray,
                gray,
                None,
                0.5,
                3,
                15,
                3,
                5,
                1.2,
                0,
            )

            grid_x, grid_y = np.meshgrid(
                np.arange(size, dtype=np.float32),
                np.arange(size, dtype=np.float32),
            )

            moved_previous = cv2.remap(
                self._prev_swapped_small,
                grid_x - flow[:, :, 0],
                grid_y - flow[:, :, 1],
                interpolation=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REFLECT_101,
            )

            magnitude = cv2.magnitude(flow[:, :, 0], flow[:, :, 1])
            magnitude = cv2.GaussianBlur(magnitude, (0, 0), 1.2)

            # Static areas favor the fresh source fit. Moving feature areas
            # increasingly carry the already-swapped texture with the motion.
            weight = np.clip(magnitude / 5.0, 0.0, 1.0)
            weight = (0.16 + weight * 0.54)[:, :, None].astype(np.float32)

            mixed_small = np.clip(
                swapped_small.astype(np.float32) * (1.0 - weight)
                + moved_previous.astype(np.float32) * weight,
                0,
                255,
            ).astype(np.uint8)

            output = cv2.resize(
                mixed_small,
                (swapped_face.shape[1], swapped_face.shape[0]),
                interpolation=cv2.INTER_LINEAR,
            )

            self._prev_target_gray = gray.copy()
            self._prev_swapped_small = mixed_small.copy()
            self._prev_rect = rect
            return output

        except cv2.error:
            self._prev_target_gray = gray.copy()
            self._prev_swapped_small = swapped_small.copy()
            self._prev_rect = rect
            return swapped_face

    def swap_face(
        self,
        source_image: np.ndarray,
        target_frame: np.ndarray,
        source_rect: Optional[Rect] = None,
        target_rect: Optional[Rect] = None,
    ) -> tuple[np.ndarray, Optional[Rect]]:
        """Do the identity swap, then make it follow the target expression."""
        base_swapped, detected_rect = super().swap_face(
            source_image,
            target_frame,
            source_rect=source_rect,
            target_rect=target_rect,
        )

        if detected_rect is None:
            self._reset_expression_state()
            return base_swapped, None

        rect = self._clip_rect(detected_rect, target_frame.shape)
        x, y, w, h = rect
        target_face = target_frame[y : y + h, x : x + w].copy()
        swapped_face = base_swapped[y : y + h, x : x + w].copy()

        if target_face.size == 0 or swapped_face.size == 0:
            self._reset_expression_state()
            return base_swapped, rect

        # A detector jump means a different face/scene. Never drag old motion
        # history onto it.
        if self._prev_rect is not None and self._iou(rect, self._prev_rect) < 0.08:
            self._reset_expression_state()

        animated = self._follow_expression_motion(
            swapped_face,
            target_face,
            rect,
        )
        expressive = self._transfer_expression_details(
            animated,
            target_face,
        )

        alpha = (
            self._face_blend_mask(swapped_face.shape).astype(np.float32)
            / 255.0
        )[:, :, None]

        final_face = np.clip(
            swapped_face.astype(np.float32) * (1.0 - alpha)
            + expressive.astype(np.float32) * alpha,
            0,
            255,
        ).astype(np.uint8)

        base_swapped[y : y + h, x : x + w] = final_face

        # The next frame moves the final identity texture, not the raw target.
        try:
            self._prev_swapped_small = cv2.resize(
                final_face,
                (self.expression_size, self.expression_size),
                interpolation=cv2.INTER_LINEAR,
            )
        except cv2.error:
            pass

        return base_swapped, rect

    def process_video(
        self,
        source_path: str,
        video_path: str,
        output_path: str,
        progress_cb: Optional[ProgressCallback] = None,
        cancel_cb: Optional[CancelCallback] = None,
    ) -> tuple[bool, str]:
        self._reset_expression_state()
        try:
            return super().process_video(
                source_path,
                video_path,
                output_path,
                progress_cb=progress_cb,
                cancel_cb=cancel_cb,
            )
        finally:
            self._reset_expression_state()


def process_video(
    source_path: str,
    video_path: str,
    output_path: str,
    progress_cb: Optional[ProgressCallback] = None,
    cancel_cb: Optional[CancelCallback] = None,
) -> tuple[bool, str]:
    """Kivy-facing expression-aware processing API."""
    return ExpressionFaceSwapper().process_video(
        source_path,
        video_path,
        output_path,
        progress_cb=progress_cb,
        cancel_cb=cancel_cb,
    )
