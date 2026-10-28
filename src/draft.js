// 메모 + 사진으로 네이버 블로그 초안(JSON)을 만듭니다.
// 사용법: npm run draft            → notes/ 에서 가장 최근 .md 파일 사용
//        npm run draft -- notes/파일.md
import fs from "node:fs";
import path from "node:path";
import Anthropic from "@anthropic-ai/sdk";
import { z } from "zod";
import { zodOutputFormat } from "@anthropic-ai/sdk/helpers/zod";
import { config, requireEnv } from "./config.js";

requireEnv("ANTHROPIC_API_KEY");

const DraftSchema = z.object({
  title: z.string().describe("네이버 검색에 잡히도록 핵심 검색어가 들어간 제목, 30자 이내"),
  paragraphs: z
    .array(z.string())
    .describe("본문 문단 목록. 각 문단은 2~4문장. 첫 문단은 인사 없이 바로 이야기로 시작"),
  photo_captions: z
    .array(z.string())
    .describe("사진 순서대로 붙일 한 줄 캡션. 사진 수와 같은 길이"),
  tags: z.array(z.string()).describe("해시태그 8~12개, # 없이 단어만"),
});

const SYSTEM = `당신은 도자기 공방을 운영하는 작가의 네이버 블로그 글을 대신 써 주는 조수입니다.

원칙
- 작가가 준 메모에 있는 사실만 씁니다. 없는 경험, 수치, 효능을 지어내지 않습니다.
- 작가가 직접 겪은 일을 1인칭으로, 말하듯이 씁니다. 광고 문구, 과장, 느낌표 남발을 피합니다.
- 네이버 검색 사용자가 궁금해할 정보(관리법, 사용법, 차이점, 과정)가 한 가지 이상 들어가야 합니다.
- 샘플 글이 주어지면 그 말투, 문장 길이, 자주 쓰는 표현을 따라 합니다.
- 본문 끝 문단에 스마트스토어 링크를 한 번만 자연스럽게 넣습니다. 링크 문구는 "구매는 여기서" 처럼 짧게.
- 전체 길이는 공백 포함 1200~2000자.`;

function latestNote() {
  const files = fs
    .readdirSync(config.notesDir)
    .filter((f) => f.endsWith(".md") && f !== "example.md")
    .map((f) => path.join(config.notesDir, f))
    .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs);
  if (files.length === 0) {
    console.error(`notes/ 폴더에 메모(.md)가 없습니다. notes/example.md 를 참고해 하나 만들어 주세요.`);
    process.exit(1);
  }
  return files[0];
}

function listPhotos() {
  if (!fs.existsSync(config.photosDir)) return [];
  return fs
    .readdirSync(config.photosDir)
    .filter((f) => /\.(jpe?g|png|webp)$/i.test(f))
    .sort()
    .map((f) => path.join(config.photosDir, f));
}

function readSamples() {
  if (!fs.existsSync(config.samplesDir)) return [];
  return fs
    .readdirSync(config.samplesDir)
    .filter((f) => f.endsWith(".txt") || (f.endsWith(".md") && f !== "README.md"))
    .map((f) => fs.readFileSync(path.join(config.samplesDir, f), "utf8").trim())
    .filter(Boolean);
}

function imageBlock(file) {
  const ext = path.extname(file).toLowerCase();
  const media = ext === ".png" ? "image/png" : ext === ".webp" ? "image/webp" : "image/jpeg";
  return {
    type: "image",
    source: { type: "base64", media_type: media, data: fs.readFileSync(file).toString("base64") },
  };
}

async function main() {
  const notePath = process.argv[2] ? path.resolve(process.argv[2]) : latestNote();
  const note = fs.readFileSync(notePath, "utf8");
  const photos = listPhotos();
  const samples = readSamples();

  console.log(`메모: ${path.basename(notePath)}`);
  console.log(`사진: ${photos.length}장, 참고 글: ${samples.length}편`);

  const content = [];
  if (samples.length) {
    content.push({
      type: "text",
      text: `[작가가 예전에 쓴 글 샘플]\n\n${samples.map((s, i) => `--- 샘플 ${i + 1} ---\n${s}`).join("\n\n")}`,
    });
  }
  for (const p of photos) content.push(imageBlock(p));
  content.push({
    type: "text",
    text: `[작가 메모]\n${note}\n\n[브랜드] ${config.brandName}\n[스마트스토어] ${config.storeUrl || "(없음)"}\n[사진 수] ${photos.length}\n\n위 메모와 사진으로 블로그 글 초안을 만들어 주세요. 사진이 ${photos.length}장이면 photo_captions 도 정확히 ${photos.length}개여야 합니다.`,
  });

  const client = new Anthropic();
  const response = await client.beta.messages.create({
    model: "claude-opus-5-5",
    max_tokens: 16000,
    output_config: { effort: "high", format: zodOutputFormat(DraftSchema) },
    betas: ["server-side-fallback-2026-07-01"],
    fallbacks: "default",
    system: SYSTEM,
    messages: [{ role: "user", content }],
  });

  if (response.stop_reason === "refusal") {
    console.error("모델이 응답을 거부했습니다. 메모 내용을 바꿔 다시 시도해 주세요.");
    process.exit(1);
  }
  const text = response.content.find((b) => b.type === "text")?.text ?? "";
  const draft = DraftSchema.parse(JSON.parse(text));

  fs.mkdirSync(config.draftsDir, { recursive: true });
  const stamp = new Date().toISOString().slice(0, 10);
  const out = path.join(config.draftsDir, `${stamp}-${path.basename(notePath, ".md")}.json`);
  fs.writeFileSync(out, JSON.stringify({ ...draft, photos, note: notePath }, null, 2));

  console.log(`\n제목: ${draft.title}\n`);
  console.log(draft.paragraphs.join("\n\n"));
  console.log(`\n태그: ${draft.tags.map((t) => `#${t}`).join(" ")}`);
  console.log(`\n초안 저장: ${out}`);
}

main().catch((err) => {
  console.error(err instanceof Anthropic.APIError ? `API 오류 ${err.status}: ${err.message}` : err);
  process.exit(1);
});
