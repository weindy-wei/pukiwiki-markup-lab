/* FilmWiki Step28: dedicated synthetic working-copy editor, token in memory only. */
const BASE="http://127.0.0.1:8094";
const $=id=>document.getElementById(id);
let token=null,manifest=null,current=null,loaded=null,sheet=1,historyRows=[];
let pageAfter=1,pageStack=[],search="",busy=false;
function node(tag,text,className){
  const n=document.createElement(tag);
  if(text!==undefined)n.textContent=String(text);
  if(className)n.className=className;
  return n;
}
function status(message,error=false,success=false){
  $("notice").textContent=message;
  $("notice").className=error?"error":success?"success":"";
}
function qs(params){return new URLSearchParams(params).toString();}
async function request(path,body=null){
  if(!token)throw new Error("请先连接编辑令牌");
  const response=await globalThis.fetch(BASE+path,{
    method:body===null?"GET":"POST",mode:"cors",credentials:"omit",
    cache:"no-store",redirect:"error",referrerPolicy:"no-referrer",
    headers:{"Authorization":"Bearer "+token,
      ...(body===null?{}:{"Content-Type":"application/json"})},
    ...(body===null?{}:{body:JSON.stringify(body)})
  });
  if(response.status===401)throw new Error("编辑令牌无效");
  if(response.status===409){
    const e=new Error("记录版本已改变，请刷新详情后再提交");
    e.conflict=true;throw e;
  }
  if(!response.ok)throw new Error("编辑 API 错误："+response.status);
  return response.json();
}
function updatePaging(){
  $("page-state").textContent="第 "+(pageStack.length+1)+" 页";
  $("prev-page").disabled=!pageStack.length||busy;
  $("next-page").disabled=!loaded?.next_after||busy;
}
function fieldValue(value){return value===null?"(null)":typeof value==="string"?value:JSON.stringify(value);}
function showRecords(){
  const root=$("record-list");root.replaceChildren();
  const rows=loaded?.records??[];
  if(!rows.length)root.append(node("p","当前条件没有记录","empty"));
  for(const item of rows){
    const brief=item.cells.find(c=>c.name==="镜头号")?.value ??
      item.cells.find(c=>c.name==="角色名称")?.value ??
      item.cells[0]?.value ??item.record_id;
    const b=node("button",undefined,"record-item"+(current?.record_id===item.record_id?" active":""));
    b.type="button";
    b.append(node("span",fieldValue(brief)),node("small","v"+item.version+" · row "+item.source_row));
    b.addEventListener("click",()=>loadRecord(item.record_id));
    root.append(b);
  }
  updatePaging();
}
async function loadPage(after=1,stack=[]){
  if(busy)return;
  busy=true;status("读取工作副本记录…");
  try{
    const page=await request("/v4/records?"+qs({sheet,after,limit:25,q:search}));
    loaded=page;pageAfter=after;pageStack=[...stack];current=null;historyRows=[];
    $("field-list").replaceChildren();$("history-list").replaceChildren();
    $("record-id").textContent="请选择左侧记录";
    $("record-version").textContent="—";
    $("edit-field").replaceChildren();
    $("edit-value").value="";$("edit-reason").value="";
    showRecords();status("已加载 "+page.count+" 条记录（本页）。");
  }catch(e){status(e.message,true);}
  finally{busy=false;updatePaging();}
}
function renderFields(){
  $("record-id").textContent=current.record_id+" · 原始行 "+current.source_row;
  $("record-version").textContent="v"+current.version;
  const out=$("field-list");out.replaceChildren();
  const select=$("edit-field");select.replaceChildren();
  for(const field of current.cells){
    const wrap=node("div",undefined,"field");
    const label=node("label",field.index+". "+field.name+(field.editable?" · 可编辑":""));
    if(field.editable)label.classList.add("editable");
    wrap.append(label,node("div",fieldValue(field.value),"value"));out.append(wrap);
    if(field.editable && (typeof field.value==="string"||field.value===null)){
      const option=node("option",field.name);option.value=String(field.index);select.append(option);
    }
  }
  $("save").disabled=select.options.length===0;
  fillValueFromSelected();
}
function fillValueFromSelected(){
  if(!current)return;
  const index=Number($("edit-field").value);
  const field=current.cells.find(x=>x.index===index);
  $("edit-value").value=field?.value===null?"":typeof field?.value==="string"?field.value:"";
}
function renderHistory(){
  const root=$("history-list");root.replaceChildren();
  if(!historyRows.length){
    root.append(node("p","尚无修改历史，当前为导入后的第 0 版。","empty"));return;
  }
  for(const event of [...historyRows].reverse()){
    const section=node("section",undefined,"item");
    section.append(node("strong","版本 "+event.version+" · 字段 "+event.column_index),
      node("small",event.utc_created+" · "+event.actor),
      node("p","原因："+event.reason),
      node("p","修改前："+fieldValue(event.old_value)),
      node("p","修改后："+fieldValue(event.new_value)),
      node("p","SHA-256："+event.event_hash,"hash"));
    root.append(section);
  }
}
async function loadRecord(id){
  status("读取记录详情与历史…");
  try{
    const detail=await request("/v4/record?"+qs({sheet,id}));
    const audit=await request("/v4/history?"+qs({sheet,id}));
    current=detail;historyRows=audit.events;
    renderFields();renderHistory();showRecords();
    status("记录版本 v"+current.version+"，审计事件 "+historyRows.length+" 条。");
  }catch(e){status(e.message,true);}
}
$("connect").addEventListener("click",async()=>{
  const candidate=$("auth-token").value.trim();
  if(candidate.length<64){status("编辑令牌格式不正确",true);return;}
  token=candidate;$("connect").disabled=true;status("正在验证独立编辑 API…");
  try{
    manifest=await request("/v4/manifest");
    if(manifest.format!=="filmwiki-editor-manifest"||!manifest.working_copy)
      throw new Error("服务器不是可编辑工作副本");
    $("auth-token").value="";
    $("auth-panel").hidden=true;$("workspace").hidden=false;
    $("project-title").textContent=manifest.project.id;
    $("sheet-total").textContent=String(manifest.sheets.length);
    const sel=$("sheet-select");sel.replaceChildren();
    for(const s of manifest.sheets){
      const option=node("option",s.name+" ("+s.record_count+")");
      option.value=s.sheet_index;sel.append(option);
    }
    sheet=manifest.sheets[0]?.sheet_index??1;
    await loadPage();
  }catch(e){token=null;status("连接失败："+e.message,true);}
  finally{$("connect").disabled=false;}
});
$("sheet-select").addEventListener("change",()=>{sheet=Number($("sheet-select").value);search="";
  $("search-term").value="";loadPage();});
