"""도자기 제품 사진의 배경 교체 스크립트.

제품 픽셀은 일절 수정하지 않는다. 마스크 안쪽 픽셀은 원본 값을 그대로
복사하고, 배경만 참고 이미지의 색감으로 새로 만든 뒤 바닥 그림자를 더한다.

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


def add_shadow(bg, alpha, strength=0.38):
    """제품 바닥 접지 그림자 (부드러운 타원 + 진한 접촉선)."""
    h, w = alpha.shape
    ys, xs = np.nonzero(alpha > 0.5)
    bottom, left, right = ys.max(), xs.min(), xs.max()
    cx, rw = (left + right) / 2, (right - left) / 2
    sh = np.zeros((h, w), np.float32)
    cv2.ellipse(sh, (int(cx), int(bottom - rw * 0.06)),
                (int(rw * 0.80), int(rw * 0.07)), 0, 0, 360, 1.0, -1)
    soft = cv2.GaussianBlur(sh, (0, 0), rw * 0.07) * 0.8
    cv2.ellipse(sh, (int(cx), int(bottom - rw * 0.02)),
                (int(rw * 0.70), int(rw * 0.025)), 0, 0, 360, 1.0, -1)
    contact = cv2.GaussianBlur(sh, (0, 0), rw * 0.025)
    s = np.clip(soft + contact * 0.9, 0, 1) * strength
    tint = np.array([0.80, 0.86, 0.92], np.float32)  # 따뜻한 갈색 톤 그림자 (BGR)
    return bg * (1 - s[..., None] * (1 - tint * 0.6))


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

    alpha = product_mask(img)

    # 제품을 가로 중앙으로 평행이동만 한다(크기 변경/회전 없음 → 픽셀 재샘플링 없음)
    ys, xs = np.nonzero(alpha > 0.5)
    dx = int(round(w / 2 - (xs.min() + xs.max()) / 2))
    dy = int(round(h * 0.86 - ys.max()))  # 바닥선을 캔버스 86% 높이에
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    img_t = cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_NEAREST)
    alpha = cv2.warpAffine(alpha, M, (w, h), flags=cv2.INTER_NEAREST)

    bg = add_shadow(make_background(ref, w, h), alpha)
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
