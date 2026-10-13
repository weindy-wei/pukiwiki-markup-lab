/* FilmWiki Stage 24: private read-only API client. Never persist credentials. */
import { parseSnapshot } from "./model.js";

export const LOCAL_API = "http://127.0.0.1:8091";
const MAX_ROWS = 5000;
const PAGE_SIZE = 200;
const MAX_FETCHES = 250;

async function request(fetcher, endpoint, token) {
  const response = await fetcher(LOCAL_API + endpoint, {
    method: "GET",
    mode: "cors",
    cache: "no-store",
    credentials: "omit",
    redirect: "error",
    referrerPolicy: "no-referrer",
    headers: { Authorization: "Bearer " + token }
  });
  if (!response.ok) {
    if (response.status === 401) throw new Error("访问令牌无效，请检查本地令牌文件。");
    if (response.status === 403) throw new Error("当前页面来源未获 API 授权。");
    throw new Error("本地数据接口错误：" + response.status);
  }
  return response.json();
}

export async function readLocalSnapshot(token, fetcher = fetch) {
  if (typeof token !== "string" || token.length < 40 || token.length > 200) {
    throw new Error("请输入有效的本地访问令牌。");
  }
  const manifest = await request(fetcher, "/v1/manifest", token);
  if (manifest?.format !== "filmwiki-read-api-manifest" || manifest.version !== 1 ||
      !Array.isArray(manifest.sheets) || manifest.sheets.length > 100) {
    throw new Error("本地接口版本或数据结构不兼容。");
  }
  let total = 0, fetchCount = 1;
  for (const sheet of manifest.sheets) {
    if (!Number.isSafeInteger(sheet.record_count) || sheet.record_count < 0 ||
        !Number.isSafeInteger(sheet.sheet_index) || !Array.isArray(sheet.fields)) {
      throw new Error("工作表清单无效。");
    }
    total += sheet.record_count;
  }
  if (total > MAX_ROWS) {
    throw new Error("当前仅支持最多 " + MAX_ROWS +
      " 条记录的本地预览。更大数据集将在下一阶段采用服务器分页。");
  }
  const assets = [];
  const sheets = [];
  for (const sheet of manifest.sheets) {
    const records = [];
    let offset = 0;
    while (offset < sheet.record_count) {
      if (++fetchCount > MAX_FETCHES) throw new Error("本地数据请求次数超出保护阈值。");
      const page = await request(fetcher,
        "/v1/records?sheet=" + sheet.sheet_index + "&offset=" + offset + "&limit=" + PAGE_SIZE, token);
      if (page?.sheet_index !== sheet.sheet_index || page.total !== sheet.record_count ||
          page.offset !== offset || !Array.isArray(page.records) ||
          !Array.isArray(page.asset_references) || page.records.length === 0 ||
          page.records.length > PAGE_SIZE) {
        throw new Error("服务端分页数据不一致，已停止载入。");
      }
      records.push(...page.records);
      assets.push(...page.asset_references);
      offset += page.records.length;
      const expected = offset < sheet.record_count ? offset : null;
      if (page.next_offset !== expected) {
        throw new Error("服务端分页游标错误，已停止载入。");
      }
    }
    sheets.push({ name: sheet.name, fields: sheet.fields, records });
  }
  // Validation finishes BEFORE returning state; a failed request does not
  // replace the previous file/demo view or leak a partially loaded workbook.
  return parseSnapshot({
    format: "filmwiki-portable-export",
    version: 1,
    source: manifest.source,
    sheets,
    asset_references: assets,
    note: "Loaded from private loopback API; no remote cloud requests."
  });
}
