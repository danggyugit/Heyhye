"""도자기 제품 사진의 배경 교체 스크립트.

제품 픽셀은 일절 수정하지 않는다. 마스크 안쪽 픽셀은 원본 값을 그대로
복사하고, 배경만 참고 이미지의 색감으로 새로 만든다. 바닥에 비친 반사 띠는
마스크에서 제외한다.

사용법:
    python tools/replace_background.py product.jpg bg_reference.jpg out.jpg
"""
import argparse

import cv2
import numpy as np


def product_mask(img_bgr):
    """rembg(AI 분할)로 제품 알파 마스크를 만든다. 0~1 float."""
    from PIL import Image
    from rembg import new_session, remove

    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
    m = remove(Image.fromarray(rgb), session=new_session("isnet-general-use"),
               only_mask=True)
    a = np.asarray(m, dtype=np.float32) / 255.0
    # 큰 덩어리 하나만 남겨 잡티 제거
    n, lab, stats, _ = cv2.connectedComponentsWithStats((a > 0.5).astype(np.uint8))
    keep = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
    region = cv2.dilate((lab == keep).astype(np.uint8), np.ones((5, 5), np.uint8))
    a *= region
    # 경계 바깥 1~2px만 부드럽게, 안쪽은 확실히 1.0 (제품 원본 유지)
    a[a > 0.92] = 1.0
    a[a < 0.04] = 0.0
    return a


def make_background(ref_bgr, w, h):
    """참고 배경을 캔버스 크기로 늘리고 블러+미세 노이즈로 매끈하게."""
    bg = cv2.resize(ref_bgr, (w, h), interpolation=cv2.INTER_CUBIC).astype(np.float32)
    bg = cv2.GaussianBlur(bg, (0, 0), sigmaX=w / 60)
    rng = np.random.default_rng(0)
    bg += rng.normal(0, 1.2, (h, w, 1)).astype(np.float32)  # 밴딩 방지용 그레인
    # 가장자리를 아주 살짝 어둡게 해서 제품에 시선이 모이게
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt(((xx - w / 2) / (w / 2)) ** 2 + ((yy - h * 0.55) / (h / 2)) ** 2)
    bg *= (1.0 - 0.07 * np.clip(d - 0.3, 0, 1))[..., None]
    return bg


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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("product")
    p.add_argument("bg_reference")
    p.add_argument("output")
    p.add_argument("--mask-out", help="알파 마스크 저장 경로(선택)")
    args = p.parse_args()

    img = cv2.imread(args.product)
    ref = cv2.imread(args.bg_reference)
    h, w = img.shape[:2]

    alpha = remove_bottom_reflection(img, product_mask(img))

    # 제품을 가로 중앙으로 평행이동만 한다(크기 변경/회전 없음 → 픽셀 재샘플링 없음)
    ys, xs = np.nonzero(alpha > 0.5)
    dx = int(round(w / 2 - (xs.min() + xs.max()) / 2))
    dy = int(round(h * 0.86 - ys.max()))  # 바닥선을 캔버스 86% 높이에
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    img_t = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_NEAREST)
    alpha = cv2.warpAffine(alpha, M, (w, h), flags=cv2.INTER_NEAREST)

    bg = make_background(ref, w, h)
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
