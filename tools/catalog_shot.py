"""도자기 제품 카탈로그 사진 만들기 — 제품은 그대로, 배경·바닥·접지 그림자만.

목표: 제품이 바닥에 안정적으로 놓인 자연스러운 스튜디오 사진

  1) rembg 로 제품 마스크 (+ 바닥 반사 제거: replace_background.py 와 같은 옵션)
  2) 제품을 평행이동만 해서 배치 — 크기·회전 변경 없음, 출력 해상도 = 원본 해상도
  3) 아이보리 스튜디오 배경: 벽은 위가 밝고 바닥과 만나는 곳으로 갈수록 차분해지며,
     바닥은 벽보다 밝다. 경계선은 또렷하되 부드럽게(참고 사진처럼).
  4) 접지 그림자 = 세 겹을 곱하기(멀티플라이) 방식으로 바닥에만 합성
     - 접촉 그림자: 굽 바닥 타원(제품 밑면 윤곽에서 추정)을 따라 맞닿는 부분이 가장
       진하고, 타원 밖으로 갈수록 타원형 그라디언트로 빠르게 옅어짐
     - 앰비언트 오클루전: 제품 실루엣 하단 윤곽에서의 거리로 계산한 넓고 옅은 그늘
     - 드리운 그림자: 빛 반대쪽으로 길게 눕는 타원형 그라디언트, 멀어질수록
       더 흐리고 옅어짐(조명 방향은 제품 하이라이트로 자동 추정 또는 지정)
     그림자 색은 회색이 아니라 바닥색을 따뜻하게 어둡게 한 색
  5) 제품 어울림(기본): 색 끼임 제거, 제품 하단 AO, 바닥 반사광, 라이트 랩, 윤곽 완화.
     형태·크기는 그대로(평행이동만). --keep-product 를 주면 제품 픽셀을 원본 그대로
     복사하고 저장 후 다시 읽어 변경 0개인지 검증한다

사용법:
    python tools/catalog_shot.py samples/wave_side_original.jpg --out-dir samples/catalog \\
        --model birefnet-general --reflection crease

결과: <이름>_before.jpg(원본 복사), <이름>_after.png(무손실), <이름>_compare.jpg(전후 비교)
그림자·배경 파라미터는 모두 옵션으로 조정 가능 (python tools/catalog_shot.py -h).
"""
import argparse
import os
import shutil
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import replace_background as rb  # noqa: E402
import studio  # noqa: E402


# ------------------------------------------------------------------ 파라미터

