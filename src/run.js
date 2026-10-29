/**
 * 참고 글 읽기 → 글 생성 → 네이버 입력 까지 한 번에.
 *
 *   npm run all -- "주제" [--notes "메모"] [--publish] [--headful]
 */
import fs from "node:fs";
import { config } from "./config.js";
import { openEphemeralBrowser } from "./browser.js";
import { fetchReference, saveReference } from "./fetch-references.js";
import { generatePost, saveDraft, draftToMarkdown, loadReferences, parseArgs } from "./generate-post.js";
import { postDraftFile } from "./post-to-naver.js";

async function ensureReferences() {
  const existing = loadReferences();
  if (existing.length) return existing;
  console.log("참고 글이 없어 먼저 가져옵니다...");
  const { browser, page } = await openEphemeralBrowser();
  try {
    for (const logNo of config.referenceLogNos) saveReference(await fetchReference(page, logNo));
  } finally {
    await browser.close();
  }
  return loadReferences();
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const topic = args.positional.join(" ").trim();
  if (!topic) {
    console.error('사용법: npm run all -- "글 주제" [--notes "메모"] [--publish] [--headful]');
    process.exit(1);
  }
  const references = await ensureReferences();
  console.log(`참고 글 ${references.length}개로 생성 중...`);
  const draft = await generatePost({ topic, notes: args.notes, references });
  const file = saveDraft(draft, args.out);
  console.log(draftToMarkdown(draft));
  console.log(`\n초안 저장: ${file}`);

  const result = await postDraftFile(file, { publish: !!args.publish, headless: !args.headful });
  if (result.status === "draft") console.log("네이버 블로그에 임시저장했습니다. 발행하려면 --publish 를 붙이세요.");
  else console.log(`발행 완료: ${result.url}`);
  fs.appendFileSync(`${config.logsDir}/history.log`, `${new Date().toISOString()}\t${result.status}\t${draft.title}\t${result.url || file}\n`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
