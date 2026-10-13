import { ProjectClient } from "./v2-client.js";
import { preferredColumns, rowLabel, rowSubtitle, display, isEmpty, recordDetail } from "./model.js";

const $=(id)=>document.getElementById(id);
const client=new ProjectClient();
const state={
  projects:[],projectId:null,manifest:null,sheetIndex:1,meta:null,
  page:null,pageNum:1,cursor:null,cursorHistory:[],
  query:{q:"",field:null,value:null},mode:"grid",
  selectedId:null,detail:null,generation:0,connected:false
};
const limits={grid:50,thumb:24,detail:50};

function elem(tag,cls,text){
  const node=document.createElement(tag);
  if(cls)node.className=cls;
  if(text!==undefined && text!==null)node.textContent=String(text);
  return node;
}
function control(label,cls,callback){
  const b=elem("button",cls,label);b.type="button";b.addEventListener("click",callback);return b;
}
function setStatus(message){$("message").textContent=message||"";}
function setLoading(loading){$("connect-button").disabled=loading;for(const id of ["previous-page","next-page"])$(id).disabled=loading;}
function sheet(){return state.meta;}
function visibleRows(){return state.page?.records??[];}
function sheetShape(){return {name:state.meta?.name??"",fields:state.meta?.fields??[]};}
function metaField(){return state.meta?.fields??[];}
function isActive(token){return state.generation===token;}

