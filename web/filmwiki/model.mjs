/* FilmWiki read-only view model. No network, storage or DOM access. */
export const FORMAT = "filmwiki-portable-export";
const MAX_SHEETS = 100;
const MAX_RECORDS = 200000;
const MAX_FIELDS = 256;
const ALLOWED_STATUS = new Set(["resolved", "ambiguous", "missing"]);

const asString = (value) => value == null ? "" : String(value);
export const display = (value) => {
  if (value == null || value === "") return "—";
  if (Array.isArray(value) || (typeof value === "object")) return JSON.stringify(value);
  return String(value);
};
export const isEmpty = (value) => value === null || value === undefined || value === "";

function assert(condition, message) {
  if (!condition) throw new Error(message);
}
function uniqueRecords(value) {
  return value && typeof value === "object" && !Array.isArray(value);
}
function validateCells(fields, cells, location) {
  assert(Array.isArray(cells) && cells.length === fields.length,
    "记录字段数不一致：" + location);
  cells.forEach((cell, index) => {
    assert(uniqueRecords(cell), "无效单元格：" + location);
    assert(cell.index === index + 1 && cell.field === fields[index].name,
      "字段位置不匹配：" + location);
  });
}

export function parseSnapshot(raw) {
  assert(uniqueRecords(raw) && raw.format === FORMAT && raw.version === 1,
    "文件不是受支持的 FilmWiki 导出格式（版本 1）");
  assert(uniqueRecords(raw.source) && typeof raw.source.sha256 === "string" &&
    /^[a-f0-9]{64}$/.test(raw.source.sha256), "缺少来源 SHA-256");
  assert(Array.isArray(raw.sheets) && raw.sheets.length <= MAX_SHEETS,
    "工作表数量无效");
  assert(Array.isArray(raw.asset_references ?? []),
    "附件引用格式无效");
  const globalIds = new Set();
  let total = 0;
  raw.sheets.forEach((sheet, si) => {
    assert(uniqueRecords(sheet) && typeof sheet.name === "string" &&
      Array.isArray(sheet.fields) && Array.isArray(sheet.records),
      "无效工作表：" + (si + 1));
    assert(sheet.fields.length <= MAX_FIELDS, "字段数量超限");
    assert(sheet.records.length + total <= MAX_RECORDS, "记录总数超限");
    total += sheet.records.length;
    const rows = new Set();
    sheet.fields.forEach((field, i) => {
      assert(uniqueRecords(field) && field.index === i + 1 &&
        typeof field.name === "string", "无效字段：" + sheet.name);
    });
    sheet.records.forEach((record) => {
      assert(uniqueRecords(record) && typeof record.record_id === "string" &&
        /^src_[a-z0-9_-]+$/i.test(record.record_id) &&
        !globalIds.has(record.record_id), "重复或无效记录 ID");
      assert(Number.isSafeInteger(record.source_row) &&
        record.source_row >= 2 && !rows.has(record.source_row),
        "重复或无效原始行");
      globalIds.add(record.record_id);
      rows.add(record.source_row);
      validateCells(sheet.fields, record.cells, record.record_id);
      if (record.parent_link != null) {
        assert(uniqueRecords(record.parent_link) &&
          ALLOWED_STATUS.has(record.parent_link.status) &&
          Array.isArray(record.parent_link.candidate_record_ids),
          "无效父镜头关系");
      }
    });
  });
  raw.asset_references.forEach((asset) => {
    assert(uniqueRecords(asset) && globalIds.has(asset.record_id) &&
      typeof asset.filename === "string" && typeof asset.status === "string",
      "无法关联的附件引用");
  });
  return raw;
}

export function getValue(sheet, record, fieldName) {
  const i = sheet.fields.findIndex((x) => x.name === fieldName);
  return i < 0 ? null : record.cells[i].value;
}

export function preferredColumns(sheet, limit = 8) {
  const priorities = [
    "镜头号", "镜头编号", "镜头ID", "镜头内容", "场景", "景别", "运镜",
    "时长（秒）", "时长秒", "状态", "父记录", "分镜", "角色名称", "角色类型",
    "场景名称", "项目名称", "内容"
  ];
  const found = [];
  for (const label of priorities) {
    const field = sheet.fields.find((f) => f.name === label);
    if (field && !found.some((f) => f.index === field.index)) found.push(field);
    if (found.length === limit) return found;
  }
  for (const field of sheet.fields) {
    if (!found.some((x) => x.index === field.index)) found.push(field);
    if (found.length === limit) break;
  }
  return found;
}

export function rowLabel(sheet, record) {
  for (const fieldName of ["镜头号", "镜头编号", "镜头ID", "角色名称", "场景名称", "道具名称", "项目名称"]) {
    const v = getValue(sheet, record, fieldName);
    if (!isEmpty(v)) return asString(v);
  }
  return "记录 #" + record.source_row;
}

export function rowSubtitle(sheet, record) {
  for (const fieldName of ["镜头内容", "内容", "角色类型", "场景", "景别"]) {
    const v = getValue(sheet, record, fieldName);
    if (!isEmpty(v)) return display(v);
  }
  return "原表第 " + record.source_row + " 行";
}

