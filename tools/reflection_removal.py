"""도자기 제품 마스크 + 선반 반사 제거 (단독 실행 가능, 다른 프로젝트 파일 불필요).

hey,hye 사진 보정 작업에서 사진별로 눈으로 검증한 함수들만 모은 파일이다.
필요한 패키지: opencv-python(-headless), numpy, rembg, pillow

함수
  product_mask(img_bgr, model)            rembg 로 제품 알파(0~1 float). model:
                                           "birefnet-general"(경계 정확, 기본 권장),
                                           "isnet-general-use"(소품과 잘 구분, 경계 거침),
                                           "ensemble"(isnet 으로 제품 선택 + birefnet 경계)
  remove_mirror_reflection(img, alpha)    (가) 흰 굽 띠 아래 거울 반사 → (alpha, 타원, x범위) 반환
  remove_rim_reflection(img, alpha)       (나) 어두운 유광 몸체 아래 밝은 띠
  remove_crease_reflection(img, alpha)    (다) 얇은 흰 굽 선 → 어두운 접지선 → 반사
  remove_bottom_reflection(img, alpha)    (라) 위에서 본 그릇의 얇은 반사 띠
  (마) 분리 모델이 이미 굽 끝에서 끊었으면 아무것도 호출하지 않는다

주의 (이전 세션에서 확인)
  - 굽 아래 밝은 띠는 실제 굽이 아니라 선반에 비친 반사다.
  - (다)는 굽이 몸통보다 좁은 컵에서 아래 모서리를 계단처럼 깎을 수 있다 → 그때는 (마).
  - 새 사진은 먼저 마스크 윤곽을 원본 위에 겹쳐 확대해 보고 방식을 고른다.
  - 모든 함수는 마스크만 줄인다. 제품 픽셀 값은 바꾸지 않는다.
  - 원본 해상도(이 작업에서는 1512x2016 로 검증)에서 동작한다. 4032x3024 원본에서는
    픽셀 단위 상수(search=32, 커널 크기 등)를 해상도 비율만큼 키워야 할 수 있다.

명령줄 사용 (윤곽 확인용 오버레이 저장):
  python reflection_removal.py 사진.jpg --model birefnet-general --reflection crease --out 폴더
"""
import argparse
import os

import cv2
import numpy as np


def _rembg(img_bgr, model):
    from PIL import Image
    from rembg import new_session, remove

    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    m = remove(Image.fromarray(rgb), session=new_session(model), only_mask=True)
    return np.asarray(m, dtype=np.float32) / 255.0


def _clean(a, cut_thin=True):
    """가장 큰 덩어리만 남기고, 붙어온 얇은 조각 제거, 작은 내부 구멍 메우기.

    cut_thin=False 면 얇은 조각 제거와 구멍 메우기를 건너뛴다. 식물 잎처럼
    얇은 부분이나 잎 사이로 보이는 배경이 있는 경우. birefnet 은 아크릴을
    애초에 잘 빼고 유광 반사광에 구멍도 거의 내지 않아 불필요.
    """
    a = a.copy()
    n, lab, stats, _ = cv2.connectedComponentsWithStats((a > 0.5).astype(np.uint8))
    body = (lab == 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])).astype(np.uint8)
    # 제품 옆에 붙어 딸려온 얇은 조각(투명 아크릴 가격표 등) 제거:
    # 굵은 몸체만 남기는 opening 후, 원래 윤곽은 그 주변 몇 px 안에서만 유지
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
    core = cv2.dilate(cv2.morphologyEx(body, cv2.MORPH_OPEN, k), np.ones((7, 7), np.uint8))
    if cut_thin:
        body &= core
    n, lab, stats, _ = cv2.connectedComponentsWithStats(body)
    body = (lab == 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])).astype(np.uint8)
    # 유광 제품의 밝은 반사광이 구멍으로 빠진 곳 메우기 (작은 내부 구멍만)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(1 - body)
    for i in range(n):
        x, y, bw, bh, area = stats[i]
        touches = x == 0 or y == 0 or x + bw == body.shape[1] or y + bh == body.shape[0]
        if cut_thin and not touches and area < 0.01 * body.sum():
            body[lab == i] = 1
            a[lab == i] = 1.0
    a *= cv2.dilate(body, np.ones((5, 5), np.uint8))
    return a, body