def build_parser():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("product")
    p.add_argument("--out-dir", default="samples/catalog")
    p.add_argument("--name", help="출력 파일 이름 앞부분 (기본: 입력 파일 이름)")
    g = p.add_argument_group("제품 분리")
    g.add_argument("--model", default="birefnet-general")
    g.add_argument("--reflection", default="none",
                   choices=["none", "thin", "mirror", "rim", "crease"])
    g = p.add_argument_group("배치")
    g.add_argument("--bottom", type=float, default=0.78,
                   help="제품이 바닥에 닿는 선의 높이(화면 비율)")
    g = p.add_argument_group("배경")
    g.add_argument("--wall-top", default="#EEE5D9", help="벽 위쪽 색")
    g.add_argument("--wall-low", default="#DDD2C3", help="벽이 바닥과 만나는 곳의 색")
    g.add_argument("--floor", default="#F4EEE5", help="바닥 색(벽보다 밝게)")
    g.add_argument("--seam", type=float, default=0.42,
                   help="벽·바닥 경계 높이: 바닥 닿는 선에서 제품 몸통 높이의 몇 배 위")
    g.add_argument("--seam-soft", type=float, default=0.004,
                   help="경계선 부드러움(화면 높이 비율)")
    g.add_argument("--vignette", type=float, default=0.05, help="가장자리 어두워짐 정도")
    g = p.add_argument_group("그림자")
    g.add_argument("--light", default="auto", choices=["auto", "left", "right", "front"],
                   help="빛이 오는 방향 (auto: 제품 하이라이트로 추정)")
    g.add_argument("--contact-strength", type=float, default=0.7)
    g.add_argument("--contact-size", type=float, default=0.09,
                   help="접촉 그림자가 밑면에서 퍼지는 폭(굽 반지름 대비)")
    g.add_argument("--ao-strength", type=float, default=0.32)
    g.add_argument("--ao-size", type=float, default=0.5,
                   help="앰비언트 오클루전 범위(굽 반지름 대비)")
    g.add_argument("--cast-strength", type=float, default=0.3)
    g.add_argument("--cast-length", type=float, default=0.9,
                   help="드리운 그림자 길이(굽 반지름 대비)")
    g.add_argument("--cast-blur", type=float, default=0.18,
                   help="드리운 그림자 흐림(굽 반지름 대비)")
    g.add_argument("--opacity", type=float, default=1.0, help="그림자 전체 투명도 배율")
    g.add_argument("--shadow-tint", default="0.56,0.61,0.68",
                   help="그림자 색 = 바닥색 × 이 값 (B,G,R; 파랑을 더 줄이면 따뜻한 그늘)")
    g = p.add_argument_group("제품 어울림 (제품 픽셀을 조금 바꿈. --keep-product 로 끔)")
    g.add_argument("--keep-product", action="store_true",
                   help="제품 픽셀을 원본 그대로 유지(아래 보정 전부 끔, 픽셀 검증 수행)")
    g.add_argument("--product-ao", type=float, default=0.22,
                   help="제품 아랫부분이 바닥 가까이에서 어두워지는 정도")
    g.add_argument("--product-ao-height", type=float, default=0.12,
                   help="제품 아래쪽 어두워지는 범위(제품 높이 대비)")
    g.add_argument("--bounce", type=float, default=0.10,
                   help="바닥에서 반사된 따뜻한 빛이 제품 아래쪽에 비치는 정도")
    g.add_argument("--neutralize", type=float, default=0.6,
                   help="원본 촬영 환경(철판·창가)의 푸른/녹색 끼를 덜어내는 정도 0~1")
    g.add_argument("--wrap", type=float, default=0.35,
                   help="배경 빛이 제품 가장자리로 번지는 라이트 랩 세기")
    g.add_argument("--edge-soft", type=float, default=0.8,
                   help="제품 윤곽 부드럽게(픽셀). 오려 붙인 듯한 딱딱한 경계 완화")
    g = p.add_argument_group("반사")
    g.add_argument("--reflect", type=float, default=0.05,
                   help="바닥 반사 세기 (기본 0.05 = 아주 약하게, 0=없음)")
    return p


# ------------------------------------------------------------------ 배경

def lab_of(hexstr):
    return studio.bgr_to_lab(studio.parse_hex(hexstr))[0]


def backdrop(w, h, seam, contact_y, light_x, P):
    """벽(위 밝음 → 경계 근처 차분) + 바닥(벽보다 밝음, 앞쪽으로 아주 살짝 어두움)."""
    y = np.arange(h, dtype=np.float32)[:, None]
    x = np.arange(w, dtype=np.float32)[None, :]
    top, low, flo = lab_of(P.wall_top), lab_of(P.wall_low), lab_of(P.floor)
    tw = np.clip(y / max(seam, 1), 0, 1) ** 1.6               # 벽: 0 위 → 1 경계
    wall = top[None, None, :] * (1 - tw[..., None]) + low[None, None, :] * tw[..., None]
    depth = np.clip((y - seam) / max(h - seam, 1), 0, 1)
    floor = np.broadcast_to(flo, (h, 1, 3)).astype(np.float32).copy()
    floor[..., 0] -= (2.5 * depth ** 1.5)                    # 맨 앞은 아주 조금 어둡게
    floor[..., 0] -= 1.5 * np.exp(-((y - seam) / (h * 0.03)) ** 2)  # 경계 바로 아래 살짝 그늘
    soft = max(P.seam_soft * h, 1.0)
    t = np.clip((y - seam) / (2 * soft) + 0.5, 0, 1)[..., None]
    t = t * t * (3 - 2 * t)
    lab = wall * (1 - t) + floor * t
    lab = np.broadcast_to(lab, (h, w, 3)).copy()
    # 조명: 빛 쪽이 조금 밝고, 가장자리로 갈수록 은은하게 어두움 (L 채널만)
    side = (x / w - 0.5) * 2
    d = np.sqrt(((x - w / 2) / (0.75 * w)) ** 2 + ((y - contact_y) / (0.9 * h)) ** 2)
    lab[..., 0] += 2.5 * light_x * side - 100 * P.vignette * np.clip(d - 0.55, 0, 1) ** 1.5
    bgr = cv2.cvtColor(lab.astype(np.float32), cv2.COLOR_Lab2BGR) * 255
    # 8bit 밴딩 방지용 아주 약한 디더 (눈에 보이지 않는 수준)
    bgr += np.random.default_rng(0).uniform(-0.6, 0.6, (h, w, 1)).astype(np.float32)
    return bgr


