/**
 * drafts/ 의 글을 네이버 블로그 글쓰기(SmartEditor ONE)에 입력하고
 * 임시저장(기본) 또는 발행(--publish) 한다. 크롬은 백그라운드(headless)로 돈다.
 *
 *   npm run post -- drafts/xxx.json            # 임시저장만
 *   npm run post -- drafts/xxx.json --publish  # 실제 발행
 *   npm run post -- drafts/xxx.json --headful  # 창을 띄워서 진행 과정 확인
 */
import fs from "node:fs";
import { pathToFileURL } from "node:url";
import { config } from "./config.js";
import { openBrowser, isLoggedIn, findEditorFrame, snapshot } from "./browser.js";
import { parseArgs } from "./generate-post.js";

const WRITE_URLS = (blogId) => [
  `https://blog.naver.com/${blogId}/postwrite`,
  `https://blog.naver.com/PostWriteForm.naver?blogId=${blogId}`,
];

async function clickIfVisible(scope, selector, { timeout = 2500 } = {}) {
  const loc = scope.locator(selector).first();
  try {
    await loc.waitFor({ state: "visible", timeout });
    await loc.click();
    return true;
  } catch {
    return false;
  }
}

/** "작성 중인 글이 있습니다" 팝업, 도움말 패널 등 글쓰기 화면의 방해 요소 정리 */
async function dismissPopups(frame) {
  // 이전에 쓰다 만 글 불러오기 → 취소 (새 글로 시작)
  await clickIfVisible(frame, ".se-popup-button-cancel");
  await clickIfVisible(frame, "button:has-text('취소')", { timeout: 1000 });
  // 도움말/가이드 패널 닫기
  await clickIfVisible(frame, ".se-help-panel-close-button", { timeout: 1500 });
  await clickIfVisible(frame, "button[class*='se-help'][class*='close']", { timeout: 800 });
}

async function typeLines(frame, lines) {
  for (let i = 0; i < lines.length; i++) {
    if (lines[i]) await frame.page().keyboard.insertText(lines[i]);
    if (i < lines.length - 1) await frame.page().keyboard.press("Enter");
  }
}

/** 생성된 블록을 에디터에 입력할 줄 목록으로 평탄화 (문단 사이는 빈 줄) */
export function blocksToLines(blocks) {
  const lines = [];
  for (const b of blocks) {
    if (b.type === "image_placeholder") continue; // 사진은 자동으로 넣지 않는다
    if (b.type === "divider") {
      if (lines.length && lines[lines.length - 1] !== "") lines.push("");
      lines.push("─────────", "");
      continue;
    }
    const text = (b.text || "").trim();
    if (!text) continue;
    if (b.type === "quote") lines.push(`“${text}”`);
    else lines.push(...text.split("\n"));
    lines.push("");
  }
  while (lines.length && lines[lines.length - 1] === "") lines.pop();
  return lines;
}

export async function writePost(page, draft, { publish = false } = {}) {
  let frame = null;
  for (const url of WRITE_URLS(config.blogId)) {
    await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
    try {
      frame = await findEditorFrame(page, { timeout: 20000 });
      break;
    } catch {
      console.warn(`에디터를 찾지 못해 다른 글쓰기 URL 로 재시도: ${url}`);
    }
  }
  if (!frame) throw new Error("글쓰기 화면을 열지 못했습니다.");
  await page.waitForTimeout(1500);
  await dismissPopups(frame);
  await snapshot(page, "editor-open");

  // 제목
  const title = frame.locator(".se-documentTitle .se-text-paragraph, .se-title-text .se-text-paragraph, .se-title-text").first();
  await title.waitFor({ state: "visible", timeout: 15000 });
  await title.click();
  await page.keyboard.insertText(draft.title);

  // 본문: 제목에서 Enter 나 Tab 으로 본문 첫 문단으로 이동하는 대신 직접 클릭
  const body = frame.locator(".se-main-container .se-component.se-text .se-text-paragraph").last();
  await body.waitFor({ state: "visible", timeout: 15000 });
  await body.click();
  await typeLines(frame, blocksToLines(draft.blocks));
  await snapshot(page, "body-typed");

  if (!publish) {
    // 임시저장
    const saved = await clickIfVisible(frame, "button:has-text('저장')", { timeout: 5000 });
    if (!saved) throw new Error("임시저장 버튼을 찾지 못했습니다.");
    await page.waitForTimeout(2000);
    await snapshot(page, "draft-saved");
    return { status: "draft" };
  }

  // 발행 버튼 → 발행 설정 레이어
  const opened = await clickIfVisible(frame, "button[class*='publish_btn'], button:has-text('발행')", { timeout: 5000 });
  if (!opened) throw new Error("발행 버튼을 찾지 못했습니다.");
  const layer = frame.locator("[class*='layer_publish'], [class*='publish_layer'], .se-popup-dim ~ *").first();
  await layer.waitFor({ state: "visible", timeout: 10000 }).catch(() => {});

  // 태그
  const tagInput = frame.locator("#tag-input, input[placeholder*='태그']").first();
  if (draft.tags?.length && (await tagInput.count())) {
    await tagInput.click();
    for (const tag of draft.tags.slice(0, 10)) {
      await page.keyboard.insertText(tag.replace(/^#/, "").replace(/\s+/g, ""));
      await page.keyboard.press("Enter");
      await page.waitForTimeout(150);
    }
  }
  await snapshot(page, "publish-layer");

  // 레이어 안의 최종 발행 버튼
  const confirm = frame
    .locator("[class*='layer_publish'] button:has-text('발행'), button[class*='confirm_btn']:has-text('발행')")
    .last();
  await confirm.waitFor({ state: "visible", timeout: 10000 });
  await confirm.click();

  // 발행되면 글 보기 화면(logNo 포함)으로 이동한다
  await page.waitForURL(/logNo=\d+|blog\.naver\.com\/[^/]+\/\d+/, { timeout: 60000 });
  await snapshot(page, "published");
  const url = page.url();
  const logNo = (url.match(/logNo=(\d+)/) || url.match(/\/(\d+)(?:[?#]|$)/) || [])[1] || null;
  return { status: "published", url, logNo };
}

export async function postDraftFile(file, { publish = false, headless = true } = {}) {
  const draft = JSON.parse(fs.readFileSync(file, "utf8"));
  const { context, page } = await openBrowser({ headless });
  try {
    if (!(await isLoggedIn(context))) {
      throw new Error("네이버 로그인 세션이 없습니다. 먼저 `npm run login` 을 실행하세요.");
    }
    const result = await writePost(page, draft, { publish });
    return result;
  } catch (e) {
    await snapshot(page, "error");
    throw e;
  } finally {
    await context.close();
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const file = args.positional[0];
  if (!file || !fs.existsSync(file)) {
    console.error("사용법: npm run post -- drafts/파일.json [--publish] [--headful]");
    process.exit(1);
  }
  const result = await postDraftFile(file, { publish: !!args.publish, headless: !args.headful });
  if (result.status === "draft") console.log("임시저장 완료. 네이버 블로그 글쓰기 화면의 '저장된 글' 에서 확인하세요.");
  else console.log(`발행 완료: ${result.url}`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