def product_mask(img_bgr, model="isnet-general-use"):
    """rembg(AI 분할)로 제품 알파 마스크를 만든다. 0~1 float.

    model="ensemble": isnet 으로 '어느 덩어리가 제품인지'를 정하고(주변 소품과
    잘 분리됨), 그 영역 안에서 birefnet 의 더 정확한 경계를 쓴다.
    """
    if model == "ensemble":
        a_i, guide = _clean(_rembg(img_bgr, "isnet-general-use"))
        guide = cv2.dilate(guide, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31)))
        a, _ = _clean(_rembg(img_bgr, "birefnet-general") * guide)
    else:
        a, _ = _clean(_rembg(img_bgr, model), cut_thin=not model.startswith("birefnet"))
    # 경계 바깥 1~2px만 부드럽게, 안쪽은 확실히 1.0 (제품 원본 유지)
    a[a > 0.92] = 1.0
    a[a < 0.04] = 0.0
    return a


def remove_bottom_reflection(img_bgr, alpha, search=32, thr=185):
    """제품 아래 바닥(금속)에 비친 반사 띠를 마스크에서 잘라낸다.

    각 열마다 마스크 바닥에서 위쪽 search px(반사 띠 두께 정도) 범위만 위→아래로 훑어,
    밝은 유약면이 끝나고 어두운 반사 띠가 시작되는 지점을 찾아 그 아래를 제거.
    제품 픽셀 값은 건드리지 않고 마스크만 줄인다.
    """
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gray = cv2.GaussianBlur(gray, (0, 0), 1.0)
    h, w = alpha.shape
    solid = alpha > 0.5
    xs = np.nonzero(solid.any(0))[0]
    cut = np.full(w, -1.0)
    for x in xs:
        ys = np.nonzero(solid[:, x])[0]
        top, bot = ys.min(), ys.max()
        for y in range(max(top, bot - search), bot + 1):
            if gray[y, x] < thr and gray[min(y + 3, h - 1), x] < thr:
                cut[x] = y
                break
        else:
            cut[x] = bot + 1
    # 바닥 윤곽이 완만한 구간(실제 바닥 테두리)에서만 자른다.
    # 옆면처럼 가파른 열에서는 자르지 않아 계단이 생기지 않게 한다.
    bots = np.array([np.nonzero(solid[:, x])[0].max() if solid[:, x].any() else -1
                     for x in range(w)], np.float32)
    slope = np.abs(np.gradient(bots))
    valid = cut >= 0
    steep = valid & (cv2.blur(slope.reshape(1, -1), (15, 1)).ravel() > 1.2)
    cut[steep] = bots[steep] + 1
    # 잘라낼 두께(깊이)를 매끈하게 → 윤곽 모양은 원본을 따르고 전환부 계단 방지
    depth = np.where(valid, bots + 1 - cut, 0).astype(np.float32)
    k = 15
    pad = np.pad(depth, k, mode="edge")
    depth = np.array([np.median(pad[i:i + 2 * k + 1]) for i in range(w)])
    depth = np.convolve(np.pad(depth, 20, mode="edge"), np.ones(41) / 41, "valid")
    c = (bots + 1 - depth)[valid]
    yy = np.arange(h, dtype=np.float32)[:, None]
    ramp = np.clip(c[None, :] - yy, 0, 1)  # 컷 경계 1px 안티앨리어싱
    out = alpha.copy()
    out[:, valid] = np.minimum(alpha[:, valid], ramp)
    # 옆면 가파른 구간에서 생기는 계단/홈을 메운다 (원본 마스크 범위 안에서만)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (61, 61))
    closed = cv2.morphologyEx(out, cv2.MORPH_CLOSE, k)
    return np.minimum(alpha, closed)


