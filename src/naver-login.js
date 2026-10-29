/**
 * 네이버 로그인 세션을 만든다.
 *  - 켜져 있는 크롬에 붙은 경우: 그 크롬에 로그인 탭을 열어 주고 로그인될 때까지 기다린다.
 *  - 아니면 창을 띄워서 직접 로그인하면 .naver-profile 에 세션이 저장된다.
 *
 *   npm run login
 */
import { openBrowser, isLoggedIn, snapshot } from "./browser.js";
import { config } from "./config.js";

async function main() {
  const { mode, context, page, close } = await openBrowser({ headless: false });
  try {
    if (await isLoggedIn(context)) {
      console.log(`이미 로그인되어 있습니다 (${mode}).`);
      return;
    }
    await page.goto("https://nid.naver.com/nidlogin.login?url=https%3A%2F%2Fblog.naver.com%2F" + config.blogId, {
      waitUntil: "domcontentloaded",
    });
    console.log("브라우저 창에서 네이버에 로그인하세요. (최대 5분 대기)");
    const deadline = Date.now() + 5 * 60 * 1000;
    while (Date.now() < deadline) {
      if (await isLoggedIn(context)) {
        console.log(mode === "cdp" ? "로그인 완료. 크롬을 닫지 않는 한 세션이 유지됩니다." : `로그인 완료. 세션이 ${config.profileDir} 에 저장되었습니다.`);
        await snapshot(page, "login-ok");
        return;
      }
      await page.waitForTimeout(1000);
    }
    throw new Error("5분 안에 로그인이 확인되지 않았습니다.");
  } finally {
    await close();
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
