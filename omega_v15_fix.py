"""Final v1.5 classifier refinement for flat-color cartoons and line art."""
from __future__ import annotations

import cv2
import numpy as np

from omega_v15 import OmegaFaceSwapper as OmegaV15


class OmegaFaceSwapper(OmegaV15):
    @staticmethod
    def _illustrated_score(roi: np.ndarray) -> float:
        if roi is None or roi.size == 0:
            return 0.0
        thumb = cv2.resize(roi, (96, 96), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(thumb, cv2.COLOR_BGR2GRAY)
        edge_density = float(np.mean(cv2.Canny(gray, 45, 120) > 0))
        smooth = cv2.GaussianBlur(gray, (0, 0), 1.2)
        residual = float(
            np.mean(np.abs(gray.astype(np.float32) - smooth.astype(np.float32)))
        )
        quantized = (thumb // 32).reshape(-1, 3)
        palette_bins = int(np.unique(quantized, axis=0).shape[0])
        palette_sparsity = max(0.0, 1.0 - palette_bins / 90.0)
        return (
            edge_density * 2.8
            + max(0.0, 1.0 - residual / 16.0) * 0.35
            + palette_sparsity * 0.55
        )
