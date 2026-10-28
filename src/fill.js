// 초안(JSON)을 네이버 블로그 글쓰기 화면에 채워 넣습니다. 발행 버튼은 누르지 않습니다.
// 사용법: npm run fill                 → drafts/ 의 가장 최근 초안
//        npm run fill -- drafts/파일.json
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { chromium } from "playwright";
import { config, requireEnv } from "./config.js";
import { waitForEnter, sleep } from "./util.js";

const blogId = requireEnv("NAVER_BLOG_ID");

function latestDraft() {
  if (!fs.existsSync(config.draftsDir)) return null;
  const files = fs
    .readdirSync(config.draftsDir)
    .filter((f) => f.endsWith(".json"))
    .map((f) => path.join(config.draftsDir, f))
    .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
  return files[0] ?? null;
}

function draftAsPlainText(d) {
  return [d.title, "", ...d.paragraphs.flatMap((p) => [p, ""]), d.tags.map((t) => `#${t}`).join(" ")].join("\n");
}

function copyToClipboard(text) {
  try {
    execFileSync("pbcopy", { input: text });
    return true;
  } catch {
    return false;
  }
}

async function dismissPopups(page) {
  // "작성 중인 글이 있습니다" 같은 안내창은 '취소'로 닫아 새 글로 시작
  for (const label of ["취소", "닫기"]) {
    const btn = page.frameLocator("iframe#mainFrame").getByRole("button", { name: label }).first();
    if (await btn.isVisible({ timeout: 1500 }).catch(() => false)) {
      await btn.click();
      await sleep(500);
    }
  }
}

async function main() {
  const draftPath = process.argv[2] ? path.resolve(process.argv[2]) : latestDraft();
  if (!draftPath) {
    console.error("drafts/ 폴더에 초안이 없습니다. 먼저 npm run draft 를 실행해 주세요.");
    process.exit(1);
  }
  const draft = JSON.parse(fs.readFileSync(draftPath, "utf8"));
  console.log(`초안: ${path.basename(draftPath)}`);

  if (!fs.existsSync(config.profileDir)) {
    console.error("로그인된 크롬 프로필이 없습니다. 먼저 npm run login 을 실행해 주세요.");
    process.exit(1);
  }

  const ctx = await chromium.launchPersistentContext(config.profileDir, {
    channel: "chrome",
    headless: false,
    viewport: null,
    args: ["--disable-blink-features=AutomationControlled"],
  });
  const page = ctx.pages()[0] ?? (await ctx.newPage());

  await page.goto(`https://blog.naver.com/${blogId}/postwrite`, { waitUntil: "domcontentloaded" });
  await sleep(3000);

  // 글쓰기 화면은 iframe#mainFrame 안에 있을 수도, 바로 떠 있을 수도 있어 둘 다 시도
  const hasFrame = (await page.locator("iframe#mainFrame").count()) > 0;
  const root = hasFrame ? page.frameLocator("iframe#mainFrame") : page;
  if (hasFrame) await dismissPopups(page);

  try {
    // 1) 제목
    const title = root.locator(".se-title-text, [data-placeholder='제목']").first();
    await title.waitFor({ timeout: 15000 });
    await title.click();
    await page.keyboard.insertText(draft.title);
    await sleep(300);

    // 2) 본문: 제목에서 Enter 하면 본문 첫 문단으로 이동
    await page.keyboard.press("Enter");
    await sleep(300);
    for (const [i, para] of draft.paragraphs.entries()) {
      await page.keyboard.insertText(para);
      await page.keyboard.press("Enter");
      await page.keyboard.press("Enter");
      await sleep(150);
      // 사진은 문단 사이에 하나씩 끼워 넣기
      const photo = draft.photos?.[i];
      if (photo && fs.existsSync(photo)) {
        await insertPhoto(page, root, photo);
        const cap = draft.photo_captions?.[i];
        if (cap) {
          await page.keyboard.insertText(cap);
          await page.keyboard.press("Enter");
        }
      }
    }
    // 3) 남은 사진 (문단보다 사진이 많을 때)
    for (let i = draft.paragraphs.length; i < (draft.photos?.length ?? 0); i++) {
      await insertPhoto(page, root, draft.photos[i]);
    }
    // 4) 태그는 본문 끝에 해시태그로 (에디터가 자동으로 태그 처리)
    await page.keyboard.insertText(draft.tags.map((t) => `#${t}`).join(" "));

    console.log("\n글쓰기 화면에 초안을 채웠습니다. 읽어 보고 고친 뒤 직접 '발행'을 눌러 주세요.");
  } catch (err) {
    console.error(`\n에디터 자동 입력에 실패했습니다 (${err.message.split("\n")[0]}).`);
    console.error("네이버 에디터 구조가 바뀌었을 수 있습니다. 대신 초안 전체를 클립보드에 복사해 두었습니다.");
    if (copyToClipboard(draftAsPlainText(draft))) {
      console.error("글쓰기 화면 본문을 클릭하고 Cmd+V 로 붙여 넣으세요. 사진은 photos/ 폴더에서 직접 올려 주세요.");
    }
  }

  await waitForEnter("발행을 마쳤으면 Enter 를 눌러 창을 닫으세요 (창은 그대로 두고 터미널만 닫아도 됩니다)...");
  await ctx.close().catch(() => {});
}

async function insertPhoto(page, root, file) {
  const btn = root.locator("button[data-name='image'], .se-image-toolbar-button, button[title*='사진']").first();
  const [chooser] = await Promise.all([
    page.waitForEvent("filechooser", { timeout: 10000 }),
    btn.click(),
  ]);
  await chooser.setFiles(file);
  await sleep(2500); // 업로드 대기
  // 사진 아래 줄로 커서 이동
  await page.keyboard.press("End");
  await page.keyboard.press("Enter");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
