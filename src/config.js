import "dotenv/config";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(here, "..");

export const config = {
  blogId: process.env.NAVER_BLOG_ID ?? "",
  storeUrl: process.env.SMARTSTORE_URL ?? "",
  brandName: process.env.BRAND_NAME ?? "우리 공방",
  profileDir: path.join(ROOT, ".chrome-profile"),
  notesDir: path.join(ROOT, "notes"),
  photosDir: path.join(ROOT, "photos"),
  samplesDir: path.join(ROOT, "samples"),
  draftsDir: path.join(ROOT, "drafts"),
};

export function requireEnv(name) {
  const v = process.env[name];
  if (!v) {
    console.error(`.env 파일에 ${name} 값이 없습니다. .env.example 을 참고해 채워 주세요.`);
    process.exit(1);
  }
  return v;
}
