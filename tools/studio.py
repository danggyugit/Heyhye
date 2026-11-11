"""스튜디오 배경 · 그림자 · 배경색 추천.

제품 픽셀은 여기서 절대 바꾸지 않는다. 배경(벽·바닥)과 그림자만 만든다.

- recommend_backgrounds(): 제품 색을 분석해 어울리는 배경색 후보를 순위대로 추천
- render(): 경계선 없는 스튜디오 배경 + 빛 방향에 맞춘 그림자 + 합성
"""
import cv2
import numpy as np


# ---------------------------------------------------------------- 색 변환

def bgr_to_lab(bgr):
    a = np.asarray(bgr, np.float32).reshape(-1, 1, 3) / 255.0
    return cv2.cvtColor(a, cv2.COLOR_BGR2Lab).reshape(-1, 3)


def lab_to_bgr(lab):
    a = np.asarray(lab, np.float32).reshape(-1, 1, 3)
    return np.clip(cv2.cvtColor(a, cv2.COLOR_Lab2BGR).reshape(-1, 3) * 255, 0, 255)


def lch(L, C, h_deg):
    h = np.deg2rad(h_deg)
    return np.array([L, C * np.cos(h), C * np.sin(h)], np.float32)


def hex_of(bgr):
    b, g, r = [int(round(v)) for v in bgr]
    return f"#{r:02X}{g:02X}{b:02X}"


def parse_hex(s):
    s = s.lstrip("#")
    return np.array([int(s[4:6], 16), int(s[2:4], 16), int(s[0:2], 16)], np.float32)


# ---------------------------------------------------------------- 배경색 추천

