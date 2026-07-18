"""Patch FaceSwap Pro to derive video dimensions from the first decoded frame."""

from pathlib import Path


path = Path("faceswap.py")
text = path.read_text(encoding="utf-8")

old_probe = """        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        if width <= 0 or height <= 0:
            capture.release()
            return False, \"The selected video has invalid dimensions\"
        if not np.isfinite(fps) or fps <= 1.0:
            fps = 30.0
        total = max(total, 1)
"""

new_probe = """        fps = float(capture.get(cv2.CAP_PROP_FPS))
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))

        # Android codecs may report zero metadata until a frame is decoded.
        first_ok, first_frame = capture.read()
        if not first_ok or first_frame is None or first_frame.size == 0:
            capture.release()
            return False, \"The selected video could not decode its first frame\"
        height, width = first_frame.shape[:2]

        if not np.isfinite(fps) or fps <= 1.0:
            fps = 30.0
        total = max(total, 1)
"""

if old_probe not in text:
    raise SystemExit("Old video metadata block was not found")
text = text.replace(old_probe, new_probe, 1)

old_loop = """        target_rect: Optional[Rect] = None
        processed = 0
        close_error: Optional[Exception] = None
        try:
            while True:
                if cancel_cb and cancel_cb():
                    return False, \"Cancelled\"
                ok, frame = capture.read()
                if not ok:
                    break
"""

new_loop = """        target_rect: Optional[Rect] = None
        processed = 0
        close_error: Optional[Exception] = None
        pending_frame = first_frame
        try:
            while True:
                if cancel_cb and cancel_cb():
                    return False, \"Cancelled\"
                if pending_frame is not None:
                    frame = pending_frame
                    pending_frame = None
                else:
                    ok, frame = capture.read()
                    if not ok:
                        break
"""

if old_loop not in text:
    raise SystemExit("Old video read loop was not found")
text = text.replace(old_loop, new_loop, 1)

path.write_text(text, encoding="utf-8")
print("Patched Android video dimensions to use the first decoded frame")
