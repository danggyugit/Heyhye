"""스튜디오 촬영 느낌으로 재구성 — 넓은 여백, 웜그레이 무경계 배경, 옆에서 오는 빛,
바닥에 길게 눕는 부드러운 사선 그림자 (참고 사진의 분위기).

catalog_shot.py 와 달리 구도를 새로 잡는다:
  - 정사각 캔버스에 제품을 작게(기본: 캔버스 높이의 40%) 배치 → 제품은 축소됨
  - 배경: 가운데가 밝고 가장자리·위쪽으로 어두워지는 웜그레이 벽이 밝은 바닥으로
    경계 없이 이어짐 (studio.backdrop_studio, 참고 사진에서 측정한 값)
  - 그림자: catalog_shot 의 접촉 그림자 + AO + 제품 단면을 바닥에 눕힌 사선 그림자
  - 제품: 빛 쪽은 살짝 밝게, 반대쪽은 어둡게 음영을 입히고(--shade), 바닥 근처 어둡게,
    흰 부분의 푸른 끼 제거, 가장자리 라이트 랩 (catalog_shot.harmonize_product)

사용법:
    python tools/studio_shot.py samples/wave_side_original.jpg --reflection crease
결과: samples/studio/<이름>_studio.jpg, <이름>_compare.jpg (원본 | 결과)
"""
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import catalog_shot as cs  # noqa: E402
import replace_background as rb  # noqa: E402
import studio  # noqa: E402


def build_parser():
    p = cs.build_parser()
    p.description = __doc__
    g = p.add_argument_group("스튜디오 구도")
    g.add_argument("--out-size", type=int, default=2000, help="결과 이미지 한 변(px, 정사각)")
    g.add_argument("--product-height", type=float, default=0.40,
                   help="제품 높이가 캔버스 높이에서 차지하는 비율")
    g.add_argument("--product-width", type=float, default=0.36,
                   help="제품 폭의 최대 비율(넓은 그릇이 너무 커지지 않게)")
    g.add_argument("--contact", type=float, default=0.86, help="바닥 닿는 선 높이(캔버스 비율)")
    g.add_argument("--offset-x", type=float, default=0.04,
                   help="제품을 빛 쪽으로 살짝 옮겨 그림자 자리를 확보(캔버스 폭 비율)")
    g.add_argument("--wall", default="#E0D7D0", help="스튜디오 벽 가운데 색(웜그레이)")
    g.add_argument("--horizon", type=float, default=0.10,
                   help="벽→바닥 전환 위치: 바닥 닿는 선보다 캔버스 높이의 몇 배 위")
    g.add_argument("--horizon-soft", type=float, default=0.26,
                   help="벽→바닥이 섞이는 폭(캔버스 높이 비율). 클수록 경계 없이 부드럽게")
    g.add_argument("--no-camera-match", dest="camera_match", action="store_false",
                   help="배경·그림자를 제품 사진의 카메라 높이에 맞추지 않고 고정값 사용")
    g.add_argument("--light-elevation", type=float, default=38.0,
                   help="빛의 고도(도). 낮을수록 그림자가 길다")
    g.add_argument("--light-back", type=float, default=0.5,
                   help="그림자가 옆(1) 대비 뒤쪽으로 향하는 비율")
    g.add_argument("--bg-texture", type=float, default=0.5,
                   help="배경 표면 결·입자 세기(0=매끈). 제품 사진의 입자감과 맞춰 합성 티를 줄임")
    g.add_argument("--grade", type=float, default=1.0,
                   help="제품+배경 전체에 같은 색 보정(약한 대비·따뜻한 색조)을 입히는 정도")
    g.add_argument("--dehighlight", type=float, default=0.0,
                   help="제품 표면의 좁은 세로 줄무늬 하이라이트(창문 반사)를 눌러 주는 정도 0~1. "
                        "제품 광택이 달라지므로 사진별로 확인하고 쓸 것")
    g.add_argument("--fill-holes", action="store_true",
                   help="제품 안쪽 구멍(화분 바닥 물구멍 등)을 제품으로 유지")
    g.add_argument("--relight-inputs",
                   help="이 폴더에 relight.py 입력(마스크·빈 배경·합성 PNG)을 함께 저장")
    g.add_argument("--shade", type=float, default=0.12,
                   help="제품에 입히는 방향성 음영(빛 반대쪽이 어두워지는 정도)")
    p.set_defaults(out_dir="samples/studio", light="right", reflect=0.0,
                   shadow_tint="0.46,0.49,0.53", cast_length=1.15, cast_rise=0.28,
                   cast_strength=1.0, cast_blur=0.07, cast_fade=0.4,
                   contact_strength=0.95, contact_size=0.06, ao_strength=0.2, ao_size=0.3,
                   wrap=0.3, product_ao=0.36, shade=0.16, cast_texture=0.12)
    return p


