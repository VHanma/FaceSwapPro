"""On-device face swap engine — OpenCV Delaunay + Seamless Clone. No API, 100% free."""

import cv2
import numpy as np


class FaceSwapper:
    def __init__(self):
        self.cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_alt2.xml'
        )

    # ── Face detection ───────────────────────────────────────────────────────
    def detect_face(self, img):
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        faces = self.cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(40, 40)
        )
        if len(faces) == 0:
            return None
        return max(faces, key=lambda f: f[2] * f[3])

    # ── Landmark estimation from bounding box ────────────────────────────────
    def _landmarks(self, rect, shape):
        x, y, w, h = rect
        H, W = shape[:2]
        pts = np.array([
            # jawline left → right
            [x,          y + h//5],
            [x,          y + h*2//5],
            [x,          y + h*3//5],
            [x + w//4,   y + h],
            [x + w//2,   y + h],
            [x + w*3//4, y + h],
            [x + w,      y + h*3//5],
            [x + w,      y + h*2//5],
            [x + w,      y + h//5],
            # eyes
            [x + w//4,   y + h*3//10],
            [x + w*3//4, y + h*3//10],
            # eyebrow centers
            [x + w//4,   y + h//6],
            [x + w*3//4, y + h//6],
            # nose bridge & tip
            [x + w//2,   y + h//3],
            [x + w//2,   y + h*9//20],
            # mouth
            [x + w//3,   y + h*13//18],
            [x + w*2//3, y + h*13//18],
            [x + w//2,   y + h*15//18],
            # forehead top
            [x + w//2,   y],
            [x + w//4,   y],
            [x + w*3//4, y],
        ], dtype=np.int32)
        pts[:, 0] = np.clip(pts[:, 0], 0, W - 1)
        pts[:, 1] = np.clip(pts[:, 1], 0, H - 1)
        return pts

    # ── Triangle warp ────────────────────────────────────────────────────────
    @staticmethod
    def _warp_tri(src, dst, t_src, t_dst):
        r1 = cv2.boundingRect(np.float32([t_src]))
        r2 = cv2.boundingRect(np.float32([t_dst]))

        if r1[2] == 0 or r1[3] == 0 or r2[2] == 0 or r2[3] == 0:
            return

        t1r = [(p[0] - r1[0], p[1] - r1[1]) for p in t_src]
        t2r = [(p[0] - r2[0], p[1] - r2[1]) for p in t_dst]

        crop = src[r1[1]:r1[1]+r1[3], r1[0]:r1[0]+r1[2]]
        if crop.size == 0:
            return

        M = cv2.getAffineTransform(np.float32(t1r), np.float32(t2r))
        warped = cv2.warpAffine(crop, M, (r2[2], r2[3]),
                                flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REFLECT_101)

        mask = np.zeros((r2[3], r2[2], 3), dtype=np.float32)
        cv2.fillConvexPoly(mask, np.int32(t2r), (1, 1, 1), 16)

        region = dst[r2[1]:r2[1]+r2[3], r2[0]:r2[0]+r2[2]]
        if region.shape[:2] != mask.shape[:2]:
            return

        blended = region.astype(np.float32) * (1 - mask) + warped.astype(np.float32) * mask
        dst[r2[1]:r2[1]+r2[3], r2[0]:r2[0]+r2[2]] = blended.astype(np.uint8)

    # ── Color / lighting match ───────────────────────────────────────────────
    @staticmethod
    def _color_match(src_crop, tgt_crop):
        """Transfer mean/std of target color to source crop."""
        result = np.copy(src_crop).astype(np.float32)
        for c in range(3):
            src_mean, src_std = src_crop[:,:,c].mean(), src_crop[:,:,c].std() + 1e-6
            tgt_mean, tgt_std = tgt_crop[:,:,c].mean(), tgt_crop[:,:,c].std() + 1e-6
            result[:,:,c] = (result[:,:,c] - src_mean) * (tgt_std / src_std) + tgt_mean
        return np.clip(result, 0, 255).astype(np.uint8)

    # ── Core swap ────────────────────────────────────────────────────────────
    def swap_face(self, source_img, target_frame,
                  src_rect=None, cached_src_pts=None):
        """
        Returns (swapped_frame, src_pts_cache).
        Pass cached_src_pts to skip repeated landmark calc on static source.
        """
        if src_rect is None:
            src_rect = self.detect_face(source_img)
        if src_rect is None:
            return target_frame, None

        tgt_rect = self.detect_face(target_frame)
        if tgt_rect is None:
            return target_frame, cached_src_pts

        if cached_src_pts is None:
            cached_src_pts = self._landmarks(src_rect, source_img.shape)
        src_pts = cached_src_pts
        tgt_pts = self._landmarks(tgt_rect, target_frame.shape)

        # Color-match source face patch to target
        sx, sy, sw, sh = src_rect
        tx, ty, tw, th = tgt_rect
        src_crop = source_img[sy:sy+sh, sx:sx+sw]
        tgt_crop = target_frame[ty:ty+th, tx:tx+tw]
        if src_crop.size > 0 and tgt_crop.size > 0:
            tgt_crop_rs = cv2.resize(tgt_crop, (sw, sh))
            src_matched = self._color_match(src_crop, tgt_crop_rs)
            matched_src = np.copy(source_img)
            matched_src[sy:sy+sh, sx:sx+sw] = src_matched
        else:
            matched_src = source_img

        # Convex hull
        hull_idx = cv2.convexHull(src_pts, returnPoints=False).flatten()
        src_hull = [src_pts[i] for i in hull_idx]
        tgt_hull = [tgt_pts[i] for i in hull_idx]

        result = np.copy(target_frame)

        # Delaunay on target hull
        H, W = target_frame.shape[:2]
        subdiv = cv2.Subdiv2D((0, 0, W, H))
        for p in tgt_hull:
            try:
                subdiv.insert((float(p[0]), float(p[1])))
            except Exception:
                pass

        for tri in subdiv.getTriangleList().astype(np.int32):
            pts2 = [[tri[0], tri[1]], [tri[2], tri[3]], [tri[4], tri[5]]]
            idx = []
            for pt in pts2:
                for i, h in enumerate(tgt_hull):
                    if abs(h[0]-pt[0]) < 2 and abs(h[1]-pt[1]) < 2:
                        idx.append(i)
                        break
            if len(idx) < 3:
                continue
            self._warp_tri(matched_src, result,
                           [src_hull[idx[0]], src_hull[idx[1]], src_hull[idx[2]]],
                           [tgt_hull[idx[0]], tgt_hull[idx[1]], tgt_hull[idx[2]]])

        # Seamless clone
        mask = np.zeros((H, W), dtype=np.uint8)
        cv2.fillConvexPoly(mask, np.int32([tgt_hull]), 255)
        r = cv2.boundingRect(np.array([tgt_hull], dtype=np.int32))
        cx = min(max(r[0] + r[2] // 2, 1), W - 2)
        cy = min(max(r[1] + r[3] // 2, 1), H - 2)
        try:
            final = cv2.seamlessClone(result, target_frame, mask, (cx, cy), cv2.NORMAL_CLONE)
        except Exception:
            final = result

        return final, src_pts

    # ── Video processor ──────────────────────────────────────────────────────
    def process_video(self, source_path, video_path, output_path, progress_cb=None):
        src = cv2.imread(source_path)
        if src is None:
            return False, "Cannot read source image"

        src_rect = self.detect_face(src)
        if src_rect is None:
            return False, "No face detected in source photo — use a clear front-facing photo"

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return False, "Cannot open target video"

        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
        fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
        W     = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        H     = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(output_path, fourcc, fps, (W, H))

        cached_pts = None
        n = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            swapped, cached_pts = self.swap_face(
                src, frame, src_rect=src_rect, cached_src_pts=cached_pts
            )
            writer.write(swapped)
            n += 1
            if progress_cb:
                progress_cb(int(n / total * 100), n, total)

        cap.release()
        writer.release()

        if n == 0:
            return False, "No frames written"
        return True, output_path
