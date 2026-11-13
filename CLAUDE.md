# hey,hye 도자기 제품 사진 보정 — 작업 안내

이 파일은 이전 세션(클라우드, CPU 전용)에서 확정된 방향과 이미 해결한 오류를 정리한 것이다.
새로 설계하지 말고 아래 스크립트를 그대로 쓰고, 고칠 때는 "이미 겪은 오류"를 먼저 확인한다.
사용자에게는 한국어로 답한다.

## 목표 (사용자가 확정한 방향)

- 매장 철판 선반에서 찍은 도자기 사진을 **스튜디오에서 촬영한 것처럼** 재구성한다.
- 참고 분위기: 웜그레이 배경이 **경계선 없이** 벽에서 바닥으로 이어지고, 제품은 넓은 여백 속에
  작게 놓이며, **빛은 오른쪽**에서 오고 **그림자는 왼쪽 뒤로 사선**으로 길게 눕는다.
- 가장 큰 불만은 "제품이 배경 위에 떠 있는 3D처럼 보인다"였다. 접지감이 최우선이다.
- 제품 **형태·무늬·로고는 바꾸지 않는다.** 색·음영을 살짝 조정하는 것은 허용됐다
  (픽셀 그대로가 필요하면 `catalog_shot.py --keep-product`).
- 바닥에 제품이 비치는 반사는 기본으로 넣지 않는다.
- 접시처럼 바로 위에서 찍은 사진은 벽 없는 아이보리 바닥면(`--backdrop flat`)을 쓴다.

## 스크립트 (tools/)

| 파일 | 용도 |
|---|---|
| `studio_shot.py` | **현재 기본.** 정사각 캔버스에 제품을 작게 배치한 스튜디오 구도(샘플 분위기) |
| `catalog_shot.py` | 원본 해상도 유지, 아이보리 벽·바닥 + 접지 그림자. `--keep-product` 로 픽셀 보존 |
| `replace_background.py` | 초기 파이프라인. 제품 분리·바닥 반사 제거 함수와 접시용 `--backdrop flat` |
| `studio.py` | 배경·그림자·색 추천 공용 함수 |
| `relight.py` | IC-Light AI 재조명(실험). GPU가 있으면 여기부터 개선 여지가 큼 |

결과물: `samples/studio/`(스튜디오), `samples/catalog/`(원본 해상도), `samples/*_result.jpg`(초기).

### 사진별 분리·반사 옵션 (이 조합으로 검증됨 — 바꾸지 말 것)

```
python tools/studio_shot.py samples/bowl_side_original.jpg  --model isnet-general-use --reflection mirror
python tools/studio_shot.py samples/cup_original.jpg        --model ensemble --reflection rim
python tools/studio_shot.py samples/vase_side_original.jpg  --model birefnet-general --reflection crease
python tools/studio_shot.py samples/wave_side_original.jpg  --model birefnet-general --reflection crease
python tools/studio_shot.py samples/purple_cup_original.jpg --model birefnet-general --reflection crease
# 나머지(planter, sand_cup, pot_top, vase_top, vase_bottom, wave_top, lilac_side, lilac_top):
python tools/studio_shot.py samples/<이름>_original.jpg     --model birefnet-general
# 바닥에 물구멍이 있는 사진(pot_bottom, lilac_bottom): 구멍이 배경으로 빠지지 않게
python tools/studio_shot.py samples/<이름>_original.jpg     --model birefnet-general --fill-holes
# 접시(plate_*, rect_*, blue_plate, white_plate, orange_*):
python tools/replace_background.py <접시>.jpg <결과>.jpg --canvas 2273x1705 --model birefnet-general --reflection none --backdrop flat
```

반사 제거 방식: `mirror`(흰 굽 아래 거울 반사), `rim`(검은 유광 몸체 아래 밝은 띠),
`crease`(얇은 흰 굽 선 → 어두운 접지선 → 반사), `thin`(위에서 본 그릇의 얇은 띠), `none`.
굽 아래 밝은 띠는 실제 굽이 아니라 **철판에 비친 반사**다(사용자 확인). 제품으로 남기지 말 것.

## 이미 겪은 오류와 해결 (반복 금지)

**제품 분리**
- isnet 은 소품(가격표)과 제품을 잘 구분하지만 경계가 거칠고, birefnet 은 경계가 정확하지만
  붙어 있는 투명 아크릴 가격표까지 잡는다 → 그런 사진은 `--model ensemble`.
- 얇은 조각 제거·구멍 메우기는 식물 잎을 잘라 먹고 잎 사이 배경을 메운다 → birefnet 단독일 때는 끔
  (`_clean(cut_thin=False)`). 다시 켜지 말 것.

