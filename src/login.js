// 처음 한 번만: 전용 크롬 프로필을 열어 네이버에 직접 로그인합니다.
// 로그인 상태는 .chrome-profile/ 에 저장되어 이후 실행에서 재사용됩니다.
import { chromium } from "playwright";
import { config } from "./config.js";
import { waitForEnter } from "./util.js";

const ctx = await chromium.launchPersistentContext(config.profileDir, {
  channel: "chrome",
  headless: false,
  viewport: null,
  args: ["--disable-blink-features=AutomationControlled"],
});
const page = ctx.pages()[0] ?? (await ctx.newPage());
await page.goto("https://nid.naver.com/nidlogin.login");

console.log("\n열린 크롬 창에서 네이버에 로그인해 주세요. (2단계 인증까지 끝내기)");
await waitForEnter("로그인이 끝나면 여기서 Enter 를 누르세요...");
await ctx.close();
console.log("저장 완료. 이제 npm run fill 을 쓸 수 있습니다.");
