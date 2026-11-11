"""도자기 제품 사진 자동 보정 파이프라인.

  원본 사진 → rembg 제품 분리 → 바닥 반사 제거 → 배경색 추천
  → 스튜디오 배경 + 빛 방향에 맞춘 그림자 → 합성

제품 픽셀은 일절 수정하지 않는다(마스크 안쪽은 원본 값을 그대로 복사).
배경색은 기본으로 아이보리(#F2EADE)를 쓴다. 제품 색을 분석한 추천 후보
(톤온톤·보색·포인트·뉴트럴)도 출력하며 --bg auto 등으로 고를 수 있다.

사용법:
    # 위에서 본 사진 (얇은 반사 띠)
    python tools/replace_background.py product.jpg out.jpg
    # 옆모습, 흰 굽 아래 거울 반사
    python tools/replace_background.py product.jpg out.jpg --view side
    # 가격표·아크릴 등 소품이 많고, 어두운 유광 몸체 아래 굽이 비친 경우
    python tools/replace_background.py product.jpg out.jpg --model ensemble --reflection rim
    # 식물 등 얇은 잎이 포함된 연출 사진
    python tools/replace_background.py product.jpg out.jpg --model birefnet-general --reflection none
    # 옆모습: 얇은 흰 굽 선 바로 아래 어두운 접지선 + 반사
    python tools/replace_background.py product.jpg out.jpg --model birefnet-general --reflection crease

    # 배경 형태: 높이가 있는 제품은 자동으로 벽·테이블(split), 접시 등 납작하면 경계 없는 배경(sweep)
    python tools/replace_background.py product.jpg out.jpg --backdrop sweep

    # 한 제품의 여러 각도 사진을 세트로: 캔버스 크기 통일
    python tools/replace_background.py a.jpg a_out.jpg --canvas 1628x2171

    # 배경색 후보 비교 이미지도 함께 저장 / 특정 후보·색 지정
    python tools/replace_background.py product.jpg out.jpg --palette-sheet sheet.jpg
    python tools/replace_background.py product.jpg out.jpg --bg complement
    python tools/replace_background.py product.jpg out.jpg --bg "#E8E2D1"
"""
import argparse
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import studio  # noqa: E402


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
        for y in range(col.max(), max(lim, col.min() + 10), -1):
            above = g[y - 9:y - 1, x]
            if above.max() > 170 and above.max() - g[y, x] > 45 and g[y, x] <= g[y - 1:y + 2, x].min():
                X.append(x)
                T.append(y - 1)
                break
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
    if abs(((ang + 90) % 180) - 90) < 15 or abs(((ang) % 180) - 90) < 15:
        # 축이 거의 수평/수직인 타원만 사용: 아래 호 y = cy + b·sqrt(1-((x-cx)/a)^2)
        t = np.clip((xx - ecx) / ea, -1, 1)
        inside = np.abs(xx - ecx) <= ea
        # 타원 끝 바로 바깥(18px)은 끝 높이로 잘라 반사 조각을 없애고,
        # 그보다 바깥은 몸통이므로 자르지 않는다
        edge = (np.abs(xx - ecx) > ea) & (np.abs(xx - ecx) <= ea + 18)
        cut = np.where(inside, ecy + eb * np.sqrt(1 - t * t), np.where(edge, ecy, 1e9))
    else:
        xl, xr = X[keep].min(), X[keep].max()
        cut = np.polyval(p, np.clip(xx, xl, xr))
    yy = np.arange(h, dtype=np.float64)[:, None]
    out = np.minimum(alpha, np.clip(cut[None, :] - yy + 0.5, 0, 1).astype(np.float32))
    # 컷 곡선과 옆 윤곽이 만나는 곳의 작은 홈 메우기 (원래 마스크 범위 안에서만)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))
    return np.minimum(alpha, np.maximum(out, cv2.morphologyEx(out, cv2.MORPH_CLOSE, k)))


# 기본 배경: 따뜻한 아이보리. 제품과 비슷한 색 배경은 제품이 묻혀 보여서
# 모든 제품에 같은 중립 밝은 색을 쓴다.
IVORY = "#F2EADE"