function selectedProject(){
  return state.projects.find(x=>x.id===state.projectId);
}
function updateProjectSelect(){
  const node=$("project-select");node.replaceChildren();
  state.projects.forEach(p=>node.append(new Option(p.title,p.id)));
  node.value=state.projectId??"";
  node.disabled=!state.connected;
}
function updateNav(){
  const nav=$("sheet-nav");nav.replaceChildren();
  for(const s of state.manifest?.sheets??[]){
    const b=control("","fw-sheet-link"+(s.sheet_index===state.sheetIndex?" is-current":""),
      ()=>switchSheet(s.sheet_index));
    b.append(elem("span","fw-sheet-icon","▤"),elem("span","",s.name),
             elem("span","fw-sheet-count",s.record_count));
    b.setAttribute("aria-current",s.sheet_index===state.sheetIndex?"page":"false");
    nav.append(b);
  }
}
function updateFieldPicker(){
  const picker=$("filter-field");picker.replaceChildren(new Option("全部字段",""));
  for(const f of metaField()){
    const op=new Option(f.name,f.index);
    picker.append(op);
  }
  picker.value=state.query.field??"";
  $("filter-value").disabled=!picker.value;
  $("filter-value").value=state.query.value??"";
  $("search").value=state.query.q;
}
function updateHeader(){
  const s=sheet();
  $("sheet-title").textContent=s?.name??"等待连接";
  $("breadcrumbs").textContent=(selectedProject()?.title??"多项目查询")+(s?" / "+s.name:"");
  $("board-title").textContent=s?(s.name+" · 服务端记录查询"):"记录查询";
  $("source-name").textContent=state.manifest?.source?.filename??"未连接";
  $("kpi-projects").textContent=state.projects.length;
  $("kpi-records").textContent=s?.record_count??"—";
  $("kpi-fields").textContent=s?.fields?.length??0;
  $("kpi-loaded").textContent=visibleRows().length;
  $("result-count").textContent=visibleRows().length+" / page";
  $("footer-count").textContent=state.connected?
    "第 "+state.pageNum+" 页，本页 "+visibleRows().length+" 条 · 游标分页，不全量加载":"等待连接";
  $("page-info").textContent="第 "+state.pageNum+" 页";
  $("previous-page").disabled=!state.connected||state.cursorHistory.length===0;
  $("next-page").disabled=!state.connected||!state.page?.has_more;
  for(const mode of ["grid","thumb","detail"]){
    const b=$("view-"+mode);
    b.classList.toggle("is-active",state.mode===mode);
    b.setAttribute("aria-pressed",String(state.mode===mode));
  }
}
function empty(text){
  const div=elem("div","fw-empty");
  div.append(elem("strong","",text),elem("span","","请检查项目、工作表和筛选条件。"));
  return div;
}
function appendGrid(root){
  const wrap=elem("div","fw-table-wrap");
  const table=elem("table","fw-data-table live-data-table");
  const thead=elem("thead");
  const h=elem("tr");
  const cols=preferredColumns(sheetShape());
  h.append(elem("th","","原始行"));
  cols.forEach(x=>h.append(elem("th","",x.name)));
  thead.append(h);
  const tbody=elem("tbody");
  for(const record of visibleRows()){
    const tr=elem("tr");tr.setAttribute("role","button");tr.tabIndex=0;
    const pick=()=>openDetail(record.record_id);
    tr.addEventListener("click",pick);
    tr.addEventListener("keydown",(e)=>{
      if(e.key==="Enter"||e.key===" "){e.preventDefault();pick();}
    });
    tr.append(elem("td","fw-row-id",String(record.source_row).padStart(4,"0")));
    for(const f of cols){
      const value=record.cells[f.index-1].value;
      tr.append(elem("td",isEmpty(value)?"fw-empty-cell":"",display(value)));
    }
    tbody.append(tr);
  }
  table.append(thead,tbody);wrap.append(table);root.append(wrap);
}
function appendThumb(root){
  const container=elem("div","fw-thumbnail-grid");
  for(const record of visibleRows()){
    const card=control("","fw-shot-card",()=>openDetail(record.record_id));
    const frame=elem("div","fw-visual-placeholder");
    frame.append(elem("span","fw-frame-index","ROW "+record.source_row),
                 elem("span","fw-film-icon","▧"),
                 elem("span","fw-frame-caption","NO VERIFIED MEDIA"));
    const body=elem("div","fw-card-body");
    body.append(elem("div","fw-card-name",rowLabel(sheetShape(),record)),
                elem("div","fw-card-subtitle",rowSubtitle(sheetShape(),record)));
    const assetCount=(state.page?.asset_references??[]).filter(a=>a.record_id===record.record_id).length;
    body.append(elem("div","fw-card-footer",assetCount?"附件待匹配 "+assetCount:"无已验证媒体"));
    card.append(frame,body);container.append(card);
  }
  root.append(container);
}
function detailView(record,assets){
  const d=recordDetail(sheetShape(),record,assets);
  const wrap=elem("div","fw-detail-wrap");
  const panel=elem("article","fw-detail-panel");
  const hero=elem("div","fw-detail-hero");
  hero.append(elem("div","fw-detail-title",d.label),
              elem("div","fw-detail-subtitle",d.subtitle+" · 原始第 "+d.sourceRow+" 行"));
  panel.append(hero);
  const section=elem("section","fw-detail-section");
  section.append(elem("h3","","ORIGINAL FIELDS · 完整字段"));
  const prop=elem("div","fw-properties");
  for(const f of d.fields){
    prop.append(elem("div","fw-property-label",String(f.index).padStart(2,"0")+" · "+f.name),
                elem("div","fw-property-value"+(isEmpty(f.value)?" fw-null":""),display(f.value)));
  }
  section.append(prop);panel.append(section);
  if(d.parent){
    const par=elem("section","fw-detail-section");
    par.append(elem("h3","","PARENT SHOT · 父镜头"));
    const text=d.parent.status==="ambiguous"?
      "存在歧义："+d.parent.source_label+"；候选记录 "+d.parent.candidate_record_ids.join("、"):
      d.parent.status==="resolved"?"确定父镜头："+d.parent.parent_record_id:
      "未找到父镜头："+d.parent.source_label;
    par.append(elem("div","live-parent-alert",text));panel.append(par);
  }
  if(d.assets.length){
    const attach=elem("section","fw-detail-section");
    attach.append(elem("h3","","ASSETS · 未匹配引用"));
    d.assets.forEach(a=>attach.append(elem("div","fw-asset-line",a.filename+" · "+a.status)));
    panel.append(attach);
  }
  wrap.append(panel);
  return wrap;
}
function renderContent(){
  const root=$("view-content");root.replaceChildren();
  if(!state.connected){root.append(empty("先连接本地 API"));return;}
  if(state.mode==="detail"){
    const row=state.detail?.record??visibleRows().find(x=>x.record_id===state.selectedId)??visibleRows()[0];
    if(row)root.append(detailView(row,state.detail?.asset_references??state.page?.asset_references??[]));
    else root.append(empty("当前页没有记录"));
  }else if(!visibleRows().length)root.append(empty("当前条件没有匹配记录"));
  else if(state.mode==="grid")appendGrid(root);
  else appendThumb(root);
}
function redraw(){
  updateProjectSelect();updateNav();updateHeader();updateFieldPicker();renderContent();
}
async function loadPage(cursor=null,history=[],query=state.query){
  if(!state.connected)return;
  const generation=++state.generation;
  setStatus("正在查询本地 SQLite…");setLoading(true);
  try{
    const response=await client.page(state.projectId,{
      sheet:state.sheetIndex,limit:limits[state.mode],cursor,
      q:query.q,field:query.field,value:query.value
    });
    if(!isActive(generation))return;
    state.page=response;state.query={...query};
    state.cursor=cursor;state.cursorHistory=[...history];state.pageNum=history.length+1;
    state.selectedId=response.records[0]?.record_id??null;state.detail=null;
    setStatus("");redraw();
  }catch(error){
    if(isActive(generation))setStatus("查询失败："+(error instanceof Error?error.message:"未知错误")+
                                    "。已有记录仍可查看。");
  }finally{if(isActive(generation)){setLoading(false);updateHeader();}}
}
async function switchProject(id){
  if(!state.connected || !client.projectIds.has(id))return;
  const generation=++state.generation;
  setStatus("正在读取项目目录…");setLoading(true);
  try{
    const m=await client.manifest(id);
    if(!isActive(generation))return;
    state.projectId=id;state.manifest=m;state.mode="grid";
    state.sheetIndex=m.sheets[0]?.sheet_index??1;
    state.meta=m.sheets[0]??null;state.query={q:"",field:null,value:null};
    state.page=null;state.pageNum=1;state.cursor=null;state.cursorHistory=[];
    state.selectedId=null;state.detail=null;
    redraw();
    if(state.meta)await loadPage();
    else{setStatus("该项目没有工作表。");setLoading(false);}
  }catch(error){
    if(isActive(generation)){setStatus("项目读取失败："+error.message);setLoading(false);}
  }
}
async function switchSheet(index){
  const meta=state.manifest?.sheets.find(s=>s.sheet_index===index);
  if(!meta)return;
  state.sheetIndex=index;state.meta=meta;state.mode="grid";
  state.query={q:"",field:null,value:null};state.page=null;state.pageNum=1;
  state.cursor=null;state.cursorHistory=[];state.detail=null;
  redraw();await loadPage();
}
async function openDetail(id){
  const row=visibleRows().find(r=>r.record_id===id);
  if(!row)return;
  const generation=++state.generation;
  state.selectedId=id;state.mode="detail";state.detail=null;
  redraw();setStatus("正在读取单条记录…");
  try{
    const full=await client.record(state.projectId,state.sheetIndex,id);
    if(!isActive(generation))return;
    state.detail=full;
    setStatus("");redraw();
  }catch(error){
    if(isActive(generation))setStatus("详情接口暂不可用："+error.message+"。显示当前页已加载记录。");
  }
}
async function switchView(mode){
  if(!state.connected){setStatus("请先连接本地 API。");return;}
  if(state.mode===mode)return;
  state.mode=mode;state.page=null;state.pageNum=1;state.cursorHistory=[];state.cursor=null;
  await loadPage();
}
async function connect(event){
  event.preventDefault();
  const input=$("token-input");
  const token=input.value.trim();
  setStatus("验证本地访问令牌…");setLoading(true);
  try{
    client.authenticate(token);
    const projects=await client.projects();
    if(!projects.length)throw new Error("本地未注册项目");
    state.projects=projects;state.connected=true;
    input.value="";$("connection-panel").classList.add("is-connected");
    await switchProject(projects[0].id);
  }catch(error){
    state.connected=false;client.disconnect();state.projects=[];state.projectId=null;
    setStatus("连接失败："+(error instanceof Error?error.message:"未知错误"));
  }finally{setLoading(false);}
}
$("connect-form").addEventListener("submit",connect);
$("project-select").addEventListener("change",e=>switchProject(e.target.value));
$("query-form").addEventListener("submit",async e=>{
  e.preventDefault();if(!state.connected)return;
  const q=$("search").value.trim();
  const fieldRaw=$("filter-field").value;
  const query={q,field:fieldRaw?Number(fieldRaw):null,
               value:fieldRaw?$("filter-value").value:null};
  await loadPage(null,[],query);
});
$("filter-field").addEventListener("change",e=>{
  $("filter-value").disabled=!e.target.value;
  if(!e.target.value)$("filter-value").value="";
});
$("reset-query").addEventListener("click",async ()=>{
  state.query={q:"",field:null,value:null};updateFieldPicker();
  await loadPage(null,[],state.query);
});
$("previous-page").addEventListener("click",async ()=>{
  if(!state.cursorHistory.length)return;
  const h=[...state.cursorHistory];const previous=h.pop();
  await loadPage(previous,h);
});
$("next-page").addEventListener("click",async ()=>{
  if(!state.page?.next_cursor)return;
  await loadPage(state.page.next_cursor,[...state.cursorHistory,state.cursor]);
});
for(const mode of ["grid","thumb","detail"]){
  $("view-"+mode).addEventListener("click",()=>switchView(mode));
}
redraw();
