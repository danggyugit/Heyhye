"""도자기 제품 사진의 배경 교체 스크립트.

제품 픽셀은 일절 수정하지 않는다. 마스크 안쪽 픽셀은 원본 값을 그대로
복사하고, 배경만 참고 이미지의 색감으로 새로 만든다. 바닥에 비친 반사 띠는
마스크에서 제외하고, 제품 실루엣으로 만든 자연스러운 접지 그림자를 더한다.

사용법:
    python tools/replace_background.py product.jpg bg_reference.jpg out.jpg            # 위에서 본 사진
    python tools/replace_background.py product.jpg bg_reference.jpg out.jpg --view side  # 옆모습(거울 반사)
    # 배경에 가격표·투명 아크릴 등 소품이 많고, 어두운 유광 몸체 아래 굽이 비친 경우
    python tools/replace_background.py product.jpg bg_reference.jpg out.jpg --view side \\
        --model ensemble --reflection rim
"""
import argparse

import cv2
import numpy as np


def _rembg(img_bgr, model):
    from PIL import Image
    from rembg import new_session, remove

    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    m = remove(Image.fromarray(rgb), session=new_session(model), only_mask=True)
    return np.asarray(m, dtype=np.float32) / 255.0


def _clean(a):
    """가장 큰 덩어리만 남기고, 붙어온 얇은 조각 제거, 작은 내부 구멍 메우기."""
    a = a.copy()
    n, lab, stats, _ = cv2.connectedComponentsWithStats((a > 0.5).astype(np.uint8))
    body = (lab == 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])).astype(np.uint8)
    # 제품 옆에 붙어 딸려온 얇은 조각(투명 아크릴 가격표 등) 제거:
    # 굵은 몸체만 남기는 opening 후, 원래 윤곽은 그 주변 몇 px 안에서만 유지
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))
    core = cv2.dilate(cv2.morphologyEx(body, cv2.MORPH_OPEN, k), np.ones((7, 7), np.uint8))
    body &= core
    n, lab, stats, _ = cv2.connectedComponentsWithStats(body)
    body = (lab == 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])).astype(np.uint8)
    # 유광 제품의 밝은 반사광이 구멍으로 빠진 곳 메우기 (작은 내부 구멍만)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(1 - body)
    for i in range(n):
        x, y, bw, bh, area = stats[i]
        touches = x == 0 or y == 0 or x + bw == body.shape[1] or y + bh == body.shape[0]
        if not touches and area < 0.01 * body.sum():
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
        a, _ = _clean(_rembg(img_bgr, model))
    # 경계 바깥 1~2px만 부드럽게, 안쪽은 확실히 1.0 (제품 원본 유지)
    a[a > 0.92] = 1.0
    a[a < 0.04] = 0.0
    return a


def make_background(ref_bgr, w, h, horizon):
    """참고 배경 색감으로 '벽 + 바닥' 스튜디오 배경을 만든다.

    원본 사진과 같은 원근(벽과 바닥이 만나는 선)을 유지해야 제품이
    바닥 위에 놓인 것처럼 보인다.
    """
    rh = ref_bgr.shape[0]
    ref = ref_bgr.astype(np.float32)
    wall = np.median(ref[: int(rh * 0.5)].reshape(-1, 3), 0)
    floor_far = np.median(ref[int(rh * 0.83): int(rh * 0.88)].reshape(-1, 3), 0)
    floor_near = np.median(ref[int(rh * 0.95):].reshape(-1, 3), 0)
    y = np.arange(h, dtype=np.float32)
    t = np.clip((y - horizon) / (h - horizon), 0, 1)[:, None]
    floor = floor_far * (1 - t) + floor_near * t
    wall_col = wall * (1 + 0.015 * (1 - y / max(horizon, 1)))[:, None]  # 위로 갈수록 살짝 밝게
    s = np.clip((y - horizon) / (h * 0.03) + 0.5, 0, 1)[:, None]          # 벽-바닥 경계 부드럽게
    s = s * s * (3 - 2 * s)
    col = wall_col * (1 - s) + floor * s
    bg = np.repeat(col[:, None, :], w, axis=1)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h * 0.6) / (h / 2)) ** 2)
    bg *= (1.0 - 0.06 * np.clip(d - 0.35, 0, 1))[..., None]               # 은은한 비네팅
    rng = np.random.default_rng(0)
    bg += rng.normal(0, 1.2, (h, w, 1)).astype(np.float32)               # 밴딩 방지 그레인
    return bg