def directional_shade(prod, alpha, light_x, amount):
    """빛 쪽은 조금 밝게, 반대쪽은 어둡게 — 둥근 물체가 옆빛을 받은 것처럼."""
    ys, xs = np.nonzero(alpha > 0.5)
    h, w = alpha.shape
    # 행마다 제품의 좌우 끝을 기준으로 -1(왼쪽 끝) ~ +1(오른쪽 끝)
    left = np.full(h, -1.0, np.float32)
    right = np.full(h, -1.0, np.float32)
    rows = np.nonzero((alpha > 0.5).any(1))[0]
    sol = alpha > 0.5
    left[rows] = np.argmax(sol[rows], axis=1)
    right[rows] = w - 1 - np.argmax(sol[rows][:, ::-1], axis=1)
    k = max(int((ys.max() - ys.min()) * 0.05) | 1, 3)
    left = cv2.GaussianBlur(left.reshape(-1, 1), (1, k * 4 + 1), k).ravel()
    right = cv2.GaussianBlur(right.reshape(-1, 1), (1, k * 4 + 1), k).ravel()
    x = np.arange(w, dtype=np.float32)[None, :]
    u = np.clip((x - left[:, None]) / np.maximum(right[:, None] - left[:, None], 1) * 2 - 1, -1, 1)
    u = u * (1.0 if light_x >= 0 else -1.0)                  # +1 = 빛 쪽
    # 빛 쪽 +35% · 반대쪽 −100% 비율의 부드러운 곡선 (원통 음영에 가깝게)
    gain = 1 + amount * np.where(u > 0, 0.35 * u, u * (0.6 + 0.4 * -u))
    return prod * gain[..., None]


def suppress_highlights(img, alpha, amount):
    """좁은 세로 줄무늬 하이라이트(매장 창문 반사)를 주변 유약의 밝기·색으로 눌러 준다.

    밝기: 어두운 반점을 먼저 메운 뒤(closing), 가로로 넓은 커널의 opening 으로
    '폭이 좁은 밝은 구조'만 골라낸다 → 가로 띠(유약 경계)나 넓고 부드러운 밝은 면은 남는다.
    색: 하이라이트 자리는 주변(하이라이트가 아닌 곳)의 색으로 되돌린다.
    """
    m = alpha > 0.5
    ys, xs = np.nonzero(m)
    pw, ph = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    lab = cv2.cvtColor(img.astype(np.float32) / 255.0, cv2.COLOR_BGR2Lab)
    L = lab[..., 0]
    Lm = np.where(m, L, 0).astype(np.float32)
    k_spk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (max(int(pw * 0.03) | 1, 3),) * 2)
    Lc = cv2.morphologyEx(Lm, cv2.MORPH_CLOSE, k_spk)
    k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                       (max(int(pw * 0.16) | 1, 5), max(int(ph * 0.012) | 1, 3)))
    spec = np.clip(Lc - cv2.morphologyEx(Lc, cv2.MORPH_OPEN, k_open), 0, None) * m
    # 세로로 긴 것만 남긴다(물결 테두리처럼 가로로 얇은 밝은 부분은 건드리지 않게)
    k_v = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(int(ph * 0.05) | 1, 5)))
    spec = cv2.morphologyEx(spec.astype(np.float32), cv2.MORPH_OPEN, k_v)
    w = np.clip((spec - 4) / 10, 0, 1)                               # 4 L 이하는 무시
    w = cv2.GaussianBlur(w.astype(np.float32), (0, 0), max(pw * 0.004, 0.8))
    keep = ((w < 0.05) & m).astype(np.float32)
    sig = pw * 0.05
    den = cv2.GaussianBlur(keep, (0, 0), sig) + 1e-4
    for c in (0, 1, 2):                                              # 밝기·색을 주변 값으로
        base = cv2.GaussianBlur(lab[..., c] * keep, (0, 0), sig) / den
        lab[..., c] += (base - lab[..., c]) * w * amount
    out = cv2.cvtColor(lab, cv2.COLOR_Lab2BGR) * 255
    res = img.astype(np.float32).copy()
    res[m] = out[m]
    return np.clip(res, 0, 255).astype(np.uint8)