$("search-button").addEventListener("click",()=>{search=$("search-term").value.trim();loadPage();});
$("search-term").addEventListener("keydown",e=>{
  if(e.key==="Enter"){e.preventDefault();$("search-button").click();}
});
$("prev-page").addEventListener("click",()=>{
  if(!pageStack.length)return;
  const stack=[...pageStack];const previous=stack.pop();loadPage(previous,stack);
});
$("next-page").addEventListener("click",()=>{
  if(loaded?.next_after)loadPage(loaded.next_after,[...pageStack,pageAfter]);
});
$("edit-field").addEventListener("change",fillValueFromSelected);
$("edit-form").addEventListener("submit",async(e)=>{
  e.preventDefault();
  if(!current||busy)return;
  const field=Number($("edit-field").value),reason=$("edit-reason").value.trim();
  if(!field||reason.length<3){status("需要选择可编辑字段，并填写至少三个字的修改原因",true);return;}
  if(!globalThis.crypto?.randomUUID){status("浏览器不支持安全 UUID 生成",true);return;}
  const payload={project_id:manifest.project.id,sheet_index:sheet,
    record_id:current.record_id,column_index:field,value:$("edit-value").value,
    expected_version:current.version,reason,request_id:globalThis.crypto.randomUUID()};
  busy=true;$("save").disabled=true;status("正在提交版本 "+current.version+" → "+(current.version+1)+"…");
  try{
    const result=await request("/v4/edit",payload);
    $("edit-reason").value="";
    const id=result.record_id;
    await loadRecord(id);
    const replaced=loaded?.records.findIndex(x=>x.record_id===id);
    if(replaced>=0)loaded.records[replaced]=current;
    showRecords();status("提交成功：v"+result.version+"，修改与审计已一起保存。",false,true);
  }catch(error){
    status(error.message,true);
    if(error.conflict)await loadRecord(payload.record_id);
  }finally{busy=false;$("save").disabled=!current?.cells.some(c=>c.editable);}
});