**그림자·배경**
- 타원을 붙인 그림자는 "그림자 같지 않다"는 평가를 받았다. 지금은 제품을 높이별로 잘라
  단면을 빛 반대쪽 바닥으로 미는 방식(`catalog_shot.shadow_map`)이다. 타원으로 되돌리지 말 것.
- 투영 행렬 부호를 틀리면 그림자가 빛 쪽으로 간다(이미 수정). 빛 오른쪽 → 그림자 왼쪽인지 눈으로 확인.
- 사선 그림자를 굽 근처(`floor_zone`)로만 제한하면 잘려서 안 보인다 → 접촉·AO만 제한, 사선 그림자는 경계 아래 전체.
- 그림자 색은 회색이 아니라 바닥색을 따뜻하게 어둡게 한 색(곱하기 합성).
- 벽·바닥을 좁은 폭(4%)으로 섞고 어두운 띠를 넣으면 "뭉개진 경계"로 보인다 →
  `backdrop_studio(soft=0.26)` 처럼 넓게 섞고 띠를 없앤다.
- 바닥 반사는 테이블 면 기준으로 뒤집어야 한다(옆면 윤곽 기준이면 몸통 옆에 줄무늬가 생김).

- 그림자가 넓게 번지면 "제품에서 투영된 그림자 같지 않다"는 평가를 받는다 → 굽 근처는 윤곽을
  또렷하게(블러 작게), 멀어질수록만 흐리게. AO 는 약하게(`ao_strength=0.2`). 낮은 카메라 각도에서
  그림자가 실처럼 얇아지지 않게 `--cast-min-depth`(기본 0.16)로 두께 하한을 둔다.
- 그림자 색에는 제품 아랫부분 색조를 은은하게 섞는다(`--shadow-color`, 기본 0.3).
- `--reflection crease` 는 굽이 몸통보다 좁은 컵에서 아래 모서리를 계단처럼 잘라낼 수 있다
  (lilac_side 에서 발생). birefnet 마스크가 이미 굽 끝에서 끝나면 `none` 을 쓴다.
  새 사진은 먼저 마스크 윤곽을 원본 위에 겹쳐 보고 방식을 고른다.

**제품 색**
- 푸른 끼 제거를 제품 전체에 적용하면 고유색이 바랜다(모래색 컵이 회색이 됨) →
  흰색에 가까운 저채도 영역에만 적용(`harmonize_product`). 범위를 넓히지 말 것.

**AI 재조명 (relight.py)**
- 기반 모델 일부 가중치가 fp16 → CPU 에서는 `.float()` 로 fp32 통일(안 하면 Half/float 오류).
  Mac GPU(MPS)에서도 fp32 로 시작하고, 장치는 `mps` 로 옮겨야 한다(현재 코드는 CPU 고정).
- AI 결과(512px)를 그대로 쓰면 무늬가 뭉개진다 → 저주파 조명 비율만 원본에 곱한다.
- 그 비율을 제품 안/밖 따로 계산하지 않으면 윤곽에 후광이 생기고, 범위 밖 0 나눗셈은 검은 구멍이 된다(이미 처리).
- 지금까지 효과는 은은했다. GPU 환경에서는 처리 해상도(`--size`)를 올리는 것부터 시도.

**환경**
- numpy 2: `arr.ptp()` 없음 → `np.ptp(arr)`.
- `cv2.imwrite` 에 PNG 로 저장하면서 JPEG 품질 옵션을 주면 경고만 뜨고 무시된다.
- `__pycache__` 는 커밋하지 않는다(.gitignore 에 있음).
- 모델은 첫 실행 때 내려받는다(rembg, Hugging Face). 방화벽이 있으면 huggingface.co 와 그 CDN 호스트 허용 필요.

## 작업 방식

- 결과는 반드시 **이미지를 직접 열어 확대해서** 확인한 뒤 보고한다. 굽 아래 접지부와 제품 윤곽을 본다.
- 한 장으로 먼저 시험하고, 괜찮으면 전체를 다시 만든다. 원본 파일(`*_original.jpg`)은 덮어쓰지 않는다.
- 값은 옵션으로 조정한다: `--cast-length/--cast-rise/--cast-strength/--cast-blur/--cast-fade`,
  `--contact-strength`, `--ao-strength`, `--shade`, `--horizon-soft`, `--product-height`, `--wall`, `--light`.
- 남은 한계: 원본의 강한 하이라이트·철판 반사는 제품 표면에 찍혀 있어 완전히 지워지지 않는다.
  가장 확실한 개선은 흰 무광 종이 위에서 확산광으로 다시 촬영하는 것이다.
