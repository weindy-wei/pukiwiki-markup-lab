import {
  parseSnapshot, demoSnapshot, display, isEmpty, preferredColumns,
  rowLabel, rowSubtitle, filterRecords, sheetSummary, recordDetail, getValue
} from "./model.js";
import { readLocalSnapshot } from "./api-client.js";

const $ = (id) => document.getElementById(id);
const MAX_FILE_BYTES = 20 * 1024 * 1024;
const state = {
  snapshot: parseSnapshot(demoSnapshot),
  sheetIndex: 0,
  mode: "grid",
  query: "",
  status: "",
  columns: [],
  selectedId: null,
  page: 0,
  columnsOpen: false,
  imported: false,
  apiConnected: false
};
const PER_PAGE = { grid: 50, thumb: 24, detail: 1 };

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}
function clear(node) { node.replaceChildren(); }
function button(label, className, handler) {
  const node = element("button", className, label);
  node.type = "button";
  node.addEventListener("click", handler);
  return node;
}
function sheet() { return state.snapshot.sheets[state.sheetIndex]; }
function sheetAssets() { return state.snapshot.asset_references ?? []; }
function hasField(name) { return sheet().fields.some((f) => f.name === name); }
function statusField() {
  return ["状态", "制作状态", "制作阶段"].find(hasField) ?? null;
}
function currentRecords() {
  let rows = filterRecords(sheet(), state.query);
  const field = statusField();
  if (field && state.status) rows = rows.filter((r) => display(getValue(sheet(), r, field)) === state.status);
  return rows;
}
function initializeSheet(index) {
  state.sheetIndex = index;
  state.query = "";
  state.status = "";
  state.page = 0;
  state.columnsOpen = false;
  state.columns = preferredColumns(sheet()).map((f) => f.index);
  state.selectedId = sheet().records[0]?.record_id ?? null;
  $("search").value = "";
  renderAll();
}
function setMessage(text = "") { $("message").textContent = text; }
function activate(mode) {
  state.mode = mode;
  state.page = 0;
  const rows = currentRecords();
  if (mode === "detail" && rows.length && !rows.some((r) => r.record_id === state.selectedId)) {
    state.selectedId = rows[0].record_id;
  }
  renderAll();
}
function openDetail(recordId) {
  state.selectedId = recordId;
  activate("detail");
}