def product_colors(img_bgr, alpha, k=4):
    """제품 픽셀의 대표 색(Lab)과 비율. 큰 비율 순."""
    px = img_bgr[alpha >= 1.0]
    if len(px) > 60000:
        px = px[np.random.default_rng(0).choice(len(px), 60000, replace=False)]
    lab = bgr_to_lab(px)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 50, 0.5)
    _, lbl, cen = cv2.kmeans(lab.astype(np.float32), k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    w = np.bincount(lbl.ravel(), minlength=k) / len(lbl)
    order = np.argsort(-w)
    return cen[order], w[order], np.median(lab, 0)


def _delta_e(a, b):
    return float(np.linalg.norm(np.asarray(a) - np.asarray(b)))


def recommend_backgrounds(img_bgr, alpha, ref_bgr=None):
    """제품 색에 어울리는 배경 후보를 추천 순으로 반환.

    각 후보: dict(name, label_ko, wall(BGR), floor(BGR), contrast, note)
    - 톤온톤: 제품 주색과 같은 색상(hue)을 아주 연하게 → 차분하고 통일감
    - 보색: 주색의 반대 색상을 아주 연하게 → 제품이 또렷하게 돋보임
    - 포인트: 제품의 두 번째 색(예: 빨간 굽)을 연하게 → 디테일 강조
    - 뉴트럴: 따뜻한 그레이지(또는 참고 이미지 색) → 어떤 제품에도 무난
    배경 밝기는 제품과 명도 차이가 충분하도록 자동 조정한다.
    """
    cen, w, med = product_colors(img_bgr, alpha)
    C = np.hypot(cen[:, 1], cen[:, 2])
    H = np.rad2deg(np.arctan2(cen[:, 2], cen[:, 1])) % 360
    # 주색: 넓게 차지하면서 색이 있는 것 (검정·흰색처럼 채도 없는 색은 제외)
    score = w * np.sqrt(np.clip(C - 6, 0, None))
    main = int(np.argmax(score)) if score.max() > 0 else None
    accent = None
    if main is not None:
        for i in np.argsort(-score):
            # 어두운 색(예: 진녹색 줄무늬)은 Lab 채도가 낮게 나오므로 기준을 낮추고 면적도 본다
            if i != main and score[i] > 0 and C[i] > 7 and w[i] >= 0.10 and \
                    min(abs(H[i] - H[main]), 360 - abs(H[i] - H[main])) > 35:
                accent = int(i)
                break
    Lp = float(med[0])

    def pick_L(h, Cw):
        # 배경이 제품과 충분히 구분되도록(색차 15 이상) 밝은 쪽부터 명도를 고른다
        for L in (90, 94, 86, 81, 76, 71):
            if _delta_e(lch(L, Cw, h), med) >= 15:
                return L
        return 71

    cands = []

    def add(name, label_ko, h, Cw, note):
        L = pick_L(h, Cw)
        wall = lch(L, Cw, h)
        floor = lch(L - 6, Cw * 1.25, h)
        cands.append(dict(name=name, label_ko=label_ko, wall_lab=wall, floor_lab=floor, note=note))

    if main is not None:
        add("tonal", "톤온톤", H[main], 9, "주색과 같은 계열 · 차분한 통일감")
        add("complement", "보색", (H[main] + 180) % 360, 8, "주색의 반대 계열 · 제품이 또렷하게")
        if accent is not None:
            add("accent", "포인트", H[accent], 11, "제품의 포인트 색 · 디테일 강조")
    if ref_bgr is not None:
        r = ref_bgr.astype(np.float32)
        rh = r.shape[0]
        wall_lab = bgr_to_lab(np.median(r[: rh // 2].reshape(-1, 3), 0))[0]
        floor_lab = bgr_to_lab(np.median(r[int(rh * 0.8):].reshape(-1, 3), 0))[0]
        cands.append(dict(name="reference", label_ko="참고 배경", wall_lab=wall_lab,
                          floor_lab=floor_lab, note="참고 배경 이미지의 색"))
    else:
        add("neutral", "뉴트럴", 70, 6, "따뜻한 그레이지 · 어떤 제품에도 무난")

    for c in cands:
        c["wall"] = lab_to_bgr(c["wall_lab"])[0]
        c["floor"] = lab_to_bgr(c["floor_lab"])[0]
        # 제품 각 대표색과 배경의 색차(가중 평균) = 돋보임 정도
        c["contrast"] = float(sum(wi * _delta_e(ci, c["wall_lab"]) for ci, wi in zip(cen, w)))
    # 추천 순서: 톤온톤(충분히 대비될 때) → 보색 → 포인트 → 뉴트럴/참고
    pref = {"tonal": 0, "complement": 1, "accent": 2, "neutral": 3, "reference": 3}
    cands.sort(key=lambda c: (c["contrast"] < 18, pref[c["name"]], -c["contrast"]))
    return cands


# ---------------------------------------------------------------- 빛 방향

def light_direction(img_bgr, alpha):
    """제품의 밝은 반사광/밝은 면 위치로 빛이 오는 쪽(x: -1 왼쪽 ~ +1 오른쪽)."""
    m = alpha >= 1.0
    ys, xs = np.nonzero(m)
    L = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2Lab)[..., 0].astype(np.float32)[m]
    cx, hw = (xs.min() + xs.max()) / 2, max((xs.max() - xs.min()) / 2, 1)
    thr = np.percentile(L, 97)
    hi = (xs[L >= thr] - cx).mean() / hw          # 하이라이트 무게중심
    side = (L[xs > cx].mean() - L[xs < cx].mean()) / 40.0  # 밝은 면
    return float(np.clip(0.6 * hi + 0.4 * side, -1, 1))


# ---------------------------------------------------------------- 발자국(굽 바닥)

def footprint(alpha):
    """마스크 아래 윤곽에서 바닥에 닿는 타원 (cx, cy, a, b) 추정."""
    solid = alpha > 0.5
    ys, xs = np.nonzero(solid)
    top, bot = ys.min(), ys.max()
    low = np.full(solid.shape[1], -1)
    cols = np.nonzero(solid.any(0))[0]
    low[cols] = solid.shape[0] - 1 - np.argmax(solid[::-1, cols], axis=0)
    foot = np.nonzero(low >= bot - 0.06 * (bot - top))[0]
    l, r = foot.min(), foot.max()
    cy = (low[l] + low[r]) / 2
    return (l + r) / 2, cy, (r - l) / 2, max(bot - cy, 4.0), bot


# ---------------------------------------------------------------- 배경 + 그림자

def backdrop(w, h, wall_bgr, floor_bgr, floor_y, light_x):
    """벽과 바닥이 곡선으로 이어지는(경계선 없는) 스튜디오 배경.

    - 벽→바닥 전환을 넓고 부드럽게 해서 어떤 촬영 각도에도 어색하지 않게
    - 빛이 오는 쪽을 살짝 밝게, 제품 뒤쪽에 은은한 빛 웅덩이
    """
    y = np.arange(h, dtype=np.float32)[:, None, None]
    x = np.arange(w, dtype=np.float32)[None, :, None]
    wall = np.asarray(wall_bgr, np.float32)
    floor = np.asarray(floor_bgr, np.float32)
    # 바닥이 '보이기 시작하는' 지점부터 제품이 놓인 줄까지 넓게 섞기
    t0, t1 = floor_y - 0.35 * h, floor_y + 0.05 * h
    t = np.clip((y - t0) / (t1 - t0), 0, 1)
    t = t * t * (3 - 2 * t)
    bg = (wall * (1 - t) + floor * t) * np.ones((1, w, 1), np.float32)
    # 바닥 앞쪽(카메라 쪽)은 아주 조금 밝게 → 깊이감
    near = np.clip((y - floor_y) / (h - floor_y + 1), 0, 1)
    bg *= 1 + 0.03 * near
    # 빛 방향: 빛 쪽 +, 반대쪽 −
    bg *= 1 + 0.035 * light_x * ((x / w) * 2 - 1)
    # 제품 뒤쪽 빛 웅덩이 + 모서리 비네팅
    cx, cy = w / 2 + light_x * w * 0.12, floor_y - 0.25 * h
    d = np.sqrt(((x - cx) / (0.75 * w)) ** 2 + ((y - cy) / (0.75 * h)) ** 2)
    bg *= 1 + 0.04 * np.clip(1 - d, 0, 1) - 0.07 * np.clip(d - 0.7, 0, 1)
    rng = np.random.default_rng(0)
    bg += rng.normal(0, 1.0, (h, w, 1)).astype(np.float32)
    return bg


def body_top(alpha, frac=0.45):
    """몸통 윗선: 폭이 최대 폭의 45% 이상인 가장 위 줄 (식물 잎·손잡이 끝 등 제외)."""
    solid = alpha > 0.5
    rows = np.nonzero(solid.any(1))[0]
    widths = np.array([np.ptp(np.nonzero(solid[r])[0]) for r in rows])
    return int(rows[np.argmax(widths >= frac * widths.max())])


def seam_y(alpha, k=0.58):
    """벽-바닥 경계선 높이.

    참고 사진(머그 정면)처럼 바닥에서 몸통 높이의 약 58% 위에 둔다.
    식물·손잡이처럼 얇게 솟은 부분은 높이 계산에서 뺀다.
    굽 타원이 아주 둥글면(위에서 크게 내려다본 사진) 경계선을 제품 위쪽 뒤로 올린다.
    """
    h = alpha.shape[0]
    _, _, a, b, bot = footprint(alpha)
    top = body_top(alpha)
    H = bot - top
    if b / max(a, 1) > 0.45:
        k = 1.05
    return float(np.clip(bot - k * H, h * 0.10, bot - 0.25 * H))


def backdrop_split(w, h, wall_bgr, floor_bgr, seam, floor_y, light_x):
    """벽과 테이블(바닥)이 경계선으로 나뉘는 배경 (높이가 있는 제품용).

    - 경계선은 부드럽지만 또렷하게(높이의 약 1%)
    - 경계선 바로 아래 바닥은 조금 어둡고 카메라 쪽으로 갈수록 밝게 → 깊이감
    - 벽은 위로 갈수록 아주 살짝 밝게, 빛 반대쪽은 좀 더 어둡게
    """
    y = np.arange(h, dtype=np.float32)[:, None, None]
    x = np.arange(w, dtype=np.float32)[None, :, None]
    wall = np.asarray(wall_bgr, np.float32)
    floor = np.asarray(floor_bgr, np.float32)
    bw = max(h * 0.006, 2.0)
    t = np.clip((y - seam) / (2 * bw) + 0.5, 0, 1)
    t = t * t * (3 - 2 * t)
    depth = np.clip((y - seam) / max(h - seam, 1), 0, 1)       # 0: 경계선 → 1: 맨 앞
    floor_col = floor * (0.97 + 0.05 * depth ** 0.7)
    wall_col = wall * (1 + 0.02 * np.clip((seam - y) / max(seam, 1), 0, 1))
    bg = (wall_col * (1 - t) + floor_col * t) * np.ones((1, w, 1), np.float32)
    bg *= 1 + 0.05 * light_x * ((x / w) * 2 - 1)
    cx = w / 2 + light_x * w * 0.15
    d = np.sqrt(((x - cx) / (0.8 * w)) ** 2 + ((y - floor_y) / (0.9 * h)) ** 2)
    bg *= 1 + 0.03 * np.clip(1 - d, 0, 1) - 0.06 * np.clip(d - 0.75, 0, 1)
    rng = np.random.default_rng(0)
    bg += rng.normal(0, 1.0, (h, w, 1)).astype(np.float32)
    return bg


def choose_backdrop(alpha):
    """높이가 있는 제품(높이/폭 ≥ 0.45) → split, 접시처럼 납작하면 → sweep."""
    ys, xs = np.nonzero(alpha > 0.5)
    ratio = (ys.max() - ys.min()) / max(xs.max() - xs.min(), 1)
    return "split" if ratio >= 0.45 else "sweep"


def shadows(alpha, light_x, strength=1.0, cast=1.0):
    """그림자 농도 맵(0~1). 세 겹:

    1) 접지 그림자: 굽이 바닥에 닿는 선 바로 주변, 짧고 진하게 (붙어 있는 느낌의 핵심)
    2) 주변광 차폐: 굽 둘레로 넓고 옅게
    3) 드리운 그림자: 실루엣을 바닥에 눕혀 빛 반대쪽 뒤로 비스듬히,
       굽에서 멀어질수록 더 흐리고 옅게
    """
    h, w = alpha.shape
    cx, cy, a, b, bot = footprint(alpha)
    ys, xs = np.nonzero(alpha > 0.5)
    H = bot - ys.min()

    def ell(sa, sb, sigma, dy=0.0):
        m = np.zeros((h, w), np.float32)
        cv2.ellipse(m, (int(round(cx)), int(round(cy + dy))),
                    (max(int(a * sa), 1), max(int(b * sb), 1)), 0, 0, 360, 1.0, -1, cv2.LINE_AA)
        return cv2.GaussianBlur(m, (0, 0), max(sigma, 0.5))

    # 접지/근접 그림자는 타원 대신 '실제 바닥 윤곽' 바로 아래에만 만든다
    # (타원이 윤곽보다 옆으로 삐져나와 얼룩처럼 보이는 것 방지)
    sil = (alpha > 0.5).astype(np.float32)
    yy = np.arange(h, dtype=np.float32)[:, None]
    floor_w = np.clip((yy - (bot - 2.2 * b - a * 0.04)) / (a * 0.05 + 1), 0, 1)

    def under(px, sigma):
        M = np.float32([[1, 0, 0], [0, 1, px]])
        sh = cv2.warpAffine(sil, M, (w, h), flags=cv2.INTER_LINEAR) * floor_w
        return cv2.GaussianBlur(sh, (0, 0), max(sigma, 0.5))

    contact = under(a * 0.006, a * 0.008)
    near = under(a * 0.02, a * 0.035)
    ambient = ell(1.25, 2.0, a * 0.18)

    # 드리운 그림자: 높이 hgt 인 점을 (뒤로) sy*hgt 만큼, (옆으로) shear*hgt 만큼 이동
    dirx = -np.sign(light_x) if abs(light_x) > 0.08 else 0.0
    # cast>1 이면 그림자가 더 길고(옆·뒤로) 진해진다
    shear = 0.55 * min(abs(light_x) * 1.6, 1.0) * dirx * (1 + 0.6 * (cast - 1))
    sy = 0.22 * (1 + 0.5 * (cast - 1))
    M = np.float32([[1, -shear, shear * bot], [0, sy, (1 - sy) * bot]])
    proj = cv2.warpAffine((alpha > 0.5).astype(np.float32), M, (w, h), flags=cv2.INTER_LINEAR)
    dist = np.clip((bot - yy) / (sy * H + 1), 0, 1)       # 0: 굽 근처 → 1: 그림자 끝
    blurs = [cv2.GaussianBlur(proj, (0, 0), s) for s in (a * 0.05, a * 0.11, a * 0.22)]
    k = dist * 2
    cast_map = np.where(k < 1, blurs[0] * (1 - k) + blurs[1] * k,
                        blurs[1] * (2 - k) + blurs[2] * (k - 1))
    cast_map *= (1 - dist) ** 1.3

    # 위에서 오는 빛에 의한 몸통 그늘: 제품 최대 폭만큼 넓은 타원을 굽 위치 바닥에 깔고
    # 크게 흐리게 (몸통이 테이블을 가리는 만큼 어두워짐 — 떠 보임을 없애는 핵심)
    maxw = max(np.ptp(np.nonzero(sil[r])[0]) for r in range(ys.min(), bot + 1, 4)
               if sil[r].any()) / 2
    pr = b / max(a, 1)                            # 원근(바닥 타원의 납작함)
    body = np.zeros((h, w), np.float32)
    cv2.ellipse(body, (int(round(cx)), int(round(cy))),
                (max(int(maxw * 0.9), 1), max(int(maxw * 0.9 * pr), 1)), 0, 0, 360, 1.0, -1,
                cv2.LINE_AA)
    body = cv2.GaussianBlur(body, (0, 0), maxw * 0.22)

    s = (contact * 0.55 + near * 0.30 * cast ** 0.5 + ambient * 0.15 * cast
         + cast_map * 0.24 * cast + body * 0.22 * (cast - 1))
    return np.clip(s * strength, 0, 0.8)


def reflection(img_t, alpha, strength=0.12, length=0.28):
    """반광택 테이블에 비친 제품 (배경에만 그림).

    열마다 실제 바닥 윤곽(가장 아래 점)을 기준으로 제품을 위아래로 뒤집어
    그 아래에 놓고, 멀어질수록 옅고 흐리게 한다. 반환: (반사 이미지, 농도 맵)
    """
    h, w = alpha.shape
    solid = alpha > 0.5
    ys = np.nonzero(solid)[0]
    top, bot = ys.min(), ys.max()
    L = max(length * (bot - top), 1.0)
    low = np.full(w, -1, np.float32)
    cols = np.nonzero(solid.any(0))[0]
    low[cols] = h - 1 - np.argmax(solid[::-1, cols], axis=0)
    # 거울면 = 테이블 면: 굽이 닿는 열은 실제 바닥 윤곽, 그 바깥(몸통 옆면)은 굽 바닥 높이.
    # 옆면 윤곽을 기준으로 뒤집으면 몸통 옆에 줄무늬가 생기므로 쓰지 않는다
    cx, cy, a, b, _ = footprint(alpha)
    xx = np.arange(w, dtype=np.float32)
    in_foot = (np.abs(xx - cx) <= a) & (low >= cy - 2)
    low = np.where(in_foot, low, cy).astype(np.float32)
    yy = np.arange(h, dtype=np.float32)[:, None]
    map_y = (2 * low[None, :] - yy).astype(np.float32)
    map_x = np.repeat(np.arange(w, dtype=np.float32)[None, :], h, 0)
    refl = cv2.remap(img_t, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    ra = cv2.remap(alpha.astype(np.float32), map_x, map_y, cv2.INTER_LINEAR,
                   borderMode=cv2.BORDER_CONSTANT)
    d = np.clip((yy - low[None, :]) / L, 0, 1)
    ra = ra * (yy > low[None, :]) * (1 - d) ** 2 * strength
    # 테이블 결 때문에 비친 모습은 살짝 흐리게
    refl = cv2.GaussianBlur(refl.astype(np.float32), (0, 0), 2.5)
    ra = cv2.GaussianBlur(ra, (0, 0), 1.5)
    return refl, ra


def render(img_t, alpha, wall_bgr, floor_bgr, light_x, shadow_strength=1.0, style="auto",
           cast=1.0, reflect=0.0):
    """배경 생성 + 그림자 + 합성. alpha==1 인 제품 픽셀은 원본 그대로.

    style: "split"(벽·바닥 경계), "sweep"(경계 없는 스튜디오), "auto"(제품 비율로 선택)
    """
    h, w = alpha.shape
    _, _, _, _, bot = footprint(alpha)
    if style == "auto":
        style = choose_backdrop(alpha)
    if style == "split":
        # 참고 사진처럼 테이블은 벽보다 명도 3.5 정도만 낮고 살짝 따뜻하게
        L, A, B = bgr_to_lab(wall_bgr)[0]
        floor_bgr = lab_to_bgr(np.array([L - 3.5, A * 1.05, B * 1.05 + 2.5], np.float32))[0]
        bg = backdrop_split(w, h, wall_bgr, floor_bgr, seam_y(alpha), bot, light_x)
    else:
        bg = backdrop(w, h, wall_bgr, floor_bgr, bot, light_x)
    s = shadows(alpha, light_x, shadow_strength, cast)[..., None]
    # 그림자 색: 바닥색을 어둡고 약간 진하게 (회색이 아닌 같은 톤의 그늘)
    shade = np.asarray(floor_bgr, np.float32) * 0.42
    bg = bg * (1 - s) + shade * s
    if reflect > 0:
        refl, ra = reflection(img_t, alpha, reflect)
        bg = bg * (1 - ra[..., None]) + refl * ra[..., None]
    a = alpha[..., None]
    out = img_t.astype(np.float32) * a + bg * (1 - a)
    out = np.clip(np.rint(out), 0, 255).astype(np.uint8)
    inside = alpha >= 1.0
    out[inside] = img_t[inside]  # 제품 픽셀 1비트도 안 바뀌게 보장
    return out


def palette_sheet(renders, cands, path, thumb_w=360):
    """후보별 결과를 나란히 놓고 순위·이름·색상 코드·설명을 붙인 비교 이미지."""
    from PIL import Image, ImageDraw, ImageFont
    fp = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
    try:
        f1, f2 = ImageFont.truetype(fp, 21), ImageFont.truetype(fp, 15)
    except OSError:
        f1 = f2 = ImageFont.load_default()
    tiles = []
    for i, (im, c) in enumerate(zip(renders, cands)):
        hh = int(im.shape[0] * thumb_w / im.shape[1])
        t = cv2.resize(im, (thumb_w, hh), interpolation=cv2.INTER_AREA)
        pil = Image.new("RGB", (thumb_w, hh + 96), (250, 250, 248))
        pil.paste(Image.fromarray(cv2.cvtColor(t, cv2.COLOR_BGR2RGB)), (0, 0))
        d = ImageDraw.Draw(pil)
        d.rectangle([12, hh + 12, 52, hh + 52], fill=tuple(int(v) for v in c["wall"][::-1]),
                    outline=(150, 150, 150))
        rank = "추천" if i == 0 else f"{i + 1}순위"
        d.text((64, hh + 10), f"{rank} · {c['label_ko']}", fill=(30, 30, 30), font=f1)
        d.text((64, hh + 37), f"{hex_of(c['wall'])}  (--bg {c['name']})", fill=(100, 100, 100), font=f2)
        d.text((12, hh + 66), c["note"], fill=(80, 80, 80), font=f2)
        tiles.append(np.asarray(pil))
    gap = np.full((tiles[0].shape[0], 8, 3), 255, np.uint8)
    sheet = np.hstack([x for t in tiles for x in (t, gap)][:-1])
    Image.fromarray(sheet).save(path, quality=92)
