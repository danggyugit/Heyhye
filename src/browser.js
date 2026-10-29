import fs from "node:fs";
import { chromium } from "playwright";
import { config } from "./config.js";

const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36";

/**
 * 로그인 세션이 유지되는 영구 프로필로 크롬을 연다.
 * headless=false 로 열면 창이 보이고, true 면 백그라운드에서 돈다.
 */
export async function openBrowser({ headless = true } = {}) {
  fs.mkdirSync(config.profileDir, { recursive: true });
  fs.mkdirSync(config.logsDir, { recursive: true });
  const context = await chromium.launchPersistentContext(config.profileDir, {
    headless,
    channel: config.browserChannel,
    locale: "ko-KR",
    timezoneId: "Asia/Seoul",
    viewport: { width: 1400, height: 1000 },
    userAgent: USER_AGENT,
    args: ["--disable-blink-features=AutomationControlled"],
  });
  const page = context.pages()[0] || (await context.newPage());
  return { context, page };
}

/** 가벼운 1회용 브라우저 (공개 글 읽기 등 로그인이 필요 없는 작업용) */
export async function openEphemeralBrowser() {
  const browser = await chromium.launch({ headless: true, channel: config.browserChannel });
  const context = await browser.newContext({
    locale: "ko-KR",
    timezoneId: "Asia/Seoul",
    userAgent: USER_AGENT,
  });
  const page = await context.newPage();
  return { browser, context, page };
}

export async function isLoggedIn(context) {
  const cookies = await context.cookies("https://www.naver.com");
  return cookies.some((c) => c.name === "NID_AUT") && cookies.some((c) => c.name === "NID_SES");
}

/**
 * 글쓰기 화면은 페이지 본체 또는 iframe(#mainFrame) 안에 있을 수 있어서
 * SmartEditor 컨테이너가 있는 프레임을 찾아 돌려준다.
 */
export async function findEditorFrame(page, { timeout = 30000 } = {}) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    for (const frame of page.frames()) {
      try {
        if (await frame.locator(".se-main-container").count()) return frame;
      } catch {
        /* 프레임이 바뀌는 중이면 무시 */
      }
    }
    await page.waitForTimeout(500);
  }
  throw new Error("SmartEditor(.se-main-container)를 찾지 못했습니다. 로그인 상태와 글쓰기 URL을 확인하세요.");
}

export async function snapshot(page, name) {
  const file = `${config.logsDir}/${new Date().toISOString().replace(/[:.]/g, "-")}-${name}.png`;
  try {
    await page.screenshot({ path: file, fullPage: false });
    return file;
  } catch {
    return null;
  }
}
