// Build the Pagefind full-text index from records.jsonl (made by prepare.py) into ../pagefind/
import * as pagefind from "pagefind";
import fs from "node:fs";
import readline from "node:readline";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const out = process.env.PF_OUT || path.join(here, "..", "pagefind");
const { index, errors } = await pagefind.createIndex({ forceLanguage: "en" });
if (errors.length) { console.error(errors); process.exit(1); }

let n = 0;
const rl = readline.createInterface({ input: fs.createReadStream(path.join(here, "records.jsonl")) });
for await (const line of rl) {
  if (!line.trim()) continue;
  const r = JSON.parse(line);
  const res = await index.addCustomRecord({ url: r.url, content: r.content, language: "en", meta: r.meta, filters: r.filters, sort: r.sort });
  if (res.errors.length) { console.error(res.errors); process.exit(1); }
  if (++n % 5000 === 0) console.log(n, "pages indexed");
}
fs.rmSync(out, { recursive: true, force: true });
const w = await index.writeFiles({ outputPath: out });
if (w.errors.length) { console.error(w.errors); process.exit(1); }
console.log(`${n} pages -> ${out}`);
await pagefind.close();
