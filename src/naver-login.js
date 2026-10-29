/**
 * 네이버에 한 번 수동 로그인해서 세션을 .naver-profile 에 저장한다.
 * 창이 뜨면 직접 아이디/비밀번호(+2단계 인증)를 입력하면 된다.
 *
 *   npm run login
 */
import { openBrowser, isLoggedIn, snapshot } from "./browser.js";
import { config } from "./config.js";

async function main() {
  const { context, page } = await openBrowser({ headless: false });
  try {
    if (await isLoggedIn(context)) {
      console.log("이미 로그인되어 있습니다. 세션을 다시 만들려면 .naver-profile 폴더를 지우고 다시 실행하세요.");
      return;
    }
    await page.goto("https://nid.naver.com/nidlogin.login?url=https%3A%2F%2Fblog.naver.com%2F" + config.blogId, {
      waitUntil: "domcontentloaded",
    });
    console.log("브라우저 창에서 네이버에 로그인하세요. (최대 5분 대기)");
    const deadline = Date.now() + 5 * 60 * 1000;
    while (Date.now() < deadline) {
      if (await isLoggedIn(context)) {
        console.log(`로그인 완료. 세션이 ${config.profileDir} 에 저장되었습니다.`);
        await snapshot(page, "login-ok");
        return;
      }
      await page.waitForTimeout(1000);
    }
    throw new Error("5분 안에 로그인이 확인되지 않았습니다.");
  } finally {
    await context.close();
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
