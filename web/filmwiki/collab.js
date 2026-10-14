/* FilmWiki Step31 per-user collaboration, ephemeral login session. */
const API="http://127.0.0.1:8098";
const $=id=>document.getElementById(id);
let token=null,me=null,manifest=null,members=new Map();
let current=null,sheet=1,page=null,after=1,trail=[],q="",busy=false;
const text=(tag,str,css)=>{
 const e=document.createElement(tag);
 e.textContent=str===undefined?"":String(str);
 if(css)e.className=css;
 return e;
};
function feedback(msg,kind=""){
 $("feedback").textContent=msg;
 $("feedback").className=kind;
}
function query(obj){return new URLSearchParams(obj).toString();}
async function call(path,data=null,tokenOverride){
 const credential=tokenOverride===undefined?token:tokenOverride;
 const response=await globalThis.fetch(API+path,{
  method:data===null?"GET":"POST",mode:"cors",cache:"no-store",
  credentials:"omit",redirect:"error",referrerPolicy:"no-referrer",
  headers:{...(credential?{"Authorization":"Bearer "+credential}:{}),
   ...(data!==null?{"Content-Type":"application/json"}:{})},
  ...(data!==null?{body:JSON.stringify(data)}:{})
 });
 if(response.status===401&&path!=="/v5/login"){
   token=null;me=null;$("workspace").hidden=true;$("login-panel").hidden=false;
   throw Error("会话已过期，请重新登录");
 }
 if(response.status===409){
   const err=Error("记录已被其他成员修改，请重新查看后再提交");err.conflict=true;throw err;
 }
 if(!response.ok)throw Error("请求被拒绝："+response.status);
 return response.json();
}
function value(x){return x===null?"(null)":typeof x==="string"?x:JSON.stringify(x);}
function allowed(){return me?.memberships?.some(x=>x.project_id===manifest?.project.id&&["editor","owner"].includes(x.role));}
function formatName(uid){return members.get(uid)??uid;}
function paging(){
 $("page-number").textContent="第 "+(trail.length+1)+" 页";
 $("previous").disabled=trail.length===0||busy;
 $("next").disabled=!page?.next_after||busy;
}
async function loadPage(next=1,stack=[]){
 if(busy)return;
 busy=true;feedback("正在读取记录…");
 try{
  page=await call("/v5/records?"+query({sheet,after:next,limit:25,q}));
  after=next;trail=[...stack];current=null;
  $("fields").replaceChildren();$("history").replaceChildren();
  $("selected").textContent="选择左侧记录";$("edit-form").hidden=true;
  showItems();feedback("已读取 "+page.count+" 条记录（本页）。");
 }catch(e){feedback(e.message,"error");}
 finally{busy=false;paging();}
}
function showItems(){
 const out=$("items");out.replaceChildren();
 for(const item of page?.records??[]){
  const label=item.cells.find(x=>x.name==="镜头号")?.value??
    item.cells.find(x=>x.name==="角色名称")?.value??item.cells[0]?.value??item.record_id;
  const button=text("button",undefined,"item"+(current?.record_id===item.record_id?" current":""));
  button.type="button";
  button.append(text("span",value(label)),text("small","v"+item.version));
  button.addEventListener("click",()=>openRecord(item.record_id));
  out.append(button);
 }
 if(!out.children.length)out.append(text("p","无匹配记录","empty"));
 paging();
}
function showFields(){
 $("selected").textContent=current.record_id+" · 修订版本 v"+current.version;
 $("fields").replaceChildren();
 const selector=$("edit-field");selector.replaceChildren();
 for(const field of current.cells){
  const wrap=text("div",undefined,"field");
  wrap.append(text("small",field.index+". "+field.name),text("div",value(field.value)));
  $("fields").append(wrap);
  if(allowed()&&field.editable&&(typeof field.value==="string"||field.value===null)){
   const opt=text("option",field.name);opt.value=field.index;selector.append(opt);
  }
 }
 $("edit-form").hidden=selector.options.length===0;
 changeField();
}
function changeField(){
 if(!current)return;
 const cell=current.cells.find(x=>x.index===Number($("edit-field").value));
 $("new-value").value=typeof cell?.value==="string"?cell.value:"";
}
function showHistory(events){
 const out=$("history");out.replaceChildren();
 if(!events.length){out.append(text("p","尚无修订历史","empty"));return;}
 for(const event of [...events].reverse()){
  const box=text("section",undefined,"event");
  box.append(text("strong","v"+event.version+" · "+formatName(event.actor)),
    text("small",event.utc_created+" · 字段 "+event.column_index),
    text("p","原因："+event.reason),
    text("p","原值："+value(event.old_value)),
    text("p","新值："+value(event.new_value)),
    text("small","校验哈希："+event.event_hash));
  out.append(box);
 }
}
async function openRecord(id){
 feedback("读取版本历史…");
 try{
  const [detail,audit]=await Promise.all([
   call("/v5/record?"+query({sheet,id})),
   call("/v5/history?"+query({sheet,id}))
  ]);
  current=detail;showFields();showHistory(audit.events);showItems();
  feedback("已载入 v"+detail.version+"，历史 "+audit.events.length+" 条。");
 }catch(err){feedback(err.message,"error");}
}
$("login-form").addEventListener("submit",async e=>{
 e.preventDefault();if(busy)return;busy=true;
 $("login-button").disabled=true;
 const name=$("username").value.trim(),password=$("password").value;
 feedback("正在验证身份…");
 try{
  const auth=await call("/v5/login",{username:name,password},null);
  token=auth.access_token;
  $("password").value="";
  me=await call("/v5/me");
  const loaded=await call("/v5/manifest");
  const directory=await call("/v5/members");
  manifest=loaded;members=new Map(directory.members.map(x=>[x.user_id,x.username]));
  $("login-panel").hidden=true;$("workspace").hidden=false;
  $("current-user").textContent=me.username;
  $("project-name").textContent=loaded.project.id;
  $("my-role").textContent=me.memberships
     .filter(x=>x.project_id===loaded.project.id).map(x=>x.role).join("、")||"无权限";
  const select=$("sheet");select.replaceChildren();
  for(const item of loaded.sheets){
   const opt=text("option",item.name+" ("+item.record_count+")");
   opt.value=item.sheet_index;select.append(opt);
  }
  sheet=loaded.sheets[0]?.sheet_index??1;
  busy=false;await loadPage();
 }catch(err){
  if(token){try{await call("/v5/logout",{});}catch{}}
  token=null;me=null;
  feedback("登录失败："+err.message,"error");
 }finally{busy=false;$("login-button").disabled=false;}
});
$("logout").addEventListener("click",async()=>{
 try{if(token)await call("/v5/logout",{});}
 catch{}
 token=null;me=null;manifest=null;current=null;
 $("workspace").hidden=true;$("login-panel").hidden=false;
 $("items").replaceChildren();$("history").replaceChildren();$("fields").replaceChildren();
 feedback("已安全退出。","success");
});
$("sheet").addEventListener("change",()=>{
 sheet=Number($("sheet").value);q="";$("search").value="";loadPage();
});
$("run-search").addEventListener("click",()=>{q=$("search").value.trim();loadPage();});
$("search").addEventListener("keydown",e=>{
 if(e.key==="Enter"){e.preventDefault();$("run-search").click();}
});
$("previous").addEventListener("click",()=>{
 if(!trail.length)return;
 const stack=[...trail];const old=stack.pop();loadPage(old,stack);
});
$("next").addEventListener("click",()=>{
 if(page?.next_after)loadPage(page.next_after,[...trail,after]);
});
$("edit-field").addEventListener("change",changeField);
$("edit-form").addEventListener("submit",async e=>{
 e.preventDefault();if(busy||!current||!allowed())return;
 const col=Number($("edit-field").value),reason=$("reason").value.trim();
 if(!col||reason.length<3)return feedback("请填写修订原因","error");
 if(!globalThis.crypto?.randomUUID)return feedback("浏览器不支持安全 UUID","error");
 const id=current.record_id;
 busy=true;$("save").disabled=true;
 try{
  const result=await call("/v5/edit",{
   project_id:manifest.project.id,sheet_index:sheet,record_id:id,
   column_index:col,value:$("new-value").value,expected_version:current.version,
   reason,request_id:globalThis.crypto.randomUUID()
  });
  $("reason").value="";
  await openRecord(id);
  const index=page.records.findIndex(x=>x.record_id===id);
  if(index>=0)page.records[index]=current;
  showItems();
  feedback("修订 v"+result.version+" 已保存，审计作者："+me.username,"success");
 }catch(err){
  feedback(err.message,"error");
  if(err.conflict)await openRecord(id);
 }finally{busy=false;$("save").disabled=false;}
});