def remove_mirror_reflection(img_bgr, alpha):
    """옆에서 찍어 굽 아래로 제품이 거울처럼 비친 경우의 반사 제거.

    hey,hye 제품은 굽 끝이 흰 무유(유약 없는) 띠이므로, 마스크 아래쪽에서
    '밝고 채도 낮은 흰 띠' 중 가장 아래 것이 끝나는 지점을 열마다 찾고, 그 점들에
    2차 곡선(굽 바닥 타원의 앞쪽 호)을 맞춰 그 아래를 모두 잘라낸다.
    반환: (새 알파, 굽 바닥 타원 (cx, cy, a, b), 굽 좌우 x 범위)
    """
    o = img_bgr.astype(np.int32)
    solid = alpha > 0.5
    ys, xs = np.nonzero(solid)
    top, bot = ys.min(), ys.max()
    white = (o.min(2) > 170) & ((o.max(2) - o.min(2)) < 45)
    pts = []
    for x in range(xs.min(), xs.max() + 1, 2):
        col = np.nonzero(solid[:, x])[0]
        if not len(col):
            continue
        # 흰 띠 중 '가장 아래' 것이 굽 끝 (위쪽의 밝은 유약면을 흰 띠로 오인하지 않게)
        run, last = 0, None
        for y in range(max(col.min(), bot - int(0.45 * (bot - top))), col.max() + 1):
            if white[y, x]:
                run += 1
            else:
                if run >= 4:
                    last = y
                run = 0
        if last is not None:
            pts.append((x, last))
    pts = np.array(pts, np.float64)
    if len(pts) < 20:
        return alpha, None, None
    # 이상치 제거하며 2차 곡선 맞추기
    keep = np.ones(len(pts), bool)
    for _ in range(3):
        q = np.polyfit(pts[keep, 0], pts[keep, 1], 2)
        r = np.abs(np.polyval(q, pts[:, 0]) - pts[:, 1])
        keep = r < max(3.0, 2.5 * np.median(r[keep]))
    h, w = alpha.shape
    xc0 = -q[1] / (2 * q[0])
    yb = float(np.polyval(q, xc0))  # 굽 바닥 앞쪽 가장 낮은 점
    # 굽과 반사가 만나는 좌우 끝은 실루엣이 잘록해지는 지점(가장 좁은 줄)
    rows = range(int(yb - 0.3 * (bot - top)), int(yb))
    widths = []
    for r in rows:
        c = np.nonzero(solid[r])[0]
        widths.append((c.max() - c.min(), r, c.min(), c.max()) if len(c) else (1e9, r, 0, 0))
    _, yp, l, r = min(widths)
    cx, ea, eb = (l + r) / 2, (r - l) / 2, max(yb - yp, 1.0)
    # 굽 바닥 = 타원의 앞쪽 아래 호. 그 아래와 좌우 바깥(잘록점 아래)은 반사
    xx = np.arange(w, dtype=np.float32)
    t = np.clip((xx - cx) / ea, -1, 1)
    curve = np.where(np.abs(xx - cx) <= ea, yp + eb * np.sqrt(1 - t * t), yp)
    yy = np.arange(h, dtype=np.float32)[:, None]
    ramp = np.clip(curve[None, :] - yy + 0.5, 0, 1).astype(np.float32)
    out = np.minimum(alpha, ramp)
    xv = np.array([l, r], np.float32)
    q = (cx, yp, ea, eb)  # 그림자용 굽 바닥 타원 정보
    return out, q, (xv.min(), xv.max())


