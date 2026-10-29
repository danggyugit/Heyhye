import "dotenv/config";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(here, "..");

export const config = {
  blogId: process.env.NAVER_BLOG_ID || "sk15y",
  model: process.env.CLAUDE_MODEL || "claude-opus-5-5",
  // 브라우저 연결 방식: "cdp"(켜져 있는 크롬에 붙기) | "profile"(스크립트가 크롬을 띄움) | "auto"(cdp 되면 cdp, 아니면 profile)
  browserMode: process.env.NAVER_BROWSER_MODE || "auto",
  cdpUrl: process.env.CHROME_CDP_URL || "http://127.0.0.1:9222",
  chromePath: process.env.CHROME_PATH || undefined,
  chromeExtraArgs: (process.env.CHROME_EXTRA_ARGS || "").split(" ").filter(Boolean),
  browserChannel: process.env.NAVER_BROWSER_CHANNEL || undefined,
  profileDir: path.resolve(ROOT, process.env.NAVER_PROFILE_DIR || ".naver-profile"),
  chromeProfileDir: path.resolve(ROOT, process.env.CHROME_PROFILE_DIR || ".chrome-profile"),
  referencesDir: path.join(ROOT, "references"),
  draftsDir: path.join(ROOT, "drafts"),
  logsDir: path.join(ROOT, "logs"),
  referenceLogNos: (process.env.REFERENCE_LOG_NOS || "224418055011,224410409150")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean),
};

export function postViewUrl(blogId, logNo) {
  return `https://blog.naver.com/PostView.naver?blogId=${blogId}&logNo=${logNo}`;
}

export function timestamp() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}