def place(img, alpha, h, w, max_w=0.76, max_h=0.74, canvas=None):
    """제품을 평행이동만 해서(크기·회전 변경 없음 → 픽셀 재샘플링 없음) 배치.

    가로 중앙, 바닥 닿는 선을 캔버스 80% 높이에. 제품이 커서 여백이 부족하면
    제품을 줄이는 대신 캔버스를 같은 비율로 넓힌다(제품 폭 ≤ 76%, 높이 ≤ 74%).
    반환: (배치된 이미지, 알파) — 캔버스 크기는 원본보다 클 수 있음
    """
    ys, xs = np.nonzero(alpha > 0.5)
    bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    k = max(1.0, bw / (max_w * w), bh / (max_h * h))
    W, H = int(np.ceil(w * k)), int(np.ceil(h * k))
    if canvas:  # 세트 사진 크기 통일용 고정 캔버스 (필요하면 그보다 크게)
        W, H = max(W, canvas[0]), max(H, canvas[1])
    dx = int(round(W / 2 - (xs.min() + xs.max()) / 2))
    dy = int(round(H * 0.80 - ys.max()))
    if ys.min() + dy < H * 0.06:
        dy = int(round(H * 0.06 - ys.min()))
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return (cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_NEAREST),
            cv2.warpAffine(alpha, M, (W, H), flags=cv2.INTER_NEAREST))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("product")
    p.add_argument("output")
    p.add_argument("--bg", default="ivory",
                   help="배경색. ivory(기본, #F2EADE) | auto(제품 색 기반 추천 1순위) | tonal | "
                        "complement | accent | neutral | reference(--bg-ref 이미지 색) | "
                        "1,2,3..(추천 순위) | #RRGGBB")
    p.add_argument("--bg-ref", help="배경 색감 참고 이미지(선택). 주면 후보에 '참고 배경'이 추가됨")
    p.add_argument("--palette-sheet", help="배경색 후보별 결과를 나란히 놓은 비교 이미지 저장 경로")
    p.add_argument("--canvas", help="출력 크기 고정 WxH (예: 1628x2171). 세트 사진 크기 통일용")
    p.add_argument("--backdrop", choices=["auto", "split", "sweep"], default="auto",
                   help="배경 형태. split: 벽과 바닥(테이블)이 경계선으로 나뉨(높이가 있는 제품), "
                        "sweep: 경계 없는 스튜디오(접시 등 납작한 제품). auto: 제품 비율로 자동")
    p.add_argument("--shadow", type=float, default=1.0, help="그림자 진하기 배율 (기본 1.0)")
    p.add_argument("--mask-out", help="알파 마스크 저장 경로(선택)")
    p.add_argument("--view", choices=["top", "side"], default="top",
                   help="top: 위에서 내려다본 사진, side: 옆에서 찍은 사진 "
                        "(--reflection auto 일 때 반사 제거 방식 선택에만 쓰임)")
    p.add_argument("--model", default="isnet-general-use",
                   help="rembg 분할 모델. 배경이 복잡하면(가격표·투명 아크릴 등) "
                        "ensemble(isnet으로 제품 선택 + birefnet 경계)이 가장 정확함. "
                        "식물 등 얇은 잎이 있으면 birefnet-general")
    p.add_argument("--reflection", choices=["auto", "thin", "mirror", "rim", "crease", "none"], default="auto",
                   help="바닥 반사 제거 방식. auto: top→thin, side→mirror. "
                        "rim: 어두운 유광 몸체 아래 밝은 굽 반사(은색 테두리만 남김). "
                        "crease: 얇은 흰 굽 선 바로 아래 어두운 접지선이 보이는 옆모습. "
                        "none: AI 마스크가 이미 굽 끝에서 정확히 끝날 때")
    args = p.parse_args()

    img = cv2.imread(args.product)
    ref = cv2.imread(args.bg_ref) if args.bg_ref else None
    h, w = img.shape[:2]

    alpha = product_mask(img, args.model)
    refl = args.reflection
    if refl == "auto":
        refl = "mirror" if args.view == "side" else "thin"
    if refl == "mirror":
        alpha, _, _ = remove_mirror_reflection(img, alpha)
    elif refl == "thin":
        alpha = remove_bottom_reflection(img, alpha)
    elif refl == "rim":
        alpha = remove_rim_reflection(img, alpha)
    elif refl == "crease":
        alpha = remove_crease_reflection(img, alpha)

    canvas = tuple(int(v) for v in args.canvas.lower().split("x")) if args.canvas else None
    img_t, alpha = place(img, alpha, h, w, canvas=canvas)
    light_x = studio.light_direction(img_t, alpha)
    cands = studio.recommend_backgrounds(img_t, alpha, ref)

    print("배경색 추천:")
    for i, c in enumerate(cands):
        print(f"  {i + 1}. {c['label_ko']:6s} {studio.hex_of(c['wall'])}  --bg {c['name']:10s} {c['note']}")

    bg = IVORY if args.bg == "ivory" else args.bg
    if bg.startswith("#"):
        wall = studio.parse_hex(bg)
        lab = studio.bgr_to_lab(wall)[0]
        # 추천 후보와 같은 규칙: 바닥은 명도 −6, 채도 ×1.25
        floor = studio.lab_to_bgr(np.array([lab[0] - 6, lab[1] * 1.25, lab[2] * 1.25], np.float32))[0]
        chosen = dict(label_ko="아이보리" if args.bg == "ivory" else "직접 지정", wall=wall, floor=floor)
    elif bg == "auto":
        chosen = cands[0]
    elif bg.isdigit():
        chosen = cands[min(int(bg), len(cands)) - 1]
    else:
        match = [c for c in cands if c["name"] == bg]
        if not match:
            p.error(f"--bg {args.bg}: 이 사진의 후보에 없음 ({', '.join(c['name'] for c in cands)})")
        chosen = match[0]
    print(f"사용: {chosen['label_ko']} {studio.hex_of(chosen['wall'])}")

    out = studio.render(img_t, alpha, chosen["wall"], chosen["floor"], light_x, args.shadow,
                        args.backdrop)
    cv2.imwrite(args.output, out, [cv2.IMWRITE_JPEG_QUALITY, 97])
    if args.palette_sheet:
        renders = [studio.render(img_t, alpha, c["wall"], c["floor"], light_x, args.shadow,
                                 args.backdrop)
                   for c in cands]
        studio.palette_sheet(renders, cands, args.palette_sheet)
    if args.mask_out:
        cv2.imwrite(args.mask_out, (alpha * 255).astype(np.uint8))


if __name__ == "__main__":
    main()