def add_contact_shadow(bg, alpha):
    """제품 바닥 테두리를 따라가는 접지 그림자.

    임의의 타원 대신 제품 실루엣을 아래로 몇 px 내린 뒤 블러해서,
    바닥 곡선 바로 아래에만 얇게 생기는 그림자를 만든다.
    옆면 쪽으로는 점점 사라지게 해서 옆으로 튀어나오지 않는다.
    """
    h, w = alpha.shape
    ys, xs = np.nonzero(alpha > 0.5)
    bot = ys.max()
    rw = (xs.max() - xs.min()) / 2

    def drop(px, sigma):
        M = np.float32([[1, 0, 0], [0, 1, px]])
        sh = cv2.warpAffine(alpha, M, (w, h), flags=cv2.INTER_LINEAR)
        return cv2.GaussianBlur(sh, (0, 0), sigma)

    contact = drop(rw * 0.008, rw * 0.008)   # 테두리에 딱 붙은 짙은 선
    ambient = drop(rw * 0.045, rw * 0.06)    # 그 아래 옅게 퍼지는 그늘
    # 바닥에 가까운 높이에서만 그림자가 생기도록 (옆면 위쪽은 0)
    yy = np.arange(h, dtype=np.float32)[:, None]
    band = rw * 0.35
    weight = np.clip((yy - (bot - band)) / band, 0, 1) ** 1.5
    s = np.clip((contact * 0.70 + ambient * 0.42) * weight, 0, 0.6)
    tint = np.array([0.55, 0.62, 0.70], np.float32)  # 배경과 같은 따뜻한 톤 (BGR 곱)
    return bg * (1 - s[..., None] * (1 - tint))


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
    '밝고 채도 낮은 흰 띠'가 끝나는 지점을 열마다 찾고, 그 점들에
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
        run = 0
        for y in range(max(col.min(), bot - int(0.45 * (bot - top))), col.max() + 1):
            if white[y, x]:
                run += 1
            else:
                if run >= 4:
                    pts.append((x, y))
                    break
                run = 0
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


def foot_from_mask(alpha):
    """반사 제거가 필요 없을 때: 마스크 아래 윤곽에서 굽 바닥 타원을 추정.

    열마다 가장 아래 점을 구해, 맨 아래 근처(제품 높이의 6% 이내)에 있는
    열들을 굽 바닥 호로 본다. 그 좌우 끝 = 타원 가로 끝, 호의 깊이 = 세로 반지름.
    """
    solid = alpha > 0.5
    ys, xs = np.nonzero(solid)
    top, bot = ys.min(), ys.max()
    low = np.full(solid.shape[1], -1)
    cols = np.nonzero(solid.any(0))[0]
    low[cols] = solid.shape[0] - 1 - np.argmax(solid[::-1, cols], axis=0)
    foot = np.nonzero(low >= bot - 0.06 * (bot - top))[0]
    l, r = foot.min(), foot.max()
    cy = (low[l] + low[r]) / 2
    return ((l + r) / 2, cy, (r - l) / 2, max(bot - cy, 1.0))