def remove_rim_reflection(img_bgr, alpha, line_px=7):
    """유광(검정 등 어두운) 몸체 아래로 굽이 밝게 비친 경우의 반사 제거.

    열마다 마스크 맨 아래에서 위로 올라가며 밝은 반사 띠를 지나
    어두운 유약면이 나오는 지점을 찾고, 그 점들에 매끈한 곡선을 맞춘다.
    가운데는 몸체 아래 얇은 은색 테두리(실제 굽 끝, line_px)를 남기고
    좌우로 갈수록 0 으로 줄여, 그 아래 밝은 띠(반사)는 모두 잘라낸다.
    """
    g = cv2.GaussianBlur(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32),
                         (0, 0), 1.5)
    solid = alpha > 0.5
    ys, xs = np.nonzero(solid)
    top, bot = ys.min(), ys.max()
    lim = int(bot - 0.15 * (bot - top))
    X, T = [], []
    for x in range(xs.min(), xs.max() + 1):
        col = np.nonzero(solid[:, x])[0]
        if not len(col):
            continue
        b = y = col.max()
        while y > max(lim, col.min()) and not (g[y - 3:y + 1, x] < 80).all():
            y -= 1
        if b - (y + 1) > 6:  # 밝은 띠가 있는 열만
            X.append(x)
            T.append(y + 1)
    if len(X) < 20:
        return alpha
    X, T = np.array(X, float), np.array(T, float)
    keep = np.ones(len(X), bool)
    for _ in range(5):  # 유광 하이라이트로 튄 점 제거하며 4차 곡선 맞추기
        p = np.polyfit(X[keep], T[keep], 4)
        r = np.abs(T - np.polyval(p, X))
        keep = r < max(3.0, 2.5 * np.median(r[keep]))
    h, w = alpha.shape
    xl, xr = X.min(), X.max()
    xc, hw = (xl + xr) / 2, (xr - xl) / 2
    xx = np.arange(w, dtype=np.float64)
    thick = line_px * np.clip(1 - (np.abs(xx - xc) / hw) ** 4, 0, 1)
    cut = np.where((xx >= xl) & (xx <= xr), np.polyval(p, xx) + thick, 1e9)
    yy = np.arange(h, dtype=np.float64)[:, None]
    return np.minimum(alpha, np.clip(cut[None, :] - yy + 0.5, 0, 1).astype(np.float32))


