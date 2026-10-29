/**
 * 기존에 쓴 네이버 블로그 글을 헤드리스 크롬으로 읽어 references/ 에 저장한다.
 *
 *   npm run fetch                      # .env 의 REFERENCE_LOG_NOS
 *   npm run fetch -- 224418055011 ...  # 글 번호 직접 지정
 *   npm run fetch -- https://blog.naver.com/PostView.naver?blogId=sk15y&logNo=224418055011
 */
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { config, postViewUrl } from "./config.js";
import { openEphemeralBrowser } from "./browser.js";

export function parseLogNo(arg) {
  const m = String(arg).match(/logNo=(\d+)/) || String(arg).match(/blog\.naver\.com\/[^/]+\/(\d+)/) || String(arg).match(/^(\d+)$/);
  if (!m) throw new Error(`글 번호를 알 수 없는 인자입니다: ${arg}`);
  return m[1];
}

/** 브라우저 안에서 실행되는 SmartEditor ONE 본문 추출기 */
export function extractInBrowser() {
  const clean = (s) => (s || "").replace(/​/g, "").replace(/\s+\n/g, "\n").trim();
  const title =
    clean(document.querySelector(".se-title-text")?.innerText) ||
    clean(document.querySelector(".pcol1 .htitle, .tit_h3, .itemSubjectBoldfont")?.innerText) ||
    document.title;
  const date = clean(document.querySelector(".se_publishDate, .blog2_container .date, .date")?.innerText);
  const category = clean(document.querySelector(".blog2_series a, .blog2_categorylink, .post_category, .category")?.innerText);
  const tags = [...document.querySelectorAll("#tagList a, .post_tag a, .tag_area a, .blog2_tag a")]
    .map((a) => clean(a.innerText).replace(/^#/, ""))
    .filter(Boolean);

  const blocks = [];
  const main = document.querySelector(".se-main-container");
  if (main) {
    for (const comp of main.querySelectorAll(":scope > .se-component, .se-section > .se-component")) {
      const cls = [...comp.classList];
      const type = (cls.find((c) => /^se-(?!component|l-|section)/.test(c)) || "se-unknown").replace(/^se-/, "");
      if (type === "text") {
        for (const p of comp.querySelectorAll(".se-text-paragraph")) {
          const align = [...p.classList].find((c) => c.startsWith("se-text-paragraph-align-"))?.replace("se-text-paragraph-align-", "") || "left";
          const bold = !!p.querySelector("b, strong, .se-fs-fs19, .se-fs-fs24");
          blocks.push({ type: "paragraph", text: clean(p.innerText), align, bold });
        }
      } else if (type === "image" || type === "imageGroup" || type === "imageStrip") {
        const imgs = [...comp.querySelectorAll("img")].map((i) => i.getAttribute("data-lazy-src") || i.src);
        const caption = clean(comp.querySelector(".se-caption")?.innerText);
        blocks.push({ type: "image", images: imgs, caption });
      } else if (type === "quotation") {
        blocks.push({
          type: "quote",
          text: clean(comp.querySelector(".se-quote")?.innerText),
          cite: clean(comp.querySelector(".se-cite")?.innerText),
        });
      } else if (type === "horizontalLine") {
        blocks.push({ type: "divider" });
      } else if (type === "sectionTitle") {
        blocks.push({ type: "heading", text: clean(comp.innerText) });
      } else if (type === "oglink") {
        blocks.push({ type: "link", text: clean(comp.innerText), url: comp.querySelector("a")?.href || "" });
      } else if (type === "sticker") {
        blocks.push({ type: "sticker" });
      } else if (type === "placesMap") {
        blocks.push({ type: "map", text: clean(comp.innerText) });
      } else {
        blocks.push({ type, text: clean(comp.innerText) });
      }
    }
  } else {
    // 구형 에디터 글
    const legacy = document.querySelector("#postViewArea, .post-view, #post-view");
    for (const line of clean(legacy?.innerText).split("\n")) blocks.push({ type: "paragraph", text: line, align: "left", bold: false });
  }
  return { title, date, category, tags, blocks };
}

export function toMarkdown(post) {
  const out = [`# ${post.title}`, ""];
  if (post.date) out.push(`- 작성일: ${post.date}`);
  if (post.category) out.push(`- 카테고리: ${post.category}`);
  if (post.tags?.length) out.push(`- 태그: ${post.tags.map((t) => `#${t}`).join(" ")}`);
  out.push("");
  for (const b of post.blocks) {
    switch (b.type) {
      case "paragraph":
        out.push(b.bold ? `**${b.text}**` : b.text, "");
        break;
      case "heading":
        out.push(`## ${b.text}`, "");
        break;
      case "quote":
        out.push(`> ${b.text}${b.cite ? `\n> — ${b.cite}` : ""}`, "");
        break;
      case "divider":
        out.push("---", "");
        break;
      case "image":
        out.push(`[사진 ${b.images.length}장${b.caption ? `: ${b.caption}` : ""}]`, "");
        break;
      case "link":
        out.push(`[링크] ${b.text} ${b.url}`, "");
        break;
      case "sticker":
        out.push("[스티커]", "");
        break;
      default:
        if (b.text) out.push(`[${b.type}] ${b.text}`, "");
    }
  }
  return out.join("\n").replace(/\n{3,}/g, "\n\n");
}

export async function fetchReference(page, logNo, blogId = config.blogId) {
  const url = postViewUrl(blogId, logNo);
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForSelector(".se-main-container, #postViewArea", { timeout: 30000 }).catch(() => {});
  // 지연 로딩 이미지와 본문이 모두 그려지도록 끝까지 스크롤
  await page.evaluate(async () => {
    for (let y = 0; y < document.body.scrollHeight; y += 800) {
      window.scrollTo(0, y);
      await new Promise((r) => setTimeout(r, 120));
    }
  });
  const post = await page.evaluate(extractInBrowser);
  return { logNo, url, fetchedAt: new Date().toISOString(), ...post };
}

export function saveReference(post) {
  fs.mkdirSync(config.referencesDir, { recursive: true });
  const base = path.join(config.referencesDir, post.logNo);
  fs.writeFileSync(`${base}.json`, JSON.stringify(post, null, 2));
  fs.writeFileSync(`${base}.md`, toMarkdown(post));
  return base;
}

async function main() {
  const args = process.argv.slice(2);
  const logNos = (args.length ? args : config.referenceLogNos).map(parseLogNo);
  const { browser, page } = await openEphemeralBrowser();
  try {
    for (const logNo of logNos) {
      const post = await fetchReference(page, logNo);
      const base = saveReference(post);
      const words = post.blocks.filter((b) => b.type === "paragraph").reduce((n, b) => n + b.text.length, 0);
      console.log(`[${logNo}] ${post.title} — 블록 ${post.blocks.length}개, 본문 ${words}자 → ${base}.md`);
    }
  } finally {
    await browser.close();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
