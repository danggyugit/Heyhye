import readline from "node:readline";

export function waitForEnter(prompt) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => rl.question(`${prompt}\n`, () => { rl.close(); resolve(); }));
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
