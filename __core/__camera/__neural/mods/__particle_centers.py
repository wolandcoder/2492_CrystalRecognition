from __future__ import annotations

import math
from collections import deque

import cv2
import numpy as np

from ..base import FrameContext, NeuralMod


                                                           
                   
                                                           

class _Det:
    __slots__ = ("cx", "cy", "w", "h", "angle", "area", "score", "kind", "top_conf")

    def __init__(
        self, cx: float, cy: float, w: float, h: float,
        angle: float, area: float, score: float,
        kind: str, top_conf: float,
    ) -> None:
        self.cx = cx
        self.cy = cy
        self.w = w
        self.h = h
        self.angle = angle
        self.area = area
        self.score = score
        self.kind = kind
        self.top_conf = top_conf


class _Track:
    SIDE_EMA = 0.08
    HYST_BAND = 0.12

    HISTORY_SIZE = 8
    PREDICT_BLEND = 0.7
    MAX_PREDICT_FRAMES = 5
    VEL_DAMPING = 0.92

    __slots__ = (
        "tid", "cx", "cy", "w", "h", "angle", "kind",
        "hits", "misses", "age", "confirmed",
        "_sin2", "_cos2", "_top_ema",
        "_history", "_vx", "_vy", "_predicted",
    )

    _next_id: int = 0

    def __init__(self, det: _Det) -> None:
        _Track._next_id += 1
        self.tid = _Track._next_id
        self.cx = det.cx
        self.cy = det.cy
        self.w = det.w
        self.h = det.h
        self.angle = det.angle
        r = math.radians(det.angle * 2)
        self._sin2 = math.sin(r)
        self._cos2 = math.cos(r)
        self._top_ema: float = det.top_conf
        self.kind = det.kind
        self.hits = 1
        self.misses = 0
        self.age = 1
        self.confirmed = False
        self._history: deque[tuple[float, float]] = deque(
            [(det.cx, det.cy)], maxlen=self.HISTORY_SIZE,
        )
        self._vx = 0.0
        self._vy = 0.0
        self._predicted = False

    def update(self, det: _Det, s: float) -> None:
        prev_cx, prev_cy = self.cx, self.cy
        self.cx = s * self.cx + (1 - s) * det.cx
        self.cy = s * self.cy + (1 - s) * det.cy
        s_wh = min(0.88, s + 0.28)
        self.w = s_wh * self.w + (1 - s_wh) * det.w
        self.h = s_wh * self.h + (1 - s_wh) * det.h

        diff = abs(det.angle - self.angle)
        diff = min(diff, 180.0 - diff)
        if diff < 20.0:
            a_s = min(0.94, s + 0.38)
            r = math.radians(det.angle * 2)
            self._sin2 = a_s * self._sin2 + (1 - a_s) * math.sin(r)
            self._cos2 = a_s * self._cos2 + (1 - a_s) * math.cos(r)
            self.angle = (math.degrees(math.atan2(self._sin2, self._cos2)) / 2) % 180

        alpha = self.SIDE_EMA
        self._top_ema = (1.0 - alpha) * self._top_ema + alpha * det.top_conf
        if self.kind == "top" and self._top_ema < 0.5 - self.HYST_BAND:
            self.kind = "bottom"
        elif self.kind == "bottom" and self._top_ema > 0.5 + self.HYST_BAND:
            self.kind = "top"

        self._history.append((self.cx, self.cy))
        self._update_velocity(prev_cx, prev_cy)

        self.hits += 1
        self.misses = 0
        self.age += 1
        self._predicted = False

    def _update_velocity(self, prev_cx: float, prev_cy: float) -> None:
        vx_inst = self.cx - prev_cx
        vy_inst = self.cy - prev_cy
        beta = 0.4
        self._vx = (1.0 - beta) * self._vx + beta * vx_inst
        self._vy = (1.0 - beta) * self._vy + beta * vy_inst

    def mark_miss(self) -> None:
        self.misses += 1
        self.age += 1
        if self.confirmed and self.misses <= self.MAX_PREDICT_FRAMES:
            self._extrapolate()

    def _extrapolate(self) -> None:
        damping = self.VEL_DAMPING ** self.misses
        pred_cx = self.cx + self._vx * damping
        pred_cy = self.cy + self._vy * damping
        self.cx = self.PREDICT_BLEND * pred_cx + (1.0 - self.PREDICT_BLEND) * self.cx
        self.cy = self.PREDICT_BLEND * pred_cy + (1.0 - self.PREDICT_BLEND) * self.cy
        self._predicted = True
        self._vx *= damping
        self._vy *= damping

    def stability(self) -> float:
        """Метрика стабильности трека [0..1].

        Учитывает: возраст, miss-rate, дисперсию скорости.
        Используется для фильтрации ненадёжных целей перед командой станку.
        """
        if self.hits < 2:
            return 0.0
        age_score = min(1.0, self.hits / 12.0)
        miss_pen = max(0.0, 1.0 - self.misses / 6.0)
        if len(self._history) >= 3:
            xs = [p[0] for p in self._history]
            ys = [p[1] for p in self._history]
            dx = max(xs) - min(xs)
            dy = max(ys) - min(ys)
            spread = math.hypot(dx, dy)
            speed = math.hypot(self._vx, self._vy)
            jitter = max(0.0, spread - speed * len(self._history))
            jitter_score = max(0.0, 1.0 - jitter / 40.0)
        else:
            jitter_score = 0.5
        return float(0.4 * age_score + 0.3 * miss_pen + 0.3 * jitter_score)

    @property
    def is_predicted(self) -> bool:
        return self._predicted


