import fs from "node:fs";
import { chromium } from "playwright";
import { config } from "./config.js";

const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36";

/** 원격 디버깅 포트가 열린 크롬이 떠 있는지 확인 */
export async function cdpAvailable(url = config.cdpUrl) {
  try {
    const res = await fetch(`${url.replace(/\/$/, "")}/json/version`, { signal: AbortSignal.timeout(1500) });
    return res.ok ? await res.json() : null;
  } catch {
    return null;
  }
}

/**
 * 켜져 있는 크롬(원격 디버깅 포트)에 붙어서 새 탭을 하나 연다.
 * 로그인 쿠키 등 그 크롬의 상태를 그대로 쓴다. close() 는 탭만 닫고 크롬은 그대로 둔다.
 */
export async function attachToChrome(url = config.cdpUrl) {
  const browser = await chromium.connectOverCDP(url, { timeout: 10000 });
  const context = browser.contexts()[0] || (await browser.newContext());
  const page = await context.newPage();
  return {
    mode: "cdp",
    browser,
    context,
    page,
    close: async () => {
      await page.close().catch(() => {});
      await browser.close().catch(() => {}); // 연결만 끊고 크롬은 유지
    },
  };
}

/** 스크립트가 직접 크롬을 띄우는 방식 (영구 프로필에 로그인 세션 저장) */
export async function launchOwnBrowser({ headless = true, persistent = true } = {}) {
  fs.mkdirSync(config.logsDir, { recursive: true });
  const common = { locale: "ko-KR", timezoneId: "Asia/Seoul", userAgent: USER_AGENT };
  if (persistent) {
    fs.mkdirSync(config.profileDir, { recursive: true });
    const context = await chromium.launchPersistentContext(config.profileDir, {
      ...common,
      headless,
      channel: config.browserChannel,
      viewport: { width: 1400, height: 1000 },
      args: ["--disable-blink-features=AutomationControlled"],
    });
    const page = context.pages()[0] || (await context.newPage());
    return { mode: "profile", context, page, close: () => context.close() };
  }
  const browser = await chromium.launch({ headless: true, channel: config.browserChannel });
  const context = await browser.newContext(common);
  const page = await context.newPage();
  return { mode: "ephemeral", browser, context, page, close: () => browser.close() };
}

/**
 * 설정(NAVER_BROWSER_MODE)에 따라 브라우저를 연다.
 *  - cdp     : 켜져 있는 크롬에 붙는다. 없으면 오류.
 *  - profile : 스크립트가 크롬을 띄운다.
 *  - auto    : 크롬이 떠 있으면 붙고, 아니면 띄운다.
 * needLogin=false 면 (공개 글 읽기) 띄울 때 1회용 브라우저를 쓴다.
 */
export async function openBrowser({ headless = true, needLogin = true } = {}) {
  fs.mkdirSync(config.logsDir, { recursive: true });
  const mode = config.browserMode;
  if (mode === "cdp" || mode === "auto") {
    const info = await cdpAvailable();
    if (info) {
      const b = await attachToChrome();
      console.log(`켜져 있는 크롬에 연결했습니다 (${info.Browser || "Chrome"}, ${config.cdpUrl})`);
      return b;
    }
    if (mode === "cdp") {
      throw new Error(
        `${config.cdpUrl} 에서 크롬을 찾지 못했습니다. 먼저 \`npm run chrome\` 으로 원격 디버깅 포트가 열린 크롬을 띄우세요.`,
      );
    }
  }
  return launchOwnBrowser({ headless, persistent: needLogin });
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
