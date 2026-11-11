"""IC-Light(fbc: 전경+배경 조건) 재조명으로 제품을 배경의 빛에 맞춘다.

replace_background.py 결과(합성 이미지)와 마스크를 받아:
  1) 제품(회색 바탕)과 배경 이미지를 512px 대로 줄여 IC-Light 에 넣고
  2) 새 조명이 입혀진 저해상도 결과를 얻은 뒤
  3) '조명 변화 비율'(재조명 결과 / 원래 합성, 둘 다 흐리게)만 고해상도 원본에 곱한다
     → 제품 형태·무늬·로고 디테일은 원본 그대로, 빛과 그림자만 배경에 맞춰짐

사용법:
    python tools/replace_background.py prod.jpg comp.jpg --mask-out mask.png ...
    python tools/relight.py comp.jpg mask.png out.jpg [--steps 20] [--prompt "..."]

모델: lllyasviel/ic-light (iclight_sd15_fbc.safetensors) +
      stablediffusionapi/realistic-vision-v51 (SD1.5). CPU 4코어 기준 한 장 약 1~2분
      (첫 실행은 모델 약 4GB 다운로드). 제품·배경 경계는 마스크로 나눠 따로 계산해
      후광이 생기지 않는다.
"""
import argparse

import cv2
import numpy as np
import torch
from diffusers import AutoencoderKL, DPMSolverMultistepScheduler, UNet2DConditionModel
from huggingface_hub import hf_hub_download
from safetensors.torch import load_file
from transformers import CLIPTextModel, CLIPTokenizer

BASE = "stablediffusionapi/realistic-vision-v51"


def load_models():
    tok = CLIPTokenizer.from_pretrained(BASE, subfolder="tokenizer")
    te = CLIPTextModel.from_pretrained(BASE, subfolder="text_encoder")
    vae = AutoencoderKL.from_pretrained(BASE, subfolder="vae")
    unet = UNet2DConditionModel.from_pretrained(BASE, subfolder="unet")
    # conv_in 을 4 → 12 채널(노이즈 + 전경 latent + 배경 latent)로 확장
    with torch.no_grad():
        new = torch.nn.Conv2d(12, unet.conv_in.out_channels, unet.conv_in.kernel_size,
                              unet.conv_in.stride, unet.conv_in.padding)
        new.weight.zero_()
        new.weight[:, :4].copy_(unet.conv_in.weight)
        new.bias = unet.conv_in.bias
        unet.conv_in = new
    off = load_file(hf_hub_download("lllyasviel/ic-light", "iclight_sd15_fbc.safetensors"))
    sd = unet.state_dict()
    unet.load_state_dict({k: sd[k] + off[k] if k in off else sd[k] for k in sd}, strict=True)
    sched = DPMSolverMultistepScheduler(num_train_timesteps=1000, beta_start=0.00085,
                                        beta_end=0.012, algorithm_type="sde-dpmsolver++",
                                        use_karras_sigmas=True, steps_offset=1)
    for m in (te, vae, unet):  # 일부 가중치가 fp16 으로 저장돼 있어 CPU 용 fp32 로 통일
        m.float().eval()
    return tok, te, vae, unet, sched


def to_tensor(img_bgr):
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 127.5 - 1
    return torch.from_numpy(rgb).permute(2, 0, 1)[None]


def encode_text(tok, te, text):
    ids = tok(text, padding="max_length", max_length=tok.model_max_length,
              truncation=True, return_tensors="pt").input_ids
    return te(ids)[0]


