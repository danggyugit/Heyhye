/**
 * references/ 의 기존 글을 참고해 새 글을 Claude 로 생성하고 drafts/ 에 저장한다.
 *
 *   npm run generate -- "주제" [--notes "꼭 넣을 내용"] [--out drafts/파일명.json]
 *   npm run generate -- "주제" --dry-run     # 프롬프트만 출력하고 API 는 호출하지 않음
 */
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import Anthropic from "@anthropic-ai/sdk";
import { z } from "zod";
import { zodOutputFormat } from "@anthropic-ai/sdk/helpers/zod";
import { config, timestamp } from "./config.js";
import { toMarkdown } from "./fetch-references.js";

export const BlockSchema = z.object({
  type: z.enum(["paragraph", "heading", "quote", "divider", "image_placeholder"]),
  text: z.string().describe("문단/소제목/인용 내용. divider 는 빈 문자열. image_placeholder 는 어떤 사진이 들어가면 좋을지 설명"),
});

export const PostSchema = z.object({
  title: z.string().describe("블로그 글 제목"),
  blocks: z.array(BlockSchema).describe("본문을 순서대로 나열한 블록. 참고 글의 문단 길이와 줄바꿈 습관을 따른다"),
  tags: z.array(z.string()).max(10).describe("# 없이 태그만, 최대 10개"),
});

export function loadReferences() {
  if (!fs.existsSync(config.referencesDir)) return [];
  return fs
    .readdirSync(config.referencesDir)
    .filter((f) => f.endsWith(".json"))
    .map((f) => JSON.parse(fs.readFileSync(path.join(config.referencesDir, f), "utf8")));
}

export function buildPrompt(references, topic, notes) {
  const refText = references
    .map((r, i) => `<reference index="${i + 1}" url="${r.url}">\n${toMarkdown(r)}\n</reference>`)
    .join("\n\n");

  const system = `당신은 네이버 블로그 "${config.blogId}" 의 주인 대신 글을 쓰는 고스트라이터입니다.
아래 <reference> 는 이 블로그에 실제로 올라간 글입니다. 새 글은 이 글들을 쓴 사람이 직접 쓴 것처럼 읽혀야 합니다.

참고 글에서 다음을 파악해서 그대로 따르세요.
- 말투와 어미 (존댓말/반말, ~해요/~했다, 감탄사, 이모티콘 유무)
- 문단 길이와 줄바꿈 습관, 글 전체 분량
- 도입부와 마무리 방식, 소제목·인용·구분선을 쓰는지 여부
- 사진을 어디쯤 몇 장 넣는지 (image_placeholder 로 위치를 표시)
- 태그를 어떤 식으로 다는지

지켜야 할 것
- 참고 글의 문장을 베끼지 말고, 주제에 맞는 새 내용을 씁니다.
- 사실을 지어내지 않습니다. 가격·날짜·장소 같은 구체 정보는 사용자가 준 메모에 있을 때만 씁니다. 없으면 일반적으로 서술하거나 [확인 필요] 로 표시합니다.
- 광고 문구, 과장, 상투적인 AI 말투("오늘은 ~에 대해 알아보겠습니다", "마무리하며")는 피합니다.
- 한국어로 씁니다.

${refText}`;

  const user = `주제: ${topic}
${notes ? `\n꼭 반영할 메모:\n${notes}\n` : ""}
위 블로그의 글쓴이가 쓴 것처럼 새 글을 작성하세요.`;

  return { system, user };
}

export async function generatePost({ topic, notes, references = loadReferences(), model = config.model }) {
  if (!references.length) {
    throw new Error("references/ 에 참고 글이 없습니다. 먼저 `npm run fetch` 를 실행하세요.");
  }
  const { system, user } = buildPrompt(references, topic, notes);
  const client = new Anthropic();

  const response = await client.messages.parse({
    model,
    max_tokens: 16000,
    system: [{ type: "text", text: system, cache_control: { type: "ephemeral" } }],
    messages: [{ role: "user", content: user }],
    output_config: { format: zodOutputFormat(PostSchema), effort: "high" },
  });

  if (response.stop_reason === "refusal") {
    throw new Error(`모델이 요청을 거절했습니다: ${response.stop_details?.explanation || "(설명 없음)"}`);
  }
  if (response.stop_reason === "max_tokens") {
    throw new Error("출력이 잘렸습니다 (max_tokens). 주제를 좁히거나 max_tokens 를 늘리세요.");
  }
  if (!response.parsed_output) {
    throw new Error("응답을 JSON 으로 해석하지 못했습니다.");
  }
  return {
    ...response.parsed_output,
    topic,
    notes: notes || "",
    model: response.model,
    generatedAt: new Date().toISOString(),
    usage: response.usage,
  };
}

export function draftToMarkdown(draft) {
  const lines = [`# ${draft.title}`, ""];
  for (const b of draft.blocks) {
    if (b.type === "paragraph") lines.push(b.text, "");
    else if (b.type === "heading") lines.push(`## ${b.text}`, "");
    else if (b.type === "quote") lines.push(`> ${b.text}`, "");
    else if (b.type === "divider") lines.push("---", "");
    else if (b.type === "image_placeholder") lines.push(`[사진: ${b.text}]`, "");
  }
  if (draft.tags?.length) lines.push(draft.tags.map((t) => `#${t}`).join(" "), "");
  return lines.join("\n");
}

export function saveDraft(draft, outFile) {
  fs.mkdirSync(config.draftsDir, { recursive: true });
  const file = outFile || path.join(config.draftsDir, `${timestamp()}.json`);
  fs.writeFileSync(file, JSON.stringify(draft, null, 2));
  fs.writeFileSync(file.replace(/\.json$/, ".md"), draftToMarkdown(draft));
  return file;
}

export function parseArgs(argv) {
  const args = { positional: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--dry-run") args.dryRun = true;
    else if (a === "--draft") args.draft = true;
    else if (a === "--publish") args.publish = true;
    else if (a === "--headful") args.headful = true;
    else if (a.startsWith("--")) args[a.slice(2)] = argv[++i];
    else args.positional.push(a);
  }
  return args;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const topic = args.positional.join(" ").trim();
  if (!topic) {
    console.error('사용법: npm run generate -- "글 주제" [--notes "메모"] [--out drafts/x.json] [--dry-run]');
    process.exit(1);
  }
  const references = loadReferences();
  if (args.dryRun) {
    const { system, user } = buildPrompt(references, topic, args.notes);
    console.log("===== system =====\n" + system + "\n\n===== user =====\n" + user);
    return;
  }
  const draft = await generatePost({ topic, notes: args.notes, references });
  const file = saveDraft(draft, args.out);
  console.log(draftToMarkdown(draft));
  console.log(`\n저장: ${file} (입력 ${draft.usage.input_tokens} / 출력 ${draft.usage.output_tokens} 토큰)`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((e) => {
    console.error(e);
    process.exit(1);
  });
}