def add_base_shadow(bg, alpha, foot, dx, dy):
    """옆모습 제품용: 굽 바닥 타원(발자국)에서 만든 접지 그림자.

    굽 바닥 타원 둘레로만 짧고 옅게 그림자를 깐다.
    제품에 가려지는 부분은 자동으로 안 보인다.
    """
    h, w = alpha.shape
    cx, cy, a, b = foot
    cx, cy = cx + dx, cy + dy

    def ell(scale_a, scale_b, sigma):
        m = np.zeros((h, w), np.float32)
        cv2.ellipse(m, (int(cx), int(cy)), (int(a * scale_a), int(b * scale_b)),
                    0, 0, 360, 1.0, -1, cv2.LINE_AA)
        return cv2.GaussianBlur(m, (0, 0), sigma)
    contact = ell(1.04, 1.15, a * 0.03)
    ambient = ell(1.45, 1.9, a * 0.14)
    s = np.clip(contact * 0.75 + ambient * 0.35, 0, 0.7)
    tint = np.array([0.55, 0.62, 0.70], np.float32)
    return bg * (1 - s[..., None] * (1 - tint))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("product")
    p.add_argument("bg_reference")
    p.add_argument("output")
    p.add_argument("--mask-out", help="알파 마스크 저장 경로(선택)")
    p.add_argument("--view", choices=["top", "side"], default="top",
                   help="top: 위에서 내려다본 사진(얇은 반사 띠), "
                        "side: 옆에서 찍은 사진")
    p.add_argument("--model", default="isnet-general-use",
                   help="rembg 분할 모델. 배경이 복잡하면(가격표·투명 아크릴 등) "
                        "ensemble(isnet으로 제품 선택 + birefnet 경계)이 가장 정확함")
    p.add_argument("--reflection", choices=["auto", "thin", "mirror", "rim", "none"], default="auto",
                   help="바닥 반사 제거 방식. auto: top→thin, side→mirror. "
                        "rim: 어두운 유광 몸체 아래 밝은 굽 반사(은색 테두리만 남김). "
                        "none: AI 마스크가 이미 굽 끝에서 정확히 끝날 때")
    args = p.parse_args()

    img = cv2.imread(args.product)
    ref = cv2.imread(args.bg_reference)
    h, w = img.shape[:2]

    alpha = product_mask(img, args.model)
    q = None
    refl = args.reflection
    if refl == "auto":
        refl = "mirror" if args.view == "side" else "thin"
    if refl == "mirror":
        alpha, q, foot_x = remove_mirror_reflection(img, alpha)
    elif refl == "thin":
        alpha = remove_bottom_reflection(img, alpha)
    elif refl == "rim":
        alpha = remove_rim_reflection(img, alpha)

    # 제품을 가로 중앙으로 평행이동만 한다(크기 변경/회전 없음 → 픽셀 재샘플링 없음)
    ys, xs = np.nonzero(alpha > 0.5)
    dx = int(round(w / 2 - (xs.min() + xs.max()) / 2))
    if args.view == "side":
        dy = int(round(h * 0.55 - (ys.min() + ys.max()) / 2))  # 세로 중앙보다 살짝 아래
    else:
        dy = int(round(h * 0.86 - ys.max()))  # 바닥선을 캔버스 86% 높이에
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    img_t = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_NEAREST)
    alpha = cv2.warpAffine(alpha, M, (w, h), flags=cv2.INTER_NEAREST)

    ys, xs = np.nonzero(alpha > 0.5)
    if args.view == "side":
        # 원본 사진처럼 벽-바닥 경계가 제품 높이 중간쯤 뒤로 지나가게
        horizon = int(ys.min() + 0.51 * (ys.max() - ys.min()))
        if q is None:
            q = foot_from_mask(alpha)
            dx_, dy_ = 0, 0
        else:
            dx_, dy_ = dx, dy
        bg = add_base_shadow(make_background(ref, w, h, horizon), alpha, q, dx_, dy_)
    else:
        horizon = int(ys.min() - h * 0.06)  # 위에서 본 사진: 제품 뒤쪽 위에 경계
        bg = add_contact_shadow(make_background(ref, w, h, horizon), alpha)
    a = alpha[..., None]
    out = img_t.astype(np.float32) * a + bg * (1 - a)
    out = np.clip(np.rint(out), 0, 255).astype(np.uint8)
    # 마스크 안쪽은 원본 값을 그대로 덮어써서 1비트도 바뀌지 않게 보장
    inside = alpha >= 1.0
    out[inside] = img_t[inside]

    cv2.imwrite(args.output, out, [cv2.IMWRITE_JPEG_QUALITY, 97])
    if args.mask_out:
        cv2.imwrite(args.mask_out, (alpha * 255).astype(np.uint8))


if __name__ == "__main__":
    main()