# ------------------------------------------------------------------ 그림자

def shadow_map(alpha, light_x, P):
    """0~1 그림자 농도(곱하기 합성용). 바닥 영역에만 생긴다."""
    h, w = alpha.shape
    cx, cy, a, b, bot = studio.footprint(alpha)
    sil = (alpha > 0.5).astype(np.uint8)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    # 바닥 영역: 굽 타원의 윗 가장자리보다 아래 (벽에는 그림자가 생기지 않게)
    floor_zone = np.clip((yy - (cy - b)) / (b * 0.6 + 1), 0, 1)

    # 1) 접촉 그림자 — 제품 밑면 윤곽에서의 거리(픽셀)로 진하기를 정하고, 굽 바닥
    #    타원형 그라디언트로 가로 범위를 굽 폭에 맞춘다 (맞닿는 선이 가장 진함)
    dist = cv2.distanceTransform((1 - sil).astype(np.uint8), cv2.DIST_L2, 5)
    r = np.sqrt(((xx - cx) / a) ** 2 + ((yy - cy) / b) ** 2)      # 1 = 굽 테두리
    ell = np.exp(-np.clip(r - 1, 0, None) / 0.25)
    contact = np.exp(-dist / max(a * P.contact_size, 1)) * ell
    contact = cv2.GaussianBlur(contact.astype(np.float32), (0, 0), max(a * 0.004, 0.8))

    # 2) 앰비언트 오클루전 — 실루엣 하단 윤곽에서의 거리
    ao = np.exp(-dist / max(a * P.ao_size, 1))
    ao *= np.clip((yy - (cy - 2 * b)) / (b + 1), 0, 1)           # 몸통 옆 높은 곳은 0
    ao = cv2.GaussianBlur(ao.astype(np.float32), (0, 0), max(a * 0.03, 1))

    # 3) 드리운 그림자 — 빛 반대쪽으로 길게, 멀어질수록 흐리고 옅게
    dirx = -np.sign(light_x) if abs(light_x) > 0.08 else 0.0
    L = a * P.cast_length
    ccx = cx + dirx * L * 0.55
    ra, rb_ = a + L * (0.55 if dirx else 0.25), b * (1.25 + 0.25 * P.cast_length)
    re = np.sqrt(((xx - ccx) / ra) ** 2 + ((yy - (cy + b * 0.1)) / rb_) ** 2)
    cast = np.clip(1 - re, 0, 1) ** 1.3
    along = np.clip(((xx - cx) * (dirx if dirx else 0) + np.abs(yy - cy) * 0.2) / (L + 1), 0, 1)
    cast *= (1 - 0.65 * along)
    cast = cv2.GaussianBlur(cast.astype(np.float32), (0, 0), max(a * P.cast_blur, 1))

    s = (P.contact_strength * contact + P.ao_strength * ao + P.cast_strength * cast)
    s = np.clip(s * floor_zone * P.opacity, 0, 0.85)
    return s


# ------------------------------------------------------------------ 제품 어울림