@torch.no_grad()
def relight_lowres(fg_bgr, bg_bgr, prompt, neg, steps, cfg, seed, models):
    tok, te, vae, unet, sched = models
    sf = vae.config.scaling_factor
    lat = vae.encode(torch.cat([to_tensor(fg_bgr), to_tensor(bg_bgr)])).latent_dist.mode() * sf
    concat = torch.cat([lat[0:1], lat[1:2]], dim=1)            # [1, 8, h, w]
    cond, uncond = encode_text(tok, te, prompt), encode_text(tok, te, neg)
    g = torch.Generator().manual_seed(seed)
    x = torch.randn((1, 4) + lat.shape[2:], generator=g)
    sched.set_timesteps(steps)
    x = x * sched.init_noise_sigma
    for t in sched.timesteps:
        xin = sched.scale_model_input(torch.cat([x, x]), t)
        xin = torch.cat([xin, torch.cat([concat, concat])], dim=1)
        eps = unet(xin, t, encoder_hidden_states=torch.cat([uncond, cond])).sample
        e_u, e_c = eps.chunk(2)
        x = sched.step(e_u + cfg * (e_c - e_u), t, x, generator=g).prev_sample
    img = vae.decode(x / sf).sample[0].permute(1, 2, 0).numpy()
    img = ((img + 1) * 127.5).clip(0, 255).astype(np.uint8)
    return cv2.cvtColor(img, cv2.COLOR_RGB2BGR)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("composite", help="replace_background.py 결과 이미지")
    p.add_argument("mask", help="replace_background.py --mask-out 으로 저장한 마스크")
    p.add_argument("output")
    p.add_argument("--background", help="빈 배경 이미지(없으면 합성에서 제품을 지워 만든다)")
    p.add_argument("--prompt", default="ceramic product on a table, professional product "
                   "photography, soft natural window light, studio, high quality")
    p.add_argument("--neg", default="lowres, bad quality, worst quality, blurry, deformed")
    p.add_argument("--steps", type=int, default=20)
    p.add_argument("--cfg", type=float, default=3.0)
    p.add_argument("--seed", type=int, default=12345)
    p.add_argument("--size", type=int, default=512, help="IC-Light 처리 긴 변 해상도(64의 배수)")
    p.add_argument("--strength", type=float, default=1.0, help="조명 변화 적용 정도 0~1")
    p.add_argument("--lowres-out", help="IC-Light 저해상도 결과 저장(확인용)")
    args = p.parse_args()

    comp = cv2.imread(args.composite)
    a = cv2.imread(args.mask, 0).astype(np.float32) / 255.0
    H, W = comp.shape[:2]
    s = args.size / max(H, W)
    w, h = int(round(W * s / 64)) * 64, int(round(H * s / 64)) * 64
    small = cv2.resize(comp, (w, h), interpolation=cv2.INTER_AREA)
    am = cv2.resize(a, (w, h), interpolation=cv2.INTER_AREA)[..., None]
    fg = (small * am + 127 * (1 - am)).astype(np.uint8)          # IC-Light 규칙: 회색 바탕
    if args.background:
        bg = cv2.resize(cv2.imread(args.background), (w, h), interpolation=cv2.INTER_AREA)
    else:  # 제품 자리를 주변 배경으로 메워 '빈 배경'을 만든다
        hole = (cv2.dilate((am[..., 0] > 0.02).astype(np.uint8), np.ones((9, 9))) * 255)
        bg = cv2.inpaint(small, hole, 15, cv2.INPAINT_TELEA)

    models = load_models()
    torch.set_num_threads(max(1, torch.get_num_threads()))
    relit = relight_lowres(fg, bg, args.prompt, args.neg, args.steps, args.cfg, args.seed, models)
    if args.lowres_out:
        cv2.imwrite(args.lowres_out, relit)

    # 조명 변화 비율만 고해상도에 적용 (디테일은 원본 유지)
    # 제품 안쪽과 배경을 따로 흐려서(마스크 정규화 블러) 경계에 후광이 생기지 않게
    k = max(w, h) / 64
    m = (am[..., 0] > 0.5).astype(np.float32)

    def lowfreq(x, mm):
        num = cv2.GaussianBlur(x.astype(np.float32) + 1, (0, 0), k) if mm is None else \
            cv2.GaussianBlur((x.astype(np.float32) + 1) * mm[..., None], (0, 0), k)
        den = 1.0 if mm is None else cv2.GaussianBlur(mm, (0, 0), k)[..., None] + 1e-4
        return num / den
    def ratio(mm):
        r = lowfreq(relit, mm) / lowfreq(small, mm)
        cover = cv2.GaussianBlur(mm, (0, 0), k)[..., None]
        return np.where(cover > 1e-3, np.nan_to_num(r, nan=1.0), 1.0)
    g_fg, g_bg = ratio(m), ratio(1 - m)
    gain = np.clip(g_fg * m[..., None] + g_bg * (1 - m[..., None]), 0.55, 1.6)
    gfg = cv2.resize(np.clip(g_fg, 0.55, 1.6), (W, H), interpolation=cv2.INTER_CUBIC)
    gbg = cv2.resize(np.clip(g_bg, 0.55, 1.6), (W, H), interpolation=cv2.INTER_CUBIC)
    A = a[..., None]
    gain = 1 + ((gfg * A + gbg * (1 - A)) - 1) * args.strength
    out = np.clip(comp.astype(np.float32) * gain, 0, 255).astype(np.uint8)
    cv2.imwrite(args.output, out, [cv2.IMWRITE_JPEG_QUALITY, 97])


if __name__ == "__main__":
    main()
