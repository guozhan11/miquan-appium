import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const projectDir = path.dirname(scriptDir);
const outputDir = path.join(projectDir, "outputs/complete-script-list-20260918");
const outputPath = `${outputDir}/完整剧本名称总表.xlsx`;
const previewPath = `${outputDir}/完整剧本名称总表预览.png`;

const baseCsv = await fs.readFile(path.join(projectDir, "data/search_terms/search_terms.csv"), "utf8");
const huxiaoliCsv = await fs.readFile(path.join(projectDir, "data/search_terms/huxiaoli_search_terms.csv"), "utf8");
const baseWorkbook = await Workbook.fromCSV(baseCsv, { sheetName: "Base" });
const huxiaoliWorkbook = await Workbook.fromCSV(huxiaoliCsv, { sheetName: "Huxiaoli" });

const baseValues = baseWorkbook.worksheets.getItem("Base").getUsedRange(true).values;
const huxiaoliValues = huxiaoliWorkbook.worksheets.getItem("Huxiaoli").getUsedRange(true).values;
const huxiaoliHeaders = huxiaoliValues[0].map((value) => String(value ?? ""));
const queryColumn = huxiaoliHeaders.indexOf("query");

const combined = [];
const seen = new Set();
const addName = (value) => {
  const name = String(value ?? "")
    .trim()
    .replace(/^((?:单|双|[一二三四五六七八九十两百零〇0-9]+)人本)[\s\-—–_：:、·.]*/, "")
    .trim();
  if (!name || seen.has(name)) return;
  seen.add(name);
  combined.push([name]);
};

for (const row of baseValues.slice(1)) addName(row[0]);
for (const row of huxiaoliValues.slice(1)) addName(row[queryColumn]);

const workbook = Workbook.create();
const sheet = workbook.worksheets.add("剧本名称");
sheet.getRange("A1").values = [["剧本名称"]];
sheet.getRange("A2").write(combined);
workbook.recalculate();

const check = await workbook.inspect({
  kind: "table",
  range: "剧本名称!A1:A12",
  include: "values,formulas",
  tableMaxRows: 12,
  tableMaxCols: 1,
});
console.log(check.ndjson);

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);

await fs.mkdir(outputDir, { recursive: true });
const preview = await workbook.render({ sheetName: "剧本名称", range: "A1:A30", scale: 1.5, format: "png" });
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(JSON.stringify({ outputPath, previewPath, rowCount: combined.length }));