def harmonize_product(img_t, alpha, bg, floor_bgr, P):
    """제품이 이 바닥 위, 이 조명 아래에 실제로 놓여 있는 것처럼 제품 쪽을 조정.

    1) 색 끼임 제거: 무채색(흰·회색) 부분의 평균 색을 배경의 중성색 쪽으로 옮김
    2) 제품 하단 AO: 바닥에 가까울수록 어둡게 (실제 물체는 바닥 근처가 어둡다)
    3) 바닥 반사광: 바닥의 따뜻한 색이 제품 아래쪽에 은은하게 비침
    4) 라이트 랩: 배경의 밝은 빛이 제품 가장자리로 살짝 번짐
    5) 윤곽을 아주 살짝 부드럽게 (alpha 가장자리만)
    """
    h, w = alpha.shape
    prod = img_t.astype(np.float32)
    m = alpha > 0.02
    ys, xs = np.nonzero(alpha > 0.5)
    top, bot = ys.min(), ys.max()
    H = bot - top
    yy = np.arange(h, dtype=np.float32)[:, None]

    # 1) 색 끼임 제거 (Lab a·b 평균 이동, 무채색 픽셀 기준)
    lab = cv2.cvtColor(np.clip(prod, 0, 255).astype(np.uint8), cv2.COLOR_BGR2Lab).astype(np.float32)
    C = np.hypot(lab[..., 1] - 128, lab[..., 2] - 128)
    neutral = m & (C < 14) & (lab[..., 0] > 120)
    if neutral.sum() > 500 and P.neutralize > 0:
        tgt = cv2.cvtColor(np.uint8([[floor_bgr]]), cv2.COLOR_BGR2Lab)[0, 0].astype(np.float32)
        shift = (np.array([128 + (tgt[1] - 128) * 0.5, 128 + (tgt[2] - 128) * 0.5])
                 - np.array([np.median(lab[..., 1][neutral]), np.median(lab[..., 2][neutral])]))
        lab[..., 1] += shift[0] * P.neutralize
        lab[..., 2] += shift[1] * P.neutralize
        prod = cv2.cvtColor(np.clip(lab, 0, 255).astype(np.uint8), cv2.COLOR_Lab2BGR).astype(np.float32)

    # 2) 제품 하단 AO + 3) 바닥 반사광 (아래 가장자리에서의 거리 기준)
    low = np.full(w, -1, np.float32)
    cols = np.nonzero((alpha > 0.5).any(0))[0]
    low[cols] = h - 1 - np.argmax((alpha > 0.5)[::-1, cols], axis=0)
    up = np.where(low[None, :] >= 0, low[None, :] - yy, 1e9)          # 바닥선 위로 몇 px
    band = max(P.product_ao_height * H, 4)
    near = np.clip(1 - up / band, 0, 1) ** 1.8 * m
    prod = prod * (1 - P.product_ao * near[..., None])
    glow = np.clip(1 - up / (band * 1.8), 0, 1) ** 1.2 * m
    prod = prod + (np.asarray(floor_bgr, np.float32) - prod) * (P.bounce * glow)[..., None] * 0.5

    # 4) 라이트 랩
    a = alpha.astype(np.float32)
    r = max(H * 0.006, 1.5)
    inner = np.clip(cv2.GaussianBlur(1 - a, (0, 0), r * 2) * a * 2.0, 0, 1)
    bgb = cv2.GaussianBlur(bg, (0, 0), r * 3)
    prod = prod * (1 - (inner * P.wrap)[..., None]) + bgb * (inner * P.wrap)[..., None]

    # 5) 윤곽 부드럽게
    if P.edge_soft > 0:
        a = np.minimum(a, cv2.GaussianBlur(a, (0, 0), P.edge_soft) + (a >= 1) * 0)
        a = cv2.GaussianBlur(a, (0, 0), P.edge_soft)
    return np.clip(prod, 0, 255), a


# ------------------------------------------------------------------ 메인

