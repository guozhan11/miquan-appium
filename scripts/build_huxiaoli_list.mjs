import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const projectDir = path.dirname(scriptDir);
const sourceCsv = path.join(projectDir, "data/search_terms/huxiaoli_search_terms.csv");
const outputDir = path.join(projectDir, "outputs/huxiaoli-script-list-20260918");
const outputPath = `${outputDir}/狐小狸剧本名称清单.xlsx`;
const previewPath = `${outputDir}/狐小狸剧本名称清单预览.png`;

const csvText = await fs.readFile(sourceCsv, "utf8");
const workbook = await Workbook.fromCSV(csvText, { sheetName: "狐小狸剧本名称" });
const sheet = workbook.worksheets.getItem("狐小狸剧本名称");
const sourceValues = sheet.getUsedRange(true).values;
const sourceHeaders = sourceValues[0].map((value) => String(value ?? ""));
const sourceRows = sourceValues.slice(1);

const index = Object.fromEntries(sourceHeaders.map((header, column) => [header, column]));
const rows = sourceRows.map((row, i) => {
  const peopleText = String(row[index.people_count] ?? "").trim();
  const sourceRowText = String(row[index.row] ?? "").trim();
  return [
    i + 1,
    String(row[index.query] ?? ""),
    String(row[index.original_name] ?? ""),
    /^\d+$/.test(peopleText) ? Number(peopleText) : peopleText,
    String(row[index.sheet] ?? ""),
    /^\d+$/.test(sourceRowText) ? Number(sourceRowText) : sourceRowText,
    String(row[index.source_link] ?? ""),
  ];
});

sheet.getUsedRange().clear({ applyTo: "all" });
sheet.name = "名称清单";
sheet.showGridLines = false;
sheet.tabColor = "#1F4E78";

sheet.getRange("A2:G2").format.rowHeight = 26;
sheet.getRange("A2").values = [["狐小狸剧本名称清单"]];
sheet.getRange("A2").format.font = { name: "Arial", size: 15, bold: true, color: "#1F1F1F" };
sheet.getRange("A2:G2").format.borders = { bottom: { style: "thin", color: "#1F4E78" } };

sheet.getRange("A3").values = [["来源：狐小狸剧本杀.xlsx；清理结果与 huxiaoli_search_terms.csv 完全一致。"]];
sheet.getRange("A4").values = [[`共 ${rows.length.toLocaleString("zh-CN")} 个去重名称。名称已移除括号或方括号中的人数、单手册等说明，并去掉常见人数和题材尾缀；原始名称保留供核对。`]];
sheet.getRange("A3:G4").format.font = { name: "Arial", size: 10, italic: true, color: "#666666" };
sheet.getRange("A3:G4").format.rowHeight = 19;

const headers = [["序号", "剧本名称（已清理）", "原始名称", "人数", "来源工作表", "原始行号", "百度网盘链接"]];
sheet.getRange("A6:G6").values = headers;
sheet.getRange("A7").write(rows);

const lastRow = 6 + rows.length;
const table = sheet.tables.add(`A6:G${lastRow}`, true, "HuxiaoliScriptList");
table.style = "TableStyleMedium2";
table.showBandedRows = true;
table.showFilterButton = true;

sheet.getRange(`A6:G${lastRow}`).format.font = { name: "Arial", size: 10, color: "#1F1F1F" };
sheet.getRange("A6:G6").format = {
  fill: "#1F4E78",
  font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
  borders: { preset: "inside", style: "thin", color: "#FFFFFF" },
};
sheet.getRange("A6:G6").format.rowHeight = 30;
sheet.getRange(`A7:A${lastRow}`).format.horizontalAlignment = "right";
sheet.getRange(`D7:F${lastRow}`).format.horizontalAlignment = "center";
sheet.getRange(`A7:G${lastRow}`).format.verticalAlignment = "center";
sheet.getRange(`A7:A${lastRow}`).format.numberFormat = "#,##0";
sheet.getRange(`D7:F${lastRow}`).format.numberFormat = "0";
sheet.getRange(`A7:G${lastRow}`).format.rowHeight = 19;

sheet.getRange(`A1:A${lastRow}`).format.columnWidth = 8;
sheet.getRange(`B1:B${lastRow}`).format.columnWidth = 28;
sheet.getRange(`C1:C${lastRow}`).format.columnWidth = 42;
sheet.getRange(`D1:D${lastRow}`).format.columnWidth = 9;
sheet.getRange(`E1:E${lastRow}`).format.columnWidth = 25;
sheet.getRange(`F1:F${lastRow}`).format.columnWidth = 11;
sheet.getRange(`G1:G${lastRow}`).format.columnWidth = 48;

sheet.freezePanes.freezeRows(6);
sheet.freezePanes.freezeColumns(2);

workbook.recalculate();

const check = await workbook.inspect({
  kind: "table",
  range: `名称清单!A1:G15`,
  include: "values,formulas",
  tableMaxRows: 15,
  tableMaxCols: 7,
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
const preview = await workbook.render({ sheetName: "名称清单", range: "A1:G24", scale: 1.5, format: "png" });
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(JSON.stringify({ outputPath, previewPath, rowCount: rows.length }));