def _rotated_box_pts(cx: float, cy: float, w: float, h: float, angle: float) -> np.ndarray:
    return np.intp(cv2.boxPoints(((cx, cy), (w, h), angle)))


                                                           
      
                                                           

class Mod(NeuralMod):
    name = "__particle_centers"

                     
    WORK_WIDTH = 640

                                                
    MIN_AREA = 55
    MAX_AREA = 10000
    SINGLE_AREA_MAX = 500
    TYPICAL_SINGLE_AREA = 280
    PEAK_MIN_DIST = 7

                               
    MIN_SOLIDITY = 0.42
    MAX_ASPECT = 2.5
    MIN_EXTENT = 0.35

                                           
    MIN_CONTRAST = 14.0
    MAX_INNER_STD = 72.0

                         
    CLAHE_CLIP = 2.5
    CLAHE_GRID = (8, 8)
    BRIGHT_ADAPT_BLOCK = 31
    BRIGHT_ADAPT_C = -8
    BRIGHT_FIXED_THRESH = 145

                   
    CANNY_LO = 40
    CANNY_HI = 130

                                  
    WARM_H_RANGE = (5, 28)
    WARM_S_MIN = 40
    WARM_V_MIN = 50

                                  
    BG_ALPHA = 0.015
    BG_WARMUP = 20
    BG_FG_THRESH = 20

                       
    WS_DIST_FRAC = 0.30

                     
    MATCH_DIST = 50.0
    SMOOTH = 0.55
    CONFIRM_HITS = 3
    HOLD_FRAMES = 14

                 
    NMS_DIST = 14

                                             
                                                                              
                                                                             
    TOP_V_MIN = 130
    TOP_S_MAX = 80
    BOTTOM_H_RANGE = (5, 30)
    BOTTOM_S_MIN = 30

                                                          
    COLOR_TOP = (0, 140, 50)
    COLOR_BOTTOM = (160, 60, 0)
    COLOR_HOLD = (80, 100, 110)
    COLOR_CENTER = (0, 200, 220)
    FONT = cv2.FONT_HERSHEY_SIMPLEX

    def __init__(self) -> None:
        self._clahe = cv2.createCLAHE(
            clipLimit=self.CLAHE_CLIP, tileGridSize=self.CLAHE_GRID,
        )
        self._kern_e3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._kern_e5 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._kern_r5 = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        self._kern_e7 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        self._tracks: list[_Track] = []
        self._bg: np.ndarray | None = None
        self._bg_n = 0
        self._colors_loaded = False

                                                            
            
                                                            

    def apply(self, frame: object, context: FrameContext) -> object:
        if not self._colors_loaded:
            self._load_colors(context)
            self._colors_loaded = True

        orig_h, orig_w = frame.shape[:2]
        scale = self.WORK_WIDTH / orig_w
        sh = int(orig_h * scale)
        small = cv2.resize(frame, (self.WORK_WIDTH, sh), interpolation=cv2.INTER_AREA)
        inv = 1.0 / scale

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray_f = gray.astype(np.float32)
        hsv_small = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)

        self._update_bg(gray_f)
        fg = self._fg_mask(gray_f)

        m_bright = self._bin_bright(gray)
        m_warm = self._bin_warm(small)
        binary = cv2.bitwise_or(m_bright, m_warm)

        if fg is not None:
            binary = cv2.bitwise_and(binary, fg)

        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, self._kern_e3, iterations=1)
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, self._kern_r5, iterations=1)

                                                                       
                                                                         
        eroded = cv2.erode(binary, self._kern_e3, iterations=1)
        dilated = cv2.dilate(eroded, self._kern_e3, iterations=1)
                                                                             
                                          
        contours_check, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        has_big = any(cv2.contourArea(c) > self.SINGLE_AREA_MAX for c in contours_check)
        pre_split = dilated if has_big else binary

        split = self._split_touching(pre_split)

        contours, _ = cv2.findContours(split, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        dets: list[_Det] = []
        for cnt in contours:
            d = self._qualify(cnt, gray, hsv_small, inv)
            if d is not None:
                dets.append(d)

        dets = self._nms(dets)
        self._update_tracks(dets)

        vis = [t for t in self._tracks if t.confirmed and t.misses == 0]
        hold = [
            t for t in self._tracks
            if t.confirmed and 0 < t.misses <= self.HOLD_FRAMES
        ]
        predicted = [
            t for t in self._tracks
            if t.confirmed and 0 < t.misses <= _Track.MAX_PREDICT_FRAMES
            and t.is_predicted
        ]

        self._render(frame, vis, hold, predicted)

        out_centers: list[dict] = []
        for t in vis:
            out_centers.append({
                "cx": int(t.cx), "cy": int(t.cy),
                "w": int(t.w), "h": int(t.h),
                "angle": round(t.angle, 1), "side": t.kind, "tid": t.tid,
                "stability": round(t.stability(), 3),
                "predicted": False,
                "misses": t.misses,
            })
        for t in predicted:
            out_centers.append({
                "cx": int(t.cx), "cy": int(t.cy),
                "w": int(t.w), "h": int(t.h),
                "angle": round(t.angle, 1), "side": t.kind, "tid": t.tid,
                "stability": round(t.stability() * 0.5, 3),
                "predicted": True,
                "misses": t.misses,
            })

        context.shared["particle_centers"] = out_centers
        context.shared["particle_centers_stable"] = [
            c for c in out_centers
            if c["stability"] >= 0.5 and not c["predicted"]
        ]
        return frame

                                                            
                
                                                            

    def _update_bg(self, gf: np.ndarray) -> None:
        if self._bg is None:
            self._bg = gf.copy()
            self._bg_n = 1
            return
        self._bg_n += 1
        a = min(self.BG_ALPHA, 1.0 / self._bg_n)
        cv2.accumulateWeighted(gf, self._bg, a)

    def _fg_mask(self, gf: np.ndarray) -> np.ndarray | None:
        if self._bg_n < self.BG_WARMUP:
            return None
        diff = cv2.absdiff(gf, self._bg)
        _, m = cv2.threshold(diff.astype(np.uint8), self.BG_FG_THRESH, 255, cv2.THRESH_BINARY)
        m = cv2.dilate(m, self._kern_e7, iterations=3)
        return m

                                                            
                  
                                                            

    def _bin_bright(self, gray: np.ndarray) -> np.ndarray:
        norm = self._clahe.apply(gray)
        blur = cv2.GaussianBlur(norm, (5, 5), 0)

        at = cv2.adaptiveThreshold(
            blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, self.BRIGHT_ADAPT_BLOCK, self.BRIGHT_ADAPT_C,
        )

        _, fixed = cv2.threshold(blur, self.BRIGHT_FIXED_THRESH, 255, cv2.THRESH_BINARY)

        edges = cv2.Canny(blur, self.CANNY_LO, self.CANNY_HI)
        closed_edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, self._kern_e5, iterations=2)
        filled = self._fill_closed_edges(closed_edges)

        bright_core = cv2.bitwise_or(fixed, filled)
        return cv2.bitwise_and(at, bright_core)

    @staticmethod
    def _fill_closed_edges(edges: np.ndarray) -> np.ndarray:
        h, w = edges.shape[:2]
        flood = edges.copy()
        mask = np.zeros((h + 2, w + 2), dtype=np.uint8)
        for y, x in [(0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)]:
            if flood[y, x] == 0:
                cv2.floodFill(flood, mask, (x, y), 128)
        result = np.zeros_like(edges)
        result[flood == 0] = 255
        return result

    def _bin_warm(self, small: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        lo = np.array([self.WARM_H_RANGE[0], self.WARM_S_MIN, self.WARM_V_MIN], dtype=np.uint8)
        hi = np.array([self.WARM_H_RANGE[1], 255, 255], dtype=np.uint8)
        m = cv2.inRange(hsv, lo, hi)
        return cv2.morphologyEx(m, cv2.MORPH_CLOSE, self._kern_e3, iterations=1)

                                                            
                                   
                                                            

    def _split_touching(self, binary: np.ndarray) -> np.ndarray:
        contours_raw, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours_raw:
            return binary

        if not any(cv2.contourArea(c) > self.SINGLE_AREA_MAX for c in contours_raw):
            return binary

        dist = cv2.distanceTransform(binary, cv2.DIST_L2, 5)
        if dist.max() < 2:
            return binary

        seeds = self._find_local_peaks(dist, binary)
        if seeds is not None:
            result = self._watershed_from_seeds(binary, seeds)
        else:
            result = self._split_by_threshold(binary, dist)

        return self._kmeans_oversized(result)

    def _find_local_peaks(self, dist: np.ndarray, binary: np.ndarray) -> np.ndarray | None:
        d_blur = cv2.GaussianBlur(dist, (5, 5), 0)
        ksize = self.PEAK_MIN_DIST * 2 + 1
        local_max = cv2.dilate(d_blur, np.ones((ksize, ksize), dtype=np.uint8))
        peak_mask = ((d_blur >= local_max) & (dist > 2.0)).astype(np.uint8) * 255
        peak_mask = cv2.bitwise_and(peak_mask, binary)
        peak_mask = cv2.morphologyEx(peak_mask, cv2.MORPH_OPEN, self._kern_e3, iterations=1)
        n_labels, markers = cv2.connectedComponents(peak_mask)
        if n_labels < 3:
            return None
        return markers

    def _split_by_threshold(self, binary: np.ndarray, dist: np.ndarray) -> np.ndarray:
        thresh_val = max(2.0, dist.max() * self.WS_DIST_FRAC)
        _, sure_fg = cv2.threshold(dist, thresh_val, 255, cv2.THRESH_BINARY)
        sure_fg = sure_fg.astype(np.uint8)
        n_labels, markers = cv2.connectedComponents(sure_fg)
        if n_labels < 3:
            return binary
        return self._watershed_from_seeds(binary, markers)

    def _watershed_from_seeds(self, binary: np.ndarray, seed_markers: np.ndarray) -> np.ndarray:
        sure_bg = cv2.dilate(binary, self._kern_r5, iterations=2)
        markers = seed_markers.copy().astype(np.int32)
        markers += 1
        markers[binary == 0] = 1
        unknown = cv2.subtract(sure_bg, (seed_markers > 0).astype(np.uint8) * 255)
        markers[unknown == 255] = 0
        ws_img = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
        cv2.watershed(ws_img, markers)
        out = np.zeros_like(binary)
        out[markers > 1] = 255
        out = cv2.erode(out, self._kern_e3, iterations=1)
        out = cv2.dilate(out, self._kern_e3, iterations=1)
        return out

    def _kmeans_oversized(self, binary: np.ndarray) -> np.ndarray:
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return binary
        result = binary.copy()
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area <= self.SINGLE_AREA_MAX:
                continue
            k = max(2, round(area / self.TYPICAL_SINGLE_AREA))
            k = min(k, 6)
            mask_blob = np.zeros(binary.shape[:2], dtype=np.uint8)
            cv2.drawContours(mask_blob, [cnt], 0, 255, -1)
            ys, xs = np.where(mask_blob > 0)
            if len(ys) < k * 10:
                continue
            pts = np.column_stack((xs, ys)).astype(np.float32)
            criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1.0)
            _, labels, _ = cv2.kmeans(pts, k, None, criteria, 4, cv2.KMEANS_PP_CENTERS)
            labels = labels.flatten()
            cv2.drawContours(result, [cnt], 0, 0, -1)
            for ci in range(k):
                cluster_mask = np.zeros_like(mask_blob)
                cluster_pts = pts[labels == ci].astype(np.int32)
                if len(cluster_pts) < self.MIN_AREA // 2:
                    continue
                cluster_mask[cluster_pts[:, 1], cluster_pts[:, 0]] = 255
                cluster_mask = cv2.morphologyEx(cluster_mask, cv2.MORPH_CLOSE, self._kern_e5, iterations=2)
                cluster_mask = cv2.erode(cluster_mask, self._kern_e3, iterations=1)
                result = cv2.bitwise_or(result, cluster_mask)
        return result

                                                            
                                  
                                                            

    def _load_colors(self, context: FrameContext) -> None:
        """Читаем цвета из context.shared['_config'] или используем дефолт."""
        cfg = context.shared.get("_particle_cfg")
        if cfg is None:
            try:
                import json
                from pathlib import Path
                p = Path(__file__).resolve().parents[4] / "config.json"
                if p.exists():
                    raw = json.loads(p.read_text("utf-8"))
                    cfg = raw.get("particle_centers", {})
            except Exception:
                cfg = {}
        if cfg:
            if "color_top" in cfg:
                self.COLOR_TOP = tuple(cfg["color_top"])
            if "color_bottom" in cfg:
                self.COLOR_BOTTOM = tuple(cfg["color_bottom"])
            if "color_center" in cfg:
                self.COLOR_CENTER = tuple(cfg["color_center"])
            if "color_hold" in cfg:
                self.COLOR_HOLD = tuple(cfg["color_hold"])

    def _classify_side(self, hsv: np.ndarray, cnt: np.ndarray) -> tuple[str, float]:
        """Определяет сторону кристалла и мягкий confidence [0..1] (1 = top).

        top  — верхушка: светлая/белая (высокий V, низкий S).
        bottom — низ: бронзовый/тёмный (средний V, заметный S, тёплый H).
        """
        h, w = hsv.shape[:2]
        x, y, bw, bh = cv2.boundingRect(cnt)
        x0, y0 = max(0, x), max(0, y)
        x1, y1 = min(w, x + bw), min(h, y + bh)
        if x1 - x0 < 2 or y1 - y0 < 2:
            return "top", 0.75

        roi_mask = np.zeros((y1 - y0, x1 - x0), dtype=np.uint8)
        shifted = cnt - np.array([x0, y0])
        cv2.drawContours(roi_mask, [shifted], 0, 255, -1)

        roi_hsv = hsv[y0:y1, x0:x1]
        pixels_h = roi_hsv[:, :, 0][roi_mask > 0]
        pixels_s = roi_hsv[:, :, 1][roi_mask > 0]
        pixels_v = roi_hsv[:, :, 2][roi_mask > 0]
        if pixels_v.size < 4:
            return "top", 0.75

        med_v = float(np.median(pixels_v))
        med_s = float(np.median(pixels_s))
        med_h = float(np.median(pixels_h))

                                                            
                                                             
        v_score = np.clip((med_v - 80.0) / (self.TOP_V_MIN - 80.0 + 50.0), 0.0, 1.0)

                                                                         
        s_score = np.clip(1.0 - (med_s - 20.0) / (self.TOP_S_MAX + 40.0), 0.0, 1.0)

                                                                             
        h_lo, h_hi = self.BOTTOM_H_RANGE
        h_mid = (h_lo + h_hi) / 2.0
        h_half = (h_hi - h_lo) / 2.0 + 5.0
        h_warm = max(0.0, 1.0 - abs(med_h - h_mid) / h_half)
        warm_pull = h_warm * np.clip(med_s / 80.0, 0.0, 1.0)

        conf = 0.45 * v_score + 0.35 * s_score - 0.30 * warm_pull
        conf = float(np.clip(conf, 0.0, 1.0))

        kind = "top" if conf >= 0.5 else "bottom"
        return kind, conf

    def _qualify(
        self, cnt: np.ndarray, gray: np.ndarray,
        hsv: np.ndarray, inv: float,
    ) -> _Det | None:
        area = cv2.contourArea(cnt)
        if area < self.MIN_AREA or area > self.MAX_AREA:
            return None
        if len(cnt) < 5:
            return None

        hull_a = cv2.contourArea(cv2.convexHull(cnt))
        if hull_a < 1 or area / hull_a < self.MIN_SOLIDITY:
            return None

        rect = cv2.minAreaRect(cnt)
        (rcx, rcy), (rw, rh), rangle = rect
        rw, rh = max(rw, 1.0), max(rh, 1.0)
        short, long = min(rw, rh), max(rw, rh)
        if long / short > self.MAX_ASPECT:
            return None
        if area / (rw * rh) < self.MIN_EXTENT:
            return None

        m = cv2.moments(cnt)
        if m["m00"] < 1:
            return None
        mcx = m["m10"] / m["m00"]
        mcy = m["m01"] / m["m00"]

        if rw < rh:
            rw, rh = rh, rw
            rangle = (rangle + 90) % 180

        aspect = long / short
        if aspect > 1.20 and len(cnt) >= 5:
            try:
                (_, _), (_, _), eangle = cv2.fitEllipse(cnt)
                angle = eangle % 180
            except cv2.error:
                angle = rangle % 180
        else:
            angle = rangle % 180

        contrast, inner_std = self._contrast_and_std(gray, cnt, mcx, mcy, long)
        if contrast < self.MIN_CONTRAST:
            return None
        if inner_std > self.MAX_INNER_STD:
            return None

        kind, top_conf = self._classify_side(hsv, cnt)

        score = contrast * 2.0 + area * 0.005 + (1.0 - abs(long / short - 1.0)) * 15.0

        out_w = max(4.0, rw * inv + 1.0)
        out_h = max(4.0, rh * inv + 1.0)

        return _Det(
            cx=mcx * inv, cy=mcy * inv,
            w=out_w, h=out_h,
            angle=angle, area=area * inv * inv,
            score=score, kind=kind, top_conf=top_conf,
        )

    @staticmethod
    def _contrast_and_std(
        gray: np.ndarray, cnt: np.ndarray,
        cx: float, cy: float, size: float,
    ) -> tuple[float, float]:
        h, w = gray.shape[:2]
        pad = max(10, int(size * 0.8))
        x0 = max(0, int(cx - size - pad))
        y0 = max(0, int(cy - size - pad))
        x1 = min(w, int(cx + size + pad))
        y1 = min(h, int(cy + size + pad))
        if x1 - x0 < 4 or y1 - y0 < 4:
            return 0.0, 999.0
        rh, rw = y1 - y0, x1 - x0
        mi = np.zeros((rh, rw), dtype=np.uint8)
        shifted = cnt - np.array([x0, y0])
        cv2.drawContours(mi, [shifted], 0, 255, -1)
        roi = gray[y0:y1, x0:x1]
        inner = roi[mi > 0]
        outer = roi[mi == 0]
        if inner.size < 6 or outer.size < 10:
            return 0.0, 999.0
        return (
            abs(float(np.mean(inner)) - float(np.mean(outer))),
            float(np.std(inner)),
        )

                                                            
          
                                                            

    @staticmethod
    def _nms(dets: list[_Det]) -> list[_Det]:
        if len(dets) <= 1:
            return dets
        dets.sort(key=lambda d: -d.score)
        keep: list[_Det] = []
        used = [False] * len(dets)
        r2 = Mod.NMS_DIST ** 2
        for i, di in enumerate(dets):
            if used[i]:
                continue
            keep.append(di)
            for j in range(i + 1, len(dets)):
                if used[j]:
                    continue
                dx = di.cx - dets[j].cx
                dy = di.cy - dets[j].cy
                if dx * dx + dy * dy < r2:
                    used[j] = True
        return keep

                                                            
              
                                                            

    def _update_tracks(self, dets: list[_Det]) -> None:
        mt: set[int] = set()
        md: set[int] = set()
        r2 = self.MATCH_DIST ** 2
        pairs: list[tuple[float, int, int]] = []
        for ti, tr in enumerate(self._tracks):
            for di, det in enumerate(dets):
                dx = tr.cx - det.cx
                dy = tr.cy - det.cy
                d2 = dx * dx + dy * dy
                if d2 < r2:
                    pairs.append((d2, ti, di))
        pairs.sort()
        for _, ti, di in pairs:
            if ti in mt or di in md:
                continue
            self._tracks[ti].update(dets[di], self.SMOOTH)
            if self._tracks[ti].hits >= self.CONFIRM_HITS:
                self._tracks[ti].confirmed = True
            mt.add(ti)
            md.add(di)
        for ti in range(len(self._tracks)):
            if ti not in mt:
                self._tracks[ti].mark_miss()
        for di in range(len(dets)):
            if di not in md:
                self._tracks.append(_Track(dets[di]))
        self._tracks = [t for t in self._tracks if t.misses <= self.HOLD_FRAMES + 4]

                                                            
                   
                                                            

    def _render(
        self, frame: np.ndarray,
        vis: list[_Track], hold: list[_Track],
        predicted: list[_Track] | None = None,
    ) -> None:
        for t in vis:
            c = self.COLOR_TOP if t.kind == "top" else self.COLOR_BOTTOM
            self._draw_track(frame, t, c)
        if predicted:
            for t in predicted:
                self._draw_predicted(frame, t)
        fh = frame.shape[0]
        suffix = f"  (pred: {len(predicted)})" if predicted else ""
        cv2.putText(
            frame, f"elements: {len(vis)}{suffix}", (8, fh - 14),
            self.FONT, 0.52, (220, 220, 220), 1, cv2.LINE_AA,
        )

    def _draw_predicted(self, frame: np.ndarray, t: _Track) -> None:
        """Рисует предсказанный трек пунктирной рамкой."""
        box = _rotated_box_pts(t.cx, t.cy, t.w, t.h, t.angle)
        for i in range(4):
            p1 = tuple(box[i])
            p2 = tuple(box[(i + 1) % 4])
            mid = ((p1[0] + p2[0]) // 2, (p1[1] + p2[1]) // 2)
            cv2.line(frame, p1, mid, self.COLOR_HOLD, 1, cv2.LINE_AA)

    def _draw_track(
        self, frame: np.ndarray, t: _Track, color: tuple,
    ) -> None:
        box = _rotated_box_pts(t.cx, t.cy, t.w, t.h, t.angle)
        cv2.drawContours(frame, [box], 0, color, 2, cv2.LINE_AA)

        rad = math.radians(t.angle)
        cs, sn = math.cos(rad), math.sin(rad)
        hw, hh = t.w * 0.5, t.h * 0.5
        cx, cy = t.cx, t.cy
                                        
        dx1, dy1 = cs * hw, sn * hw
        cv2.line(
            frame,
            (int(cx - dx1), int(cy - dy1)),
            (int(cx + dx1), int(cy + dy1)),
            self.COLOR_CENTER, 1, cv2.LINE_AA,
        )
                                                                 
        dx2, dy2 = -sn * hh, cs * hh
        cv2.line(
            frame,
            (int(cx - dx2), int(cy - dy2)),
            (int(cx + dx2), int(cy + dy2)),
            self.COLOR_CENTER, 1, cv2.LINE_AA,
        )