def main():
    P = build_parser().parse_args()
    name = P.name or os.path.splitext(os.path.basename(P.product))[0].replace("_original", "")
    os.makedirs(P.out_dir, exist_ok=True)

    img = cv2.imread(P.product)
    h, w = img.shape[:2]
    alpha = rb.product_mask(img, P.model)
    if P.reflection == "thin":
        alpha = rb.remove_bottom_reflection(img, alpha)
    elif P.reflection == "mirror":
        alpha, _, _ = rb.remove_mirror_reflection(img, alpha)
    elif P.reflection == "rim":
        alpha = rb.remove_rim_reflection(img, alpha)
    elif P.reflection == "crease":
        alpha = rb.remove_crease_reflection(img, alpha)

    # 평행이동만 (원본 해상도 유지). 가로 중앙, 바닥 닿는 선을 P.bottom 높이에
    ys, xs = np.nonzero(alpha > 0.5)
    dx = int(round(w / 2 - (xs.min() + xs.max()) / 2))
    dy = int(round(h * P.bottom - ys.max()))
    dy = max(dy, int(h * 0.04) - ys.min())          # 위가 잘리지 않게
    dy = min(dy, h - 1 - ys.max())                   # 아래가 잘리지 않게
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    img_t = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_NEAREST,
                           borderMode=cv2.BORDER_REPLICATE)
    alpha = cv2.warpAffine(alpha, M, (w, h), flags=cv2.INTER_NEAREST)

    if P.light == "auto":
        light_x = studio.light_direction(img_t, alpha)
    else:
        light_x = {"left": -0.6, "right": 0.6, "front": 0.0}[P.light]
    _, cy, _, _, bot = studio.footprint(alpha)
    H = bot - studio.body_top(alpha)
    seam = float(np.clip(bot - P.seam * H, h * 0.15, bot - 0.08 * H))

    bg = backdrop(w, h, seam, bot, light_x, P)
    s = shadow_map(alpha, light_x, P)[..., None]
    tint = np.array([float(v) for v in P.shadow_tint.split(",")], np.float32)
    bg = bg * (1 - s * (1 - tint))                                   # 곱하기 합성
    if P.reflect > 0:
        # 반광택 바닥에 아주 약하게, 짧게, 흐릿하게 비치도록
        refl, ra = studio.reflection(img_t, alpha, P.reflect, length=0.18)
        refl = cv2.GaussianBlur(refl, (0, 0), 4)
        bg = bg * (1 - ra[..., None]) + refl * ra[..., None]

    inside = alpha >= 1.0
    if P.keep_product:
        prod, comp_a = img_t.astype(np.float32), alpha
    else:
        prod, comp_a = harmonize_product(img_t, alpha, bg, studio.parse_hex(P.floor), P)
    A = comp_a[..., None]
    out = np.clip(np.rint(prod * A + bg * (1 - A)), 0, 255).astype(np.uint8)
    if P.keep_product:
        out[inside] = img_t[inside]                                  # 제품 원본 픽셀 그대로

    before = os.path.join(P.out_dir, f"{name}_before.jpg")
    after = os.path.join(P.out_dir, f"{name}_after.png")
    compare = os.path.join(P.out_dir, f"{name}_compare.jpg")
    shutil.copyfile(P.product, before)                               # 원본은 덮어쓰지 않음
    cv2.imwrite(after, out)
    gap = np.full((h, max(w // 60, 8), 3), 255, np.uint8)
    cv2.imwrite(compare, cv2.resize(np.hstack([img, gap, out]), None, fx=0.5, fy=0.5,
                                    interpolation=cv2.INTER_AREA), [cv2.IMWRITE_JPEG_QUALITY, 92])

    # 검증: 저장한 파일을 다시 읽어 제품 영역이 원본과 같은지
    chk = cv2.imread(after)
    Y, X = np.nonzero(inside)
    diff = np.abs(chk[Y, X].astype(int) - img[Y - dy, X - dx].astype(int)).max(1)
    assert chk.shape == img.shape, "해상도가 원본과 다름"
    light = '오른쪽' if light_x > 0.08 else '왼쪽' if light_x < -0.08 else '정면'
    if P.keep_product:
        print(f"{name}: {w}x{h} (원본과 동일), 빛 {light}, 제품 픽셀 {len(Y):,}개 중 변경 {int((diff > 0).sum())}개")
        if (diff > 0).any():
            raise SystemExit("제품 픽셀이 바뀌었습니다")
    else:  # 제품 어울림 보정: 얼마나 바뀌었는지 보고 (형태는 평행이동만, 크기·윤곽 동일)
        print(f"{name}: {w}x{h} (원본과 동일), 빛 {light}, 제품 픽셀 평균 변화 {diff.mean():.1f}/255, "
              f"10 이상 바뀐 픽셀 {100 * (diff >= 10).mean():.1f}%")


if __name__ == "__main__":
    main()