def remove_crease_reflection(img_bgr, alpha, zone=0.12):
    """옆모습: 흰 굽 선 → 어두운 접지선 → (반사) 순서로 보이는 경우의 반사 제거.

    열마다 마스크 맨 아래에서 위로 올라가며, '바로 위가 밝은 흰 선(굽 끝)이고
    자신은 어두운' 접지선을 찾는다. 그 점들에 매끈한 곡선을 맞춰 접지선부터
    아래(바닥에 비친 굽·몸체)를 잘라낸다. 흰 선이 얇아도(3px~) 동작한다.
    """
    g = cv2.GaussianBlur(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32),
                         (0, 0), 0.8)
    solid = alpha > 0.5
    ys, xs = np.nonzero(solid)
    top, bot = ys.min(), ys.max()
    lim = int(bot - zone * (bot - top))
    X, T = [], []
    for x in range(xs.min(), xs.max() + 1):
        col = np.nonzero(solid[:, x])[0]
        if not len(col) or col.max() < lim:
            continue
        # 후보 중 '밝은 선 → 어두운 접지선' 대비가 가장 큰 곳을 고른다
        # (반사 띠 안의 약한 명암 변화를 접지선으로 오인하지 않게)
        best, by = 0.0, None
        for y in range(col.max(), max(lim, col.min() + 10), -1):
            above = g[y - 9:y - 1, x]
            drop = above.max() - g[y, x]
            if above.max() > 170 and drop > 45 and g[y, x] <= g[y - 1:y + 2, x].min() and drop > best:
                best, by = drop, y
        if by is not None:
            X.append(x)
            T.append(by - 1)
    if len(X) < 20:
        return alpha
    X, T = np.array(X, float), np.array(T, float)
    keep = np.ones(len(X), bool)
    for _ in range(5):
        p = np.polyfit(X[keep], T[keep], 4)
        r = np.abs(T - np.polyval(p, X))
        keep = r < max(2.5, 2.5 * np.median(r[keep]))
    h, w = alpha.shape
    xx = np.arange(w, dtype=np.float64)
    # 굽 바닥은 타원 → 앞쪽 호의 점들로 타원을 맞추면 양 끝(위로 휘는 부분)까지 정확
    pts = np.stack([X[keep], T[keep]], 1).astype(np.float32)
    (ecx, ecy), (d1, d2), ang = cv2.fitEllipse(pts)
    ea, eb = max(d1, d2) / 2, min(d1, d2) / 2
    # 굽 타원이 굽 바로 위 몸통보다 훨씬 좁은지(아래 모서리가 둥근 컵 등) 확인
    r0 = int(T[keep].min()) - 12
    bc = np.nonzero(alpha[r0] > 0.5)[0]
    narrow = bool(len(bc)) and 2 * ea < 0.9 * (bc.max() - bc.min())
    if abs(((ang + 90) % 180) - 90) < 15 or abs(((ang) % 180) - 90) < 15:
        # 축이 거의 수평/수직인 타원만 사용: 아래 호 y = cy + b·sqrt(1-((x-cx)/a)^2)
        t = np.clip((xx - ecx) / ea, -1, 1)
        inside = np.abs(xx - ecx) <= ea
        # 타원 끝 바로 바깥(18px)은 끝 높이로 잘라 반사 조각을 없애고,
        # 그보다 바깥은 몸통이므로 자르지 않는다
        edge = (np.abs(xx - ecx) > ea) & (np.abs(xx - ecx) <= ea + 18)
        if narrow:
            # 굽이 몸통보다 좁으면(둥근 아래 모서리), 굽 바깥 모서리 아래는 반사뿐 →
            # 굽 타원 끝 높이에서 몸통 폭 끝까지 모두 자른다
            edge = np.abs(xx - ecx) > ea
        cut = np.where(inside, ecy + eb * np.sqrt(1 - t * t), np.where(edge, ecy, 1e9))
        if narrow:
            # 굽 바깥 열: 몸통 색보다 반사 띠 색(굽 아래에서 잰 색)에 가까워지는 지점부터
            # 자른 뒤, 굽 타원 컷과 이어지게 전체를 매끈하게 다듬는다
            lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2Lab).astype(np.float32)
            ytop = int(T[keep].min())
            ref = np.median(lab[ytop - 40:ytop - 15, int(ecx - ea * 0.6):int(ecx + ea * 0.6)]
                            .reshape(-1, 3), 0)
            ybot = int(ecy + eb)
            band = np.median(lab[ybot + 10:ybot + 35, int(ecx - ea * 0.5):int(ecx + ea * 0.5)]
                             .reshape(-1, 3), 0)
            outs = np.nonzero((np.abs(xx - ecx) > ea) & (alpha > 0.5).any(0))[0]
            for x in outs:
                col = np.nonzero(alpha[:, x] > 0.5)[0]
                y0 = max(ytop - 60, col.min())
                if col.max() - y0 < 6:
                    continue
                px = lab[y0:col.max() + 1, x]
                # 몸통 색보다 반사 띠 색에 더 가까운 픽셀이 4px 이상 이어지는 곳
                is_band = np.linalg.norm(px - band, axis=1) < np.linalg.norm(px - ref, axis=1)
                run = np.convolve(is_band, np.ones(4), "same") >= 4
                hit = np.nonzero(run)[0]
                cut[x] = y0 + hit[0] - 1 if len(hit) else 1e9
            fin = np.nonzero(cut < 1e8)[0]
            c = cut[fin]
            k = 15
            pad = np.pad(c, k, mode="edge")
            c = np.array([np.median(pad[i:i + 2 * k + 1]) for i in range(len(c))])
            c = np.convolve(np.pad(c, 8, mode="edge"), np.ones(17) / 17, "valid")
            # 굽 타원 안쪽은 그대로, 바깥만 매끈하게 다듬은 값으로 (몇 열짜리 튀는 값 제거)
            outside = np.abs(xx[fin] - ecx) > ea
            cut[fin] = np.where(outside, c, cut[fin])
            # 둥근 아래 모서리는 바깥으로 갈수록 올라가기만 한다 → 컷이 바깥에서 다시
            # 내려가지 않게(반사가 뾰족하게 남지 않게)
            # (몇 열짜리 튀는 값은 넓은 중앙값으로 먼저 걸러 낸 뒤 누적 최소)
            l, r = int(np.ceil(ecx - ea)), int(np.floor(ecx + ea))
            for side in (np.arange(l - 1, -1, -1), np.arange(r + 1, w)):
                v = cut[side].copy()
                ok = v < 1e8
                if ok.sum() < 3:
                    continue
                vv = v[ok]
                pad = np.pad(vv, 20, mode="edge")
                vv = np.array([np.median(pad[i:i + 41]) for i in range(len(vv))])
                v[ok] = vv
                run = np.minimum.accumulate(np.where(ok, v, np.inf))
                cut[side] = np.where(np.isfinite(run), run, cut[side])
    else:
        xl, xr = X[keep].min(), X[keep].max()
        cut = np.polyval(p, np.clip(xx, xl, xr))
    yy = np.arange(h, dtype=np.float64)[:, None]
    out = np.minimum(alpha, np.clip(cut[None, :] - yy + 0.5, 0, 1).astype(np.float32))
    # 컷 곡선과 옆 윤곽이 만나는 곳의 작은 홈 메우기 (원래 마스크 범위 안에서만)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))
    return np.minimum(alpha, np.maximum(out, cv2.morphologyEx(out, cv2.MORPH_CLOSE, k)))