def surface_texture(S, amount):
    """배경지·회벽 같은 은은한 표면 결(곱하기용, 평균 1). 고운 입자 + 느린 얼룩."""
    rng = np.random.default_rng(11)
    fine = cv2.GaussianBlur(rng.normal(0, 1, (S, S)).astype(np.float32), (0, 0), 0.9)
    mid = cv2.resize(rng.normal(0, 1, (S // 6, S // 6)).astype(np.float32), (S, S),
                     interpolation=cv2.INTER_CUBIC)
    slow = cv2.resize(rng.normal(0, 1, (S // 120, S // 120)).astype(np.float32), (S, S),
                      interpolation=cv2.INTER_CUBIC)
    t = 0.012 * fine / fine.std() + 0.010 * mid / mid.std() + 0.008 * slow / slow.std()
    return 1 + amount * t


def main():
    P = build_parser().parse_args()
    name = P.name or os.path.splitext(os.path.basename(P.product))[0].replace("_original", "")
    os.makedirs(P.out_dir, exist_ok=True)

    img = cv2.imread(P.product)
    alpha = rb.product_mask(img, P.model)
    if P.reflection == "thin":
        alpha = rb.remove_bottom_reflection(img, alpha)
    elif P.reflection == "mirror":
        alpha, _, _ = rb.remove_mirror_reflection(img, alpha)
    elif P.reflection == "rim":
        alpha = rb.remove_rim_reflection(img, alpha)
    elif P.reflection == "crease":
        alpha = rb.remove_crease_reflection(img, alpha)

    if P.fill_holes:   # 제품 안쪽에 뚫린 구멍(화분 물구멍 등)이 배경으로 빠지지 않게 메운다
        solid = (alpha > 0.5).astype(np.uint8)
        n, lab, st, _ = cv2.connectedComponentsWithStats(1 - solid)
        hh, ww = solid.shape
        for i in range(n):
            x, y, bw, bh, _ = st[i]
            if x > 0 and y > 0 and x + bw < ww and y + bh < hh:
                alpha[cv2.dilate((lab == i).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0] = 1.0

    # 제품만 잘라 캔버스 크기에 맞게 축소
    S = P.out_size
    ys, xs = np.nonzero(alpha > 0.5)
    pad = 6
    y0, y1 = max(ys.min() - pad, 0), min(ys.max() + pad + 1, img.shape[0])
    x0, x1 = max(xs.min() - pad, 0), min(xs.max() + pad + 1, img.shape[1])
    ph, pw = ys.max() - ys.min() + 1, xs.max() - xs.min() + 1
    f = min(P.product_height * S / ph, P.product_width * S / pw)
    crop = cv2.resize(img[y0:y1, x0:x1], None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    ca = cv2.resize(alpha[y0:y1, x0:x1], None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    ch, cw = ca.shape
    light_x = {"left": -0.6, "right": 0.6, "front": 0.0}.get(P.light)
    if light_x is None:
        light_x = studio.light_direction(img, alpha)
    side = 1 if light_x >= 0 else -1
    bot = int(S * P.contact)
    cx = int(S * (0.5 + P.offset_x * side))
    ty = bot - (ch - int(round(pad * f)))                    # 제품 바닥이 bot 에 오게
    tx = cx - cw // 2
    img_t = np.zeros((S, S, 3), np.uint8)
    a_t = np.zeros((S, S), np.float32)
    img_t[ty:ty + ch, tx:tx + cw] = crop
    a_t[ty:ty + ch, tx:tx + cw] = ca

    if P.dehighlight > 0:
        img_t = suppress_highlights(img_t, a_t, P.dehighlight)

    wall = studio.parse_hex(P.wall)
    _, _, fa, fb, b0 = studio.footprint(a_t)
    # 카메라 높이 추정: 굽 바닥 타원의 납작함 b/a ≈ sin(카메라가 내려다보는 각도)
    cam = float(np.clip(fb / max(fa, 1), 0.10, 0.60))
    if P.camera_match:
        cos = float(np.sqrt(1 - cam * cam))
        # 바닥이 화면에서 차지하는 깊이는 sin 에 비례 → 많이 내려다볼수록 벽·바닥 전환이 위로
        P.horizon = 0.05 + 0.55 * cam
        # 그림자: 빛 고도 light_elevation, 바닥에서 옆(1)·뒤(light_back) 방향으로 눕는다.
        # 화면에서 높이 t 인 점의 실제 높이는 t/cos, 바닥의 앞뒤 거리는 sin 만큼 줄어 보인다
        L = 1.0 / np.tan(np.deg2rad(P.light_elevation)) / cos
        n = float(np.hypot(1.0, P.light_back))
        P.cast_length = L / n
        P.cast_rise = L * P.light_back / n * cam
        P.cast_min_depth = cam
    seam = b0 - P.horizon * S
    bg = studio.backdrop_studio(S, S, wall, seam, b0, light_x, soft=P.horizon_soft)
    floor_bgr = np.median(bg[min(b0 + 20, S - 1):, :].reshape(-1, 3), 0)
    if P.bg_texture > 0:
        bg = bg * surface_texture(S, P.bg_texture)[..., None]

    # 경계가 넓게 섞이므로 그림자는 섞임이 시작되는 높이 아래 전체에 허용
    s = cs.shadow_map(a_t, light_x, P, seam - P.horizon_soft * S / 2)[..., None]
    tint = cs.shadow_tint_for(img_t, a_t, [float(v) for v in P.shadow_tint.split(",")],
                              P.shadow_color)
    bg = bg * (1 - s * (1 - tint))
    if P.reflect > 0:
        refl, ra = studio.reflection(img_t, a_t, P.reflect, length=0.18)
        bg = bg * (1 - ra[..., None]) + cv2.GaussianBlur(refl, (0, 0), 3) * ra[..., None]

    P.floor = studio.hex_of(floor_bgr)
    prod, comp_a = cs.harmonize_product(img_t, a_t, bg, floor_bgr, P)
    prod = directional_shade(prod, a_t, light_x, P.shade)
    A = comp_a[..., None]
    out = prod * A + bg * (1 - A)
    # 사진 한 장처럼: 전체에 아주 약한 필름 입자
    out += np.random.default_rng(2).normal(0, 1.2, (S, S, 1)).astype(np.float32)
    if P.grade > 0:   # 제품과 배경에 같은 보정: 아주 약한 대비 + 따뜻한 색조
        x = np.clip(out / 255.0, 0, 1)
        x = x + P.grade * 0.05 * (x - 0.5) * (1 - np.abs(2 * x - 1))        # 중간톤 대비
        out = x * 255 * (1 + P.grade * np.array([-0.012, 0.0, 0.010], np.float32))
    out = np.clip(np.rint(out), 0, 255).astype(np.uint8)

    res = os.path.join(P.out_dir, f"{name}_studio.jpg")
    cv2.imwrite(res, out, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if P.relight_inputs:   # relight.py 용: 제품 마스크와 제품 없는 배경(그림자 포함)
        cv2.imwrite(os.path.join(P.relight_inputs, f"{name}_mask.png"),
                    (np.clip(comp_a, 0, 1) * 255).astype(np.uint8))
        cv2.imwrite(os.path.join(P.relight_inputs, f"{name}_bg.png"),
                    np.clip(np.rint(bg), 0, 255).astype(np.uint8))
        cv2.imwrite(os.path.join(P.relight_inputs, f"{name}_comp.png"), out)
    oh = 1000
    o_small = cv2.resize(img, (int(img.shape[1] * oh / img.shape[0]), oh), interpolation=cv2.INTER_AREA)
    gap = np.full((oh, 16, 3), 255, np.uint8)
    cv2.imwrite(os.path.join(P.out_dir, f"{name}_compare.jpg"),
                np.hstack([o_small, gap, cv2.resize(out, (oh, oh), interpolation=cv2.INTER_AREA)]),
                [cv2.IMWRITE_JPEG_QUALITY, 92])
    print(f"{name}: {S}x{S}, 제품 배율 {f:.2f}, 카메라 {np.degrees(np.arcsin(cam)):.0f}도, 저장 {res}")


if __name__ == "__main__":
    main()