function renderNav() {
  const nav = $("sheet-nav");
  clear(nav);
  state.snapshot.sheets.forEach((s, index) => {
    const link = button("", "fw-sheet-link" + (index === state.sheetIndex ? " is-current" : ""), () => initializeSheet(index));
    link.setAttribute("aria-current", index === state.sheetIndex ? "page" : "false");
    link.append(
      element("span", "fw-sheet-icon", s.name.startsWith("第") ? "▧" : "▤"),
      element("span", "", s.name),
      element("span", "fw-sheet-count", s.records.length)
    );
    nav.append(link);
  });
}
function renderHeader() {
  const s = sheet();
  $("breadcrumb-sheet").textContent = s.name;
  $("sheet-title").textContent = s.name;
  $("board-title").textContent = s.name.startsWith("第") ? "镜头记录" : s.name + " · 数据记录";
  $("sheet-description").textContent = s.records.length
    ? "原始信息与资产关系的只读工作台 · 点击任意记录查看完整字段"
    : "此工作表暂无数据记录，原始字段结构已经保留。";
  const source = state.snapshot.source;
  $("project-name").textContent = "FilmWiki 项目";
  $("project-source").textContent = state.apiConnected ? "NAS 私有数据 · 只读" : (state.imported ? "已载入本地 JSON" : "示例工作空间");
  $("filename").textContent = source.filename ?? "本地导出";
  $("source-mode").textContent = state.apiConnected ? "本地 API · 只读" : (state.imported ? "本地 JSON · 未上传" : "合成演示数据");
  const info = sheetSummary(s, sheetAssets());
  $("kpi-records").textContent = info.records;
  $("kpi-fields").textContent = info.fields;
  $("kpi-empty").textContent = info.emptyValues;
  $("kpi-review").textContent = info.unresolvedAssets + info.parentLinks.ambiguous + info.parentLinks.missing;
}
function renderFilter() {
  const select = $("status-filter");
  clear(select);
  select.append(new Option("全部状态", ""));
  const field = statusField();
  select.disabled = !field;
  if (field) {
    const choices = new Set(sheet().records.map((r) => getValue(sheet(), r, field))
      .filter((v) => !isEmpty(v)).map(display));
    [...choices].sort((a,b) => a.localeCompare(b,"zh-CN")).forEach((value) => select.append(new Option(value, value)));
  }
  select.value = state.status;
  $("column-button").disabled = state.mode !== "grid";
  $("column-button").setAttribute("aria-expanded", String(state.mode === "grid" && state.columnsOpen));
  renderColumns();
}
function renderColumns() {
  const choices = $("column-options");
  clear(choices);
  choices.hidden = !(state.columnsOpen && state.mode === "grid");
  if (choices.hidden) return;
  choices.append(element("span", "fw-column-info", "最多显示 8 列，底层数据全部保留"));
  sheet().fields.forEach((field) => {
    const label = element("label","fw-checkbox");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = state.columns.includes(field.index);
    input.disabled = !input.checked && state.columns.length >= 8;
    input.addEventListener("change", () => {
      if (!input.checked && state.columns.length <= 1) {
        setMessage("至少保留一列以便展示。");
        input.checked = true;
        return;
      }
      if (input.checked) {
        if (state.columns.length < 8) state.columns.push(field.index);
      } else {
        state.columns = state.columns.filter((v) => v !== field.index);
      }
      state.columns.sort((a,b) => a - b);
      renderColumns();
      renderContent();
    });
    label.append(input, document.createTextNode(field.name || "未命名列"));
    choices.append(label);
  });
}
function placeholder(message, subtitle) {
  const root = element("div","fw-empty");
  root.append(element("strong","",message), element("span","",subtitle));
  return root;
}
function renderGrid(rows) {
  const wrap = element("div","fw-table-wrap");
  const table = element("table","fw-data-table");
  table.setAttribute("aria-label",sheet().name + " Grid 视图");
  const head = element("thead");
  const titles = element("tr");
  titles.append(element("th","","原始行"));
  const fields = sheet().fields.filter((f) => state.columns.includes(f.index));
  fields.forEach((f) => titles.append(element("th","",f.name || "未命名列")));
  head.append(titles);
  const body = element("tbody");
  rows.forEach((record) => {
    const tr = element("tr");
    tr.tabIndex = 0;
    tr.title = "查看完整记录 · 原始行 " + record.source_row;
    const activateRow = () => openDetail(record.record_id);
    tr.addEventListener("click", activateRow);
    tr.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activateRow(); }
    });
    tr.append(element("td","fw-row-id",String(record.source_row).padStart(3,"0")));
    fields.forEach((f,index) => {
      const value=record.cells[f.index-1].value;
      const td=element("td",(index===0 ? "fw-record-name " : "")+(isEmpty(value)?"fw-empty-cell":""),display(value));
      td.title = isEmpty(value) ? "原始数据为空" : display(value);
      tr.append(td);
    });
    body.append(tr);
  });
  table.append(head,body);
  wrap.append(table);
  return wrap;
}
function renderThumb(rows) {
  const container = element("div","fw-thumbnail-grid");
  for(const record of rows) {
    const card=button("","fw-shot-card",()=>openDetail(record.record_id));
    const frame=element("div","fw-visual-placeholder");
    frame.append(
      element("span","fw-frame-index","ROW "+String(record.source_row).padStart(3,"0")),
      element("span","fw-film-icon","▧"),
      element("span","fw-frame-caption","NO VERIFIED THUMBNAIL")
    );
    const body=element("div","fw-card-body");
    body.append(element("div","fw-card-name",rowLabel(sheet(),record)),element("div","fw-card-subtitle",rowSubtitle(sheet(),record)));
    const footer=element("div","fw-card-footer");
    const assets=sheetAssets().filter((x)=>x.record_id===record.record_id);
    footer.append(
      element("span","",record.record_id.slice(0,18)+"…"),
      element("span",assets.length?"fw-missing-image":"",assets.length?"附件待关联："+assets.length:"未关联媒体")
    );
    body.append(footer);
    card.append(frame,body);
    container.append(card);
  }
  return container;
}
function section(title) {
  const root=element("section","fw-detail-section");
  root.append(element("h3","",title));
  return root;
}
function renderDetail(rows) {
  const wrap=element("div","fw-detail-wrap");
  const record=rows.find((r)=>r.record_id===state.selectedId) || rows[0];
  if(!record){wrap.append(placeholder("暂无可查看的记录","更换搜索条件或选择其他工作表。"));return wrap;}
  state.selectedId=record.record_id;
  const detail=recordDetail(sheet(),record,sheetAssets());
  const panel=element("article","fw-detail-panel");
  const hero=element("div","fw-detail-hero");
  hero.append(element("div","fw-detail-title",detail.label),element("div","fw-detail-subtitle",detail.subtitle));
  const tags=element("div","fw-detail-tags");
  tags.append(element("span","badge bg-azure-lt","SOURCE ROW "+detail.sourceRow));
  if(detail.parent){tags.append(element("span","badge bg-yellow-lt","父镜头："+detail.parent.status));}
  if(detail.assets.length){tags.append(element("span","badge bg-orange-lt",detail.assets.length+" 个附件引用"));}
  const index=rows.indexOf(record);
  const prev=button("← 上一条","btn btn-outline-secondary btn-sm",()=>stepDetail(rows,-1));
  prev.disabled=index===0;
  const next=button("下一条 →","btn btn-outline-secondary btn-sm",()=>stepDetail(rows,1));
  next.disabled=index===rows.length-1;
  tags.append(prev,element("span","fw-row-id",(index+1)+"/"+rows.length),next);
  hero.append(tags);
  panel.append(hero);
  const info=section("ORIGINAL FIELDS · 完整字段");
  const properties=element("div","fw-properties");
  detail.fields.forEach((f)=>{
    properties.append(
      element("div","fw-property-label",String(f.index).padStart(2,"0")+" · "+(f.name||"未命名列")),
      element("div","fw-property-value"+(isEmpty(f.value)?" fw-null":""),display(f.value))
    );
  });
  info.append(properties);
  panel.append(info);
  if(detail.parent) {
    const parent=section("PARENT SHOT · 父镜头关系");
    const msg=detail.parent.status==="ambiguous" ?
      "父镜头标签存在歧义，候选记录 "+detail.parent.candidate_record_ids.join("、")+"；尚未自动绑定。" :
      detail.parent.status==="missing" ? "未找到父镜头 "+detail.parent.source_label+"；关系暂存为缺失。" :
      "父镜头已确定："+detail.parent.parent_record_id;
    parent.append(element("div","fw-parent-alert",msg));
    panel.append(parent);
  }
  if(detail.assets.length) {
    const asset=section("ASSETS · 未匹配附件");
    detail.assets.forEach((ref)=>asset.append(element("div","fw-asset-line",ref.filename+" · "+ref.status+" · 未推断图片内容")));
    panel.append(asset);
  }
  wrap.append(panel);
  return wrap;
}
function stepDetail(rows,delta) {
  const index=rows.findIndex((r)=>r.record_id===state.selectedId);
  const next=rows[index+delta];
  if(next){state.selectedId=next.record_id;renderContent();}
}
function renderPager(total,pageSize) {
  const pages=Math.ceil(total/pageSize);
  const pager=element("div","fw-pager");
  pager.style.display="flex";
  pager.style.alignItems="center";
  pager.style.justifyContent="end";
  pager.style.gap="10px";
  pager.style.padding="12px 25px";
  const previous=button("上一页","btn btn-outline-secondary btn-sm",()=>{state.page--;renderContent();});
  previous.disabled=state.page<=0;
  const next=button("下一页","btn btn-outline-secondary btn-sm",()=>{state.page++;renderContent();});
  next.disabled=state.page>=pages-1;
  pager.append(previous,element("span","fw-row-id",(state.page+1)+" / "+pages),next);
  return pager;
}
function renderContent() {
  const root=$("view-content");clear(root);
  const rows=currentRecords();
  const count=rows.length;
  $("result-count").textContent=count+" records";
  $("footer-count").textContent="已匹配 "+count+" / "+sheet().records.length+" 条 · 来源："+(state.snapshot.source.filename??"本地");
  for(const mode of ["grid","thumb","detail"]){
    const b=$("view-"+mode);
    b.classList.toggle("is-active",mode===state.mode);
    b.setAttribute("aria-pressed",String(mode===state.mode));
  }
  $("column-button").disabled=state.mode!=="grid";
  renderColumns();
  if(!count){
    root.append(placeholder(sheet().records.length?"没有匹配的记录":"这是一个只有字段结构的空表","原始数据没有被删除或修改。"));
    return;
  }
  const limit=PER_PAGE[state.mode];
  const pages=Math.ceil(count/limit);
  state.page=Math.min(state.page,Math.max(pages-1,0));
  const pageRows=state.mode==="detail"? rows : rows.slice(state.page*limit,(state.page+1)*limit);
  root.append(state.mode==="grid" ? renderGrid(pageRows) : state.mode==="thumb" ? renderThumb(pageRows) : renderDetail(pageRows));
  if(state.mode!=="detail" && count>limit)root.append(renderPager(count,limit));
}
function renderAll() {
  renderNav();renderHeader();renderFilter();renderContent();
}
async function importFile(file) {
  if(!file) return;
  if(file.size>MAX_FILE_BYTES){setMessage("文件超过 20 MB。请先在本机按工作表拆分或使用后端导出。");return;}
  try{
    const text=await file.text();
    const parsed=parseSnapshot(JSON.parse(text));
    state.snapshot=parsed;
    state.imported=true;
    state.apiConnected=false;
    state.mode="grid";
    initializeSheet(0);
    setMessage("");
    $("import-json").value="";
  }catch(error){
    setMessage("导入失败："+(error instanceof Error?error.message:"无效 JSON"));
  }
}
$("api-toggle").addEventListener("click", () => {
  const panel=$("api-panel");
  panel.hidden=!panel.hidden;
  $("api-toggle").setAttribute("aria-expanded",String(!panel.hidden));
  if(!panel.hidden) $("api-token").focus();
});
$("api-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const token=$("api-token").value.trim();
  const submit=$("api-connect-now");
  submit.disabled=true;
  $("api-status").textContent="正在从本地私有数据库读取记录…";
  try {
    const snapshot=await readLocalSnapshot(token);
    state.snapshot=snapshot;
    state.imported=true;
    state.apiConnected=true;
    state.mode="grid";
    initializeSheet(0);
    $("api-token").value="";
    $("api-panel").hidden=true;
    $("api-toggle").setAttribute("aria-expanded","false");
    $("api-status").textContent="连接成功；全部记录只读。";
    setMessage("");
  } catch(error) {
    const message=error instanceof Error?error.message:"本地 API 连接失败";
    $("api-status").textContent=message;
    setMessage("连接未完成："+message+"。现有视图未被替换。");
  } finally {
    submit.disabled=false;
  }
});

$("import-json").addEventListener("change", (event)=>importFile(event.target.files?.[0]));
$("search").addEventListener("input",(event)=>{
  state.query=event.target.value;state.page=0;renderContent();
});
$("status-filter").addEventListener("change",(event)=>{
  state.status=event.target.value;state.page=0;renderContent();
});
$("column-button").addEventListener("click",()=>{
  state.columnsOpen=!state.columnsOpen;renderFilter();
});
["grid","thumb","detail"].forEach((mode)=>{
  $("view-"+mode).addEventListener("click",()=>activate(mode));
});
initializeSheet(0);