def apply(img_bgr, model="birefnet-general", reflection="none"):
    """마스크 생성 + 반사 제거를 한 번에. 반환: 알파(0~1 float32)."""
    alpha = product_mask(img_bgr, model)
    if reflection == "mirror":
        alpha, _, _ = remove_mirror_reflection(img_bgr, alpha)
    elif reflection == "rim":
        alpha = remove_rim_reflection(img_bgr, alpha)
    elif reflection == "crease":
        alpha = remove_crease_reflection(img_bgr, alpha)
    elif reflection == "thin":
        alpha = remove_bottom_reflection(img_bgr, alpha)
    return alpha.astype(np.float32)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("image")
    p.add_argument("--model", default="birefnet-general")
    p.add_argument("--reflection", default="none",
                   choices=["none", "mirror", "rim", "crease", "thin"])
    p.add_argument("--out", default=".")
    a = p.parse_args()
    img = cv2.imread(a.image)
    alpha = apply(img, a.model, a.reflection)
    name = os.path.splitext(os.path.basename(a.image))[0]
    os.makedirs(a.out, exist_ok=True)
    cv2.imwrite(os.path.join(a.out, name + "_mask.png"), (alpha * 255).astype(np.uint8))
    ov = img.copy()
    edge = cv2.Canny(((alpha > 0.5) * 255).astype(np.uint8), 50, 150)
    ov[cv2.dilate(edge, None) > 0] = (255, 0, 255)
    cv2.imwrite(os.path.join(a.out, name + "_overlay.jpg"), ov)
    ys, xs = np.nonzero(alpha > 0.5)
    b = ys.max()
    crop = ov[max(b - 220, 0):b + 80, max(xs.min() - 40, 0):xs.max() + 40]
    cv2.imwrite(os.path.join(a.out, name + "_bottom_300.png"),
                cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST))
    print(name, "bottom row", int(b))


if __name__ == "__main__":
    main()
