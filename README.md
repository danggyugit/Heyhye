# Heyhye — 네이버 블로그 글 자동 작성기

기존에 올린 네이버 블로그 글을 참고해 같은 말투·구성으로 새 글을 만들고,
백그라운드(headless) 크롬으로 네이버 블로그 글쓰기 화면에 입력해 임시저장하거나 발행합니다.

```
참고 글 수집 ──▶ 글 생성 (Claude) ──▶ 네이버 블로그 입력 ──▶ 임시저장 / 발행
 fetch              generate               post
```

## 준비

1. Node.js 20 이상, PC 에 크롬 설치
2. 의존성 설치

   ```bash
   npm install
   ```

   내장 Chromium 을 쓰려면 (`NAVER_BROWSER_CHANNEL` 을 비운 경우) 한 번만:

   ```bash
   npx playwright install chromium
   ```

3. 설정 파일

   ```bash
   cp .env.example .env
   ```

   `.env` 에 `NAVER_BLOG_ID` 와 `ANTHROPIC_API_KEY` 를 채웁니다. `.env` 는 git 에 올라가지 않습니다.

## 사용 순서

### 1. 참고 글 가져오기 (로그인 불필요)

```bash
npm run fetch
```

`.env` 의 `REFERENCE_LOG_NOS` 에 적힌 글(기본값은 기존 글 두 개)을 읽어 `references/<글번호>.json` 과 `.md` 로 저장합니다.
글 번호나 URL 을 직접 줄 수도 있습니다.

```bash
npm run fetch -- 224418055011 224410409150
```

### 2. 네이버 로그인 (최초 1회)

```bash
npm run login
```

크롬 창이 뜨면 직접 로그인합니다 (2단계 인증 포함). 세션은 `.naver-profile/` 에 저장되고 이후 실행은 백그라운드로 돕니다.
세션이 만료되면 같은 명령을 다시 실행하면 됩니다.

### 3. 글 생성

```bash
npm run generate -- "가을 단풍 보러 간 공원 산책" --notes "서울숲, 10월 말, 은행나무"
```

`references/` 의 글을 모두 읽어 말투·문단 길이·구성·태그 습관을 따라 새 글을 만들고 `drafts/<시각>.json` 과 `.md` 로 저장합니다.
`--notes` 에 적은 내용만 구체 사실로 쓰고, 모르는 정보는 지어내지 않고 `[확인 필요]` 로 표시하도록 지시되어 있습니다.
API 를 부르지 않고 프롬프트만 보려면 `--dry-run` 을 붙입니다.

### 4. 네이버 블로그에 입력

```bash
npm run post -- drafts/2026-10-29T....json             # 임시저장만 (기본)
npm run post -- drafts/2026-10-29T....json --publish   # 바로 발행
npm run post -- drafts/2026-10-29T....json --headful   # 창을 띄워서 확인
```

기본은 **임시저장**입니다. 글쓰기 화면의 "저장된 글" 에서 사진을 넣고 다듬은 뒤 직접 발행하는 흐름을 권합니다.
`--publish` 를 붙이면 태그까지 입력하고 발행 버튼을 눌러 글 URL 을 출력합니다.
진행 과정 스크린샷은 `logs/` 에 남습니다.

### 한 번에 실행

```bash
npm run all -- "글 주제" --notes "메모" [--publish] [--headful]
```

참고 글이 없으면 가져오고, 글을 생성한 뒤 네이버에 입력까지 합니다.

## 초안 형식 (`drafts/*.json`)

```json
{
  "title": "제목",
  "blocks": [
    { "type": "paragraph", "text": "문단" },
    { "type": "heading", "text": "소제목" },
    { "type": "quote", "text": "인용" },
    { "type": "divider", "text": "" },
    { "type": "image_placeholder", "text": "어떤 사진을 넣으면 좋을지" }
  ],
  "tags": ["태그1", "태그2"]
}
```

입력 단계에서는 블록을 문단 단위로 평탄화합니다. 소제목은 일반 문단으로, 인용은 따옴표로, 구분선은 가로줄 문자로 들어가고
`image_placeholder` 는 건너뜁니다 (사진은 직접 넣어야 합니다). 손으로 쓴 JSON 도 같은 형식이면 `npm run post` 로 올릴 수 있습니다.

## 주의

- 네이버 글쓰기 화면(SmartEditor ONE)의 구조가 바뀌면 `src/post-to-naver.js` 의 선택자를 손봐야 할 수 있습니다. `--headful` 로 실행해 어디서 멈추는지 보고 `logs/` 의 스크린샷을 참고하세요.
- 짧은 시간에 여러 글을 자동 발행하면 네이버에서 제한을 걸 수 있습니다. 하루 한두 편 정도로 쓰는 것을 권합니다.
- `.naver-profile/` 에는 로그인 세션이 들어 있으니 공유하거나 커밋하지 마세요 (`.gitignore` 에 포함).

## 파일 구성

| 파일 | 역할 |
| --- | --- |
| `src/config.js` | `.env` 읽기, 경로 설정 |
| `src/browser.js` | 영구 프로필 크롬 열기, 로그인 확인, 에디터 프레임 찾기, 스크린샷 |
| `src/fetch-references.js` | 기존 글 본문·태그 추출 → `references/` |
| `src/naver-login.js` | 수동 로그인으로 세션 저장 |
| `src/generate-post.js` | 참고 글 기반 프롬프트 구성, Claude 호출, `drafts/` 저장 |
| `src/post-to-naver.js` | 글쓰기 화면 입력, 임시저장/발행 |
| `src/run.js` | 위 과정을 한 번에 |
