// Check the built site: every asset, data file and download referenced by
// index.html or the JS bundle exists in dist/, and all paths use the /careo/ base.
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

const dist = process.argv[2] ?? "dist";
const base = "/careo/";
const problems = [];
const html = readFileSync(join(dist, "index.html"), "utf8");
for (const m of html.matchAll(/(?:src|href)="([^"]+)"/g)) {
  const url = m[1];
  if (/^(https?:|#|data:)/.test(url)) continue;
  if (!url.startsWith(base)) problems.push(`index.html: ${url} does not use base ${base}`);
  else if (!existsSync(join(dist, url.slice(base.length)))) problems.push(`index.html: missing ${url}`);
}
const js = readdirSync(join(dist, "assets")).filter((f) => f.endsWith(".js"));
const bundle = js.map((f) => readFileSync(join(dist, "assets", f), "utf8")).join("\n");
const refs = new Set();
for (const m of bundle.matchAll(/["'`](data\/[\w.-]+\.json|downloads\/)["'`]/g)) refs.add(m[1]);
for (const m of bundle.matchAll(/["'`]((?:careo-|test_careo)[\w.-]+\.(?:json|jsonl|py))["'`]/g)) refs.add(`downloads/${m[1]}`);
for (const name of ["demo.json", "benchmark.json"]) refs.add(`data/${name}`);
for (const r of refs) {
  if (r.endsWith("/")) continue;
  if (!existsSync(join(dist, r))) problems.push(`bundle references missing file ${r}`);
}
if (problems.length) {
  console.error(problems.join("\n"));
  process.exit(1);
}
console.log(`OK: ${refs.size} referenced files present, base path ${base}`);
