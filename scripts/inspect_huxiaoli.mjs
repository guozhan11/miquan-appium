import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const projectDir = path.dirname(scriptDir);
const inputPath = process.argv[2] ?? path.join(projectDir, "data/sources/狐小狸剧本杀.xlsx");
const input = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(input);
const summary = await workbook.inspect({
  kind: "workbook,sheet,table",
  maxChars: 12000,
  tableMaxRows: 8,
  tableMaxCols: 8,
  tableMaxCellChars: 120,
});
console.log(summary.ndjson);

if (inputPath.includes("名称清单")) {
  const tail = await workbook.inspect({
    kind: "table",
    range: "名称清单!A2568:G2571",
    include: "values,formulas",
    tableMaxRows: 4,
    tableMaxCols: 7,
  });
  console.log(tail.ndjson);
}
