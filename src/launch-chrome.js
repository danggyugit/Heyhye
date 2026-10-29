/**
 * 원격 디버깅 포트(기본 9222)가 열린 크롬을 띄운다. 이 창에서 네이버에 로그인해 두면
 * 이후 fetch / post 스크립트가 이 크롬에 붙어서 로그인된 상태 그대로 작업한다.
 *
 *   npm run chrome
 *
 * 크롬 136 이후로는 평소 쓰는 기본 프로필로는 원격 디버깅을 열 수 없어서
 * 전용 프로필(.chrome-profile)을 쓴다. 로그인은 처음 한 번만 하면 유지된다.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { config } from "./config.js";
import { cdpAvailable } from "./browser.js";

function findChrome() {
  if (config.chromePath) return config.chromePath;
  const candidates = {
    win32: [
      path.join(process.env["PROGRAMFILES"] || "C:\\Program Files", "Google\\Chrome\\Application\\chrome.exe"),
      path.join(process.env["PROGRAMFILES(X86)"] || "C:\\Program Files (x86)", "Google\\Chrome\\Application\\chrome.exe"),
      path.join(process.env.LOCALAPPDATA || "", "Google\\Chrome\\Application\\chrome.exe"),
    ],
    darwin: ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"],
    linux: ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/chromium", "/usr/bin/chromium-browser"],
  }[os.platform()] || [];
  const found = candidates.find((p) => p && fs.existsSync(p));
  if (!found) throw new Error("크롬 실행 파일을 찾지 못했습니다. .env 에 CHROME_PATH 를 지정하세요.");
  return found;
}

async function main() {
  const port = new URL(config.cdpUrl).port || "9222";
  if (await cdpAvailable()) {
    console.log(`이미 ${config.cdpUrl} 에 크롬이 떠 있습니다. 그대로 사용하면 됩니다.`);
    return;
  }
  fs.mkdirSync(config.chromeProfileDir, { recursive: true });
  const exe = findChrome();
  const args = [
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${config.chromeProfileDir}`,
    "--no-first-run",
    "--no-default-browser-check",
    ...config.chromeExtraArgs,
    `https://nid.naver.com/nidlogin.login?url=https%3A%2F%2Fblog.naver.com%2F${config.blogId}`,
  ];
  const child = spawn(exe, args, { detached: true, stdio: "ignore" });
  child.unref();

  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    const info = await cdpAvailable();
    if (info) {
      console.log(`크롬을 띄웠습니다: ${info.Browser || exe} (${config.cdpUrl})`);
      console.log("이 창에서 네이버에 로그인한 뒤 창을 닫지 말고 두세요. 이후 스크립트가 이 크롬에 붙어서 작업합니다.");
      return;
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("크롬을 띄웠지만 원격 디버깅 포트가 열리지 않았습니다. 이미 떠 있는 크롬을 모두 종료하고 다시 시도하세요.");
}

main().catch((e) => {
  console.error(e.message || e);
  process.exit(1);
});
