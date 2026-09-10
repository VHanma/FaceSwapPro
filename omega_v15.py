"""Omega v1.5 sharp + illustrated face engine.

Geometry is temporally stabilized, but rendered pixels are never averaged across
frames. This avoids the cumulative blur in v1.4. A dedicated anime LBP cascade
plus a color-agnostic contour/symmetry fallback adds illustrated face support.
"""
from __future__ import annotations

import os
from typing import Optional, Sequence

import cv2
import numpy as np

from faceswap_engine import FaceSwapper as BaseFaceSwapper, Rect
from omega_engine import OmegaFaceSwapper as OmegaV14


def _anime_cascade_path() -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "lbpcascade_animeface.xml")
    return path if os.path.exists(path) else ""


class OmegaFaceSwapper(OmegaV14):
    """v1.5 renderer: sharp full-res identity warp + cartoon/anime detection."""

    def __init__(self, detection_width: int = 1024) -> None:
        super().__init__(detection_width=detection_width)
        path = _anime_cascade_path()
        self.anime_cascade = cv2.CascadeClassifier(path) if path else None
        if self.anime_cascade is not None and self.anime_cascade.empty():
            self.anime_cascade = None
        self._omega_detect_interval = 3
        self._v15_source_key = None
        self._v15_source = None
        self._v15_source_points = None
        self._v15_source_rect = None
        self._v15_source_illustrated = False

    @staticmethod
    def _clip(rect: Rect, shape: Sequence[int]) -> Rect:
        x, y, w, h = rect
        ih, iw = shape[:2]
        x = max(0, min(int(x), max(0, iw - 1)))
        y = max(0, min(int(y), max(0, ih - 1)))
        return x, y, max(1, min(int(w), iw - x)), max(1, min(int(h), ih - y))

    def _anime_faces(self, image: np.ndarray) -> list[Rect]:
        if self.anime_cascade is None or image is None or image.size == 0:
            return []
        h, w = image.shape[:2]
        scale = min(1.0, self.detection_width / float(max(1, w)))
        small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else image
        gray = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
        minimum = max(28, int(min(small.shape[:2]) * 0.075))
        try:
            found = self.anime_cascade.detectMultiScale(gray, 1.08, 4, minSize=(minimum, minimum))
        except cv2.error:
            return []
        inv = 1.0 / scale
        return [(int(x*inv), int(y*inv), int(fw*inv), int(fh*inv)) for x, y, fw, fh in found]

    @staticmethod
    def _cartoon_score(roi: np.ndarray) -> float:
        if roi is None or roi.size == 0:
            return -1.0
        thumb = cv2.resize(roi, (96, 96), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(thumb, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 40, 120)
        symmetry = 1.0 - float(np.mean(np.abs(gray[:, :48].astype(np.float32) - np.fliplr(gray[:, 48:]).astype(np.float32))) / 255.0)
        return symmetry * 1.7 + float(np.mean(edges[16:55] > 0)) * 4.2 + float(np.mean(edges[54:86] > 0)) * 1.5

    def _generic_cartoon_faces(self, image: np.ndarray) -> list[Rect]:
        iw = image.shape[1]
        scale = min(1.0, 420.0 / float(max(1, iw)))
        small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else image
        gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        edges = cv2.Canny(gray, 35, 110)
        joined = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)), iterations=2)
        contours, _ = cv2.findContours(joined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        sh, sw = small.shape[:2]
        scored = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = w * h / float(max(1, sw * sh))
            ratio = w / float(max(1, h))
            if area < 0.018 or area > 0.72 or not 0.55 <= ratio <= 1.55 or min(w, h) < 42:
                continue
            px, py = int(w*.08), int(h*.07)
            rx, ry = max(0, x-px), max(0, y-py)
            rw, rh = min(sw-rx, w+2*px), min(sh-ry, h+2*py)
            score = self._cartoon_score(small[ry:ry+rh, rx:rx+rw])
            if score >= 1.45:
                inv = 1.0 / scale
                scored.append((score, (int(rx*inv), int(ry*inv), int(rw*inv), int(rh*inv))))
        scored.sort(reverse=True, key=lambda item: item[0])
        return [r for _, r in scored[:6]]

    def detect_face(self, image: np.ndarray, previous: Optional[Rect] = None) -> Optional[Rect]:
        candidates = []
        human = BaseFaceSwapper.detect_face(self, image, previous=None)
        if human is not None:
            candidates.append(human)
        candidates.extend(self._anime_faces(image))
        if not candidates:
            candidates.extend(self._generic_cartoon_faces(image))
        if not candidates:
            return previous
        def score(r: Rect) -> float:
            overlap = self._iou(r, previous) if previous else 0.0
            area = r[2]*r[3] / float(max(1, image.shape[0]*image.shape[1]))
            return overlap*5.0 + area
        return max(candidates, key=score)

    @staticmethod
    def _illustrated_score(roi: np.ndarray) -> float:
        thumb = cv2.resize(roi, (96, 96), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(thumb, cv2.COLOR_BGR2GRAY)
        edge_density = float(np.mean(cv2.Canny(gray, 45, 120) > 0))
        smooth = cv2.GaussianBlur(gray, (0, 0), 1.2)
        residual = float(np.mean(np.abs(gray.astype(np.float32)-smooth.astype(np.float32))))
        return edge_density*2.8 + max(0.0, 1.0-residual/16.0)*.35

    def _is_illustrated(self, image: np.ndarray, rect: Rect) -> bool:
        x, y, w, h = self._clip(rect, image.shape)
        roi = image[y:y+h, x:x+w]
        return bool(roi.size and self._illustrated_score(roi) >= .67)

    @staticmethod
    def _feature(roi: np.ndarray):
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        score = cv2.Canny(gray, 35, 110).astype(np.float32)/255.0*2.2 + (1.0-gray.astype(np.float32)/255.0)*.55
        score = cv2.GaussianBlur(score, (5, 5), 0)
        threshold = float(np.percentile(score, 72))
        weights = np.where(score >= threshold, score-threshold+.05, 0)
        h, w = gray.shape[:2]
        total = float(weights.sum())
        if total <= 1e-6:
            return w*.5, h*.5, w*.22, h*.10
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        cx, cy = float((xx*weights).sum()/total), float((yy*weights).sum()/total)
        sx = float(np.sqrt(max(1, (((xx-cx)**2)*weights).sum()/total)))
        sy = float(np.sqrt(max(1, (((yy-cy)**2)*weights).sum()/total)))
        return cx, cy, sx, sy

    def _cartoon_landmarks(self, image: np.ndarray, rect: Rect) -> np.ndarray:
        x, y, w, h = rect
        ih, iw = image.shape[:2]
        eyes = []
        for left in (True, False):
            f0, f1 = ((.06,.49) if left else (.51,.94))
            rx, ry, rw, rh = self._clip((int(x+f0*w), int(y+.14*h), int((f1-f0)*w), int(.40*h)), image.shape)
            cx, cy, sx, sy = self._feature(image[ry:ry+rh, rx:rx+rw])
            eyes.append((rx+cx, ry+cy, float(np.clip(sx*1.45,w*.07,w*.175)), float(np.clip(sy*1.05,h*.025,h*.105))))
        lex, ley, lew, leh = eyes[0]
        rex, rey, rew, reh = eyes[1]
        rx, ry, rw, rh = self._clip((int(x+.17*w), int(y+.52*h), int(.66*w), int(.37*h)), image.shape)
        mcx, mcy, msx, msy = self._feature(image[ry:ry+rh, rx:rx+rw])
        mx, my = rx+mcx, ry+mcy
        mw, mh = float(np.clip(msx*1.55,w*.11,w*.31)), float(np.clip(msy*1.05,h*.018,h*.12))
        openness = float(np.clip(mh/max(1,h*.09), .18, 1.35))
        pts = [
            (x+.18*w,y+.11*h),(x+.34*w,y+.035*h),(x+.50*w,y+.015*h),(x+.66*w,y+.035*h),(x+.82*w,y+.11*h),
            (x+.055*w,y+.30*h),(x+.025*w,y+.50*h),(x+.085*w,y+.72*h),(x+.24*w,y+.90*h),(x+.50*w,y+.985*h),
            (x+.76*w,y+.90*h),(x+.915*w,y+.72*h),(x+.975*w,y+.50*h),(x+.945*w,y+.30*h),
            (lex-1.22*lew,ley-1.45*leh),(lex,ley-1.62*leh),(lex+1.22*lew,ley-1.45*leh),
            (rex-1.22*rew,rey-1.45*reh),(rex,rey-1.62*reh),(rex+1.22*rew,rey-1.45*reh),
            (lex-lew,ley),(lex-.5*lew,ley-leh),(lex+.5*lew,ley-leh),(lex+lew,ley),(lex+.5*lew,ley+leh),(lex-.5*lew,ley+leh),
            (rex-rew,rey),(rex-.5*rew,rey-reh),(rex+.5*rew,rey-reh),(rex+rew,rey),(rex+.5*rew,rey+reh),(rex-.5*rew,rey+reh),
            (x+.50*w,y+.36*h),(x+.46*w,y+.55*h),(x+.54*w,y+.55*h),(x+.50*w,y+.64*h),
            (mx-mw,my),(mx-.55*mw,my-mh),(mx,my-1.05*mh),(mx+.55*mw,my-mh),(mx+mw,my),
            (mx+.55*mw,my+mh*openness),(mx,my+1.05*mh*openness),(mx-.55*mw,my+mh*openness),(mx,my),
        ]
        arr = np.asarray(pts, np.float32)
        arr[:,0], arr[:,1] = np.clip(arr[:,0],0,iw-1), np.clip(arr[:,1],0,ih-1)
        return arr

    def _adaptive_landmarks(self, image: np.ndarray, rect: Rect):
        illustrated = self._is_illustrated(image, rect)
        return (self._cartoon_landmarks(image, rect), True) if illustrated else (BaseFaceSwapper._landmarks(self, image, rect), False)

    def _geometry(self, frame: np.ndarray, hint: Optional[Rect]):
        gray = self._omega_gray(frame)
        old = self._omega_prev_points.copy() if self._omega_prev_points is not None else None
        tracked, conf = self._omega_track_points(gray)
        force = self._omega_prev_rect is None or self._omega_frame_index % self._omega_detect_interval == 0 or conf < .70 or self._omega_lost_frames > 0
        measured = rect = None
        illustrated = False
        if force:
            rect = self.detect_face(frame, hint or self._omega_prev_rect)
            if rect is not None:
                measured, illustrated = self._adaptive_landmarks(frame, rect)
        if measured is not None and tracked is not None and measured.shape == tracked.shape:
            weight = float(np.clip(.12+conf*.26,.12,.38))
            points = measured*(1-weight)+tracked*weight
            self._omega_lost_frames = 0
        elif measured is not None:
            points = measured
            self._omega_lost_frames = 0
        elif tracked is not None and conf >= .58 and self._omega_lost_frames < 7:
            points = tracked
            rect = self._omega_rect_from_points(points, frame.shape)
            illustrated = self._is_illustrated(frame, rect)
            self._omega_lost_frames += 1
        else:
            self._omega_prev_gray = gray
            self._omega_lost_frames += 1
            self._omega_frame_index += 1
            return None, None, illustrated
        motion = self._omega_motion(old, points, float(rect[2]))
        if old is not None and old.shape == points.shape:
            fresh = float(np.clip(.46+motion*4.8,.46,.90))
            points = old*(1-fresh)+points*fresh
        points[:,0], points[:,1] = np.clip(points[:,0],0,frame.shape[1]-1), np.clip(points[:,1],0,frame.shape[0]-1)
        rect = self._omega_rect_from_points(points, frame.shape)
        self._omega_prev_gray, self._omega_prev_points, self._omega_prev_rect = gray, points.copy(), rect
        self._omega_frame_index += 1
        return points, rect, illustrated

    @staticmethod
    def _upscale_source(image: np.ndarray, rect: Rect):
        x,y,w,h = rect
        maxdim = max(image.shape[:2])
        scale = min(3.0, 480.0/max(1,w), 2200.0/max(1,maxdim)) if w < 480 and maxdim < 2200 else 1.0
        if scale <= 1.03:
            return image, rect
        out = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_LANCZOS4)
        return out, (int(x*scale),int(y*scale),int(w*scale),int(h*scale))

    def _prepare_source(self, image: np.ndarray, rect: Rect):
        key = (id(image), image.shape, rect)
        if key != self._v15_source_key:
            image2, rect2 = self._upscale_source(image, rect)
            illustrated = self._is_illustrated(image2, rect2)
            x,y,w,h = self._clip(rect2, image2.shape)
            out = image2.copy()
            roi = out[y:y+h,x:x+w]
            if roi.size:
                low = cv2.GaussianBlur(roi,(0,0),.62 if illustrated else .72)
                out[y:y+h,x:x+w] = cv2.addWeighted(roi,1.26 if illustrated else 1.30,low,-.26 if illustrated else -.30,0)
            points,_ = self._adaptive_landmarks(out,rect2)
            self._v15_source_key, self._v15_source, self._v15_source_points, self._v15_source_rect = key,out,points,rect2
            self._v15_source_illustrated = illustrated
        return self._v15_source,self._v15_source_points,self._v15_source_rect,self._v15_source_illustrated

    @staticmethod
    def _affine(src: np.ndarray, dst: np.ndarray):
        idx = np.array([0,2,4,6,8,10,12,20,23,26,29,32,35,36,40],np.int32)
        idx = idx[idx < min(len(src),len(dst))]
        try:
            matrix, inliers = cv2.estimateAffinePartial2D(src[idx],dst[idx],method=cv2.LMEDS)
        except cv2.error:
            return None
        return matrix.astype(np.float32) if matrix is not None and (inliers is None or float(np.mean(inliers)) >= .45) else None

    @staticmethod
    def _transform(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
        return np.c_[points.astype(np.float32),np.ones(len(points),np.float32)] @ matrix.T

    @staticmethod
    def _feature_rect(points: np.ndarray, shape, pad=.32):
        x0,y0 = np.min(points,axis=0); x1,y1 = np.max(points,axis=0)
        w,h = max(4,float(x1-x0)),max(4,float(y1-y0))
        return OmegaFaceSwapper._clip((int(x0-w*pad),int(y0-h*pad),int(w*(1+2*pad)),int(h*(1+2*pad))),shape)

    @staticmethod
    def _feature_warp(image: np.ndarray, srcpos: np.ndarray, dstpos: np.ndarray, ids, strength: float):
        ids = [i for i in ids if i < len(srcpos) and i < len(dstpos)]
        sr,dr = OmegaFaceSwapper._feature_rect(srcpos[ids],image.shape),OmegaFaceSwapper._feature_rect(dstpos[ids],image.shape)
        sx,sy,sw,sh=sr; dx,dy,dw,dh=dr
        if min(sw,sh,dw,dh)<3: return image
        patch=image[sy:sy+sh,sx:sx+sw]
        if not patch.size: return image
        patch=cv2.resize(patch,(dw,dh),interpolation=cv2.INTER_LANCZOS4)
        m=np.zeros((dh,dw),np.uint8); cv2.ellipse(m,(dw//2,dh//2),(max(1,int(dw*.44)),max(1,int(dh*.42))),0,0,360,255,-1,cv2.LINE_AA)
        k=max(3,int(min(dw,dh)*.18))|1; a=(cv2.GaussianBlur(m,(k,k),0).astype(np.float32)/255*strength)[:,:,None]
        out=image.copy(); base=out[dy:dy+dh,dx:dx+dw].astype(np.float32)
        out[dy:dy+dh,dx:dx+dw]=np.clip(base*(1-a)+patch.astype(np.float32)*a,0,255).astype(np.uint8)
        return out

    @staticmethod
    def _mask(points: np.ndarray, shape, fw: int):
        m=np.zeros(shape[:2],np.uint8); cv2.fillConvexPoly(m,cv2.convexHull(np.rint(points[:14]).astype(np.int32)),255,cv2.LINE_AA)
        e=max(1,int(fw*.010)); m=cv2.erode(m,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(2*e+1,2*e+1)),iterations=1)
        b=max(5,int(fw*.045))|1; return cv2.GaussianBlur(m,(b,b),0)

    def _harmonize(self, warped: np.ndarray, target: np.ndarray, rect: Rect, illustrated: bool):
        x,y,w,h=self._clip(rect,target.shape); out=warped.copy(); s=out[y:y+h,x:x+w]; t=target[y:y+h,x:x+w]
        if not s.size:return out
        if illustrated:
            sl=cv2.cvtColor(s,cv2.COLOR_BGR2LAB).astype(np.float32); tl=cv2.cvtColor(t,cv2.COLOR_BGR2LAB).astype(np.float32)
            sl[:,:,0]=np.clip(sl[:,:,0]+np.clip(np.median(tl[:,:,0])-np.median(sl[:,:,0]),-18,18)*.55,0,255)
            matched=cv2.cvtColor(sl.astype(np.uint8),cv2.COLOR_LAB2BGR)
        else:
            matched=BaseFaceSwapper._color_match(s,t); matched=cv2.addWeighted(s,.48,matched,.52,0)
        out[y:y+h,x:x+w]=matched; return out

    @staticmethod
    def _finish(image: np.ndarray, mask: np.ndarray, fw: int, illustrated: bool):
        sigma,amount=(.58,.34) if illustrated else (.72,.28)
        low=cv2.GaussianBlur(image,(0,0),sigma); sharp=cv2.addWeighted(image,1+amount,low,-amount,0)
        k=max(3,int(fw*.025))|1; inner=cv2.erode(mask,cv2.getStructuringElement(cv2.MORPH_ELLIPSE,(k,k)),iterations=1)
        a=(inner.astype(np.float32)/255)[:,:,None]
        return np.clip(image.astype(np.float32)*(1-a)+sharp.astype(np.float32)*a,0,255).astype(np.uint8)

    def swap_face(self, source_image: np.ndarray, target_frame: np.ndarray, source_rect: Optional[Rect]=None, target_rect: Optional[Rect]=None):
        if source_rect is None: source_rect=self.detect_face(source_image)
        if source_rect is None: raise ValueError("No face detected in source image")
        target_points,rect,target_illustrated=self._geometry(target_frame,target_rect)
        if target_points is None or rect is None:return target_frame.copy(),self._omega_prev_rect
        source,source_points,_,source_illustrated=self._prepare_source(source_image,source_rect)
        matrix=self._affine(source_points,target_points)
        if matrix is None:
            warped=target_frame.copy()
            for a,b,c in BaseFaceSwapper._triangle_indices(target_points,target_frame.shape):
                BaseFaceSwapper._warp_triangle(source,warped,source_points[[a,b,c]],target_points[[a,b,c]])
        else:
            h,w=target_frame.shape[:2]
            warped=cv2.warpAffine(source,matrix,(w,h),flags=cv2.INTER_LANCZOS4,borderMode=cv2.BORDER_REFLECT_101)
            transformed=self._transform(source_points,matrix)
            warped=self._feature_warp(warped,transformed,target_points,range(20,26),.78)
            warped=self._feature_warp(warped,transformed,target_points,range(26,32),.78)
            warped=self._feature_warp(warped,transformed,target_points,range(36,44),.86)
        fw=rect[2]; illustrated=source_illustrated or target_illustrated
        warped=self._harmonize(warped,target_frame,rect,illustrated)
        mask=self._mask(target_points,target_frame.shape,fw)
        if not target_illustrated: warped=BaseFaceSwapper._transfer_target_lighting(warped,target_frame,mask)
        a=(mask.astype(np.float32)/255)[:,:,None]
        out=np.clip(warped.astype(np.float32)*a+target_frame.astype(np.float32)*(1-a),0,255).astype(np.uint8)
        out=BaseFaceSwapper._restore_expression_detail(self,out,target_frame,target_points,rect)
        return self._finish(out,mask,fw,illustrated),rect

    def process_video(self,*args,**kwargs):
        self._omega_reset_temporal()
        self._omega_prev_render=None
        return BaseFaceSwapper.process_video(self,*args,**kwargs)