export function filterRecords(sheet, query) {
  const q = query.trim().toLocaleLowerCase();
  if (!q) return [...sheet.records];
  return sheet.records.filter((record) =>
    record.cells.some(({ value }) => !isEmpty(value) && display(value).toLocaleLowerCase().includes(q)) ||
    rowLabel(sheet, record).toLocaleLowerCase().includes(q) ||
    record.record_id.toLocaleLowerCase().includes(q)
  );
}

export function linksSummary(sheet) {
  const stats = { resolved: 0, ambiguous: 0, missing: 0 };
  sheet.records.forEach((record) => {
    const status = record.parent_link?.status;
    if (status && Object.prototype.hasOwnProperty.call(stats, status)) stats[status]++;
  });
  return stats;
}

export function sheetSummary(sheet, assets = []) {
  const rowIds = new Set(sheet.records.map((row) => row.record_id));
  const referencedAssets = assets.filter((asset) => rowIds.has(asset.record_id));
  return {
    fields: sheet.fields.length,
    records: sheet.records.length,
    emptyValues: sheet.records.reduce((n, record) =>
      n + record.cells.filter(({ value }) => isEmpty(value)).length, 0),
    unresolvedAssets: referencedAssets.filter((asset) => asset.status !== "resolved").length,
    parentLinks: linksSummary(sheet)
  };
}

export function recordDetail(sheet, record, assets = []) {
  return {
    label: rowLabel(sheet, record),
    subtitle: rowSubtitle(sheet, record),
    recordId: record.record_id,
    sourceRow: record.source_row,
    parent: record.parent_link ?? null,
    fields: sheet.fields.map((field, index) =>
      ({ name: field.name, index: field.index, value: record.cells[index].value })),
    assets: assets.filter((asset) => asset.record_id === record.record_id)
  };
}

export const demoSnapshot = {
  format: FORMAT, version: 1,
  source: { filename: "FilmWiki 合成演示数据（非真实项目）", sha256: "d".repeat(64) },
  sheets: [
    {
      name: "第1集",
      fields: [
        "镜头号", "分镜", "镜头视频", "场景", "出场角色", "景别", "拍摄角度",
        "运镜", "镜头内容", "角色动作和表演", "台词和声音", "时长（秒）",
        "转场方式", "生图提示词", "视频提示词", "备注", "父记录"
      ].map((name, i) => ({ index: i + 1, name })),
      records: [
        ["SC_01", "开场", "街头", "远景", "横移", "6", null, "未开始"],
        ["SC_02", "环境建立", "广场", "全景", "推进", "8", null, "制作中"],
        ["SC_03", "人物进入画面", "广场", "中景", "跟拍", "12-18 秒", null, "待审核"],
        ["SC_04", "跟随角色穿越人群", "街头", "近景", "手持", "7", null, "已完成"],
        ["SC_06", "角色回望", "街头", "特写", "固定", "4", null, "制作中"],
        ["SC_06", "另一机位拍摄", "街头", "中景", "环绕", "6", null, "未开始"],
        ["SC_06_01", "眼神细节", "街头", "特写", "固定", "3", "SC_06", "待审核"]
      ].map((entry, i) => {
        const vals = Array(17).fill(null);
        vals[0] = entry[0]; vals[3] = entry[2]; vals[5] = entry[3];
        vals[7] = entry[4]; vals[8] = entry[1]; vals[11] = entry[5];
        vals[15] = "演示状态：" + entry[7]; vals[16] = entry[6];
        return {
          record_id: "src_demo_" + String(i + 1).padStart(3, "0"),
          source_row: i + 2, shot_number: entry[0],
          cells: vals.map((value, j) => ({ index: j + 1, field: [
            "镜头号", "分镜", "镜头视频", "场景", "出场角色", "景别", "拍摄角度",
            "运镜", "镜头内容", "角色动作和表演", "台词和声音", "时长（秒）",
            "转场方式", "生图提示词", "视频提示词", "备注", "父记录"
          ][j], value })),
          parent_link: entry[6] ? {
            source_label: entry[6], status: "ambiguous", parent_record_id: null,
            candidate_record_ids: ["src_demo_005", "src_demo_006"]
          } : null
        };
      })
    },
    {
      name: "人物小传",
      fields: ["角色名称", "角色类型", "人物小传"].map((name, i) => ({ index: i + 1, name })),
      records: [["主角A", "主要角色", "坚持自己的选择"], ["主角B", "主要角色", "寻找失落的线索"]]
        .map((items, i) => ({
          record_id: "src_demo_character_" + i, source_row: i + 2,
          cells: items.map((value, j) => ({ index: j + 1, field: ["角色名称", "角色类型", "人物小传"][j], value })),
          parent_link: null
        }))
    },
    {
      name: "道具设定",
      fields: ["道具名称", "道具资产"].map((name, i) => ({ index: i + 1, name })),
      records: []
    }
  ],
  asset_references: [
    { ref_id: "ref_demo_1", record_id: "src_demo_002", filename: "image.png", source_column: 2, status: "unresolved" },
    { ref_id: "ref_demo_2", record_id: "src_demo_006", filename: "image.png", source_column: 2, status: "unresolved" }
  ]
};
