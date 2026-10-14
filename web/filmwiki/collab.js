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
async function completeLogin(auth){
 token=auth.access_token;
 $("password").value="";
 me=await call("/v5/me");
 const loaded=await call("/v5/manifest");
 const directory=await call("/v5/members");
 manifest=loaded;members=new Map(directory.members.map(x=>[x.user_id,x.username]));
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
 busy=false;
 await loadPage();
 $("login-panel").hidden=true;$("workspace").hidden=false;
 void showSessions();void showAssets();
}
$("login-form").addEventListener("submit",async e=>{
 e.preventDefault();if(busy)return;busy=true;
 $("login-button").disabled=true;
 const name=$("username").value.trim(),password=$("password").value;
 feedback("正在验证身份…");
 try{
  const auth=await call("/v5/login",{username:name,password},null);
  await completeLogin(auth);
 }catch(err){
  if(token){try{await call("/v5/logout",{});}catch{}}
  token=null;me=null;
  feedback("登录失败："+err.message,"error");
 }finally{busy=false;$("login-button").disabled=false;}
});
async function tryNativeSSO(quiet=false){
 if(busy||token)return;
 busy=true;$("sso-button").disabled=true;
 try{
  const endpoint="/index.php?plugin=fwsso";
  const options={credentials:"same-origin",cache:"no-store",
     redirect:"error",referrerPolicy:"no-referrer"};
  const check=await globalThis.fetch(endpoint,{...options,method:"GET"});
  if(!check.ok)throw Error("请先登录 PukiWiki");
  const challenge=await check.json();
  if(!challenge.authenticated||!(/^[a-f0-9]{64}$/.test(challenge.csrf))){
    throw Error("Wiki 身份会话尚未就绪");
  }
  const post=await globalThis.fetch(endpoint,{
    ...options,method:"POST",
    headers:{"Content-Type":"application/x-www-form-urlencoded"},
    body:new URLSearchParams({csrf:challenge.csrf}).toString()
  });
  if(!post.ok)throw Error("无法验证当前 Wiki 会话");
  const proof=await post.json();
  const auth=await call("/v5/sso",{
    assertion:proof.assertion,signature:proof.signature
  },null);
  await completeLogin(auth);
  feedback("已使用当前 Wiki 身份进入协作项目。","success");
 }catch(err){
  if(token){try{await call("/v5/logout",{});}catch{}}
  token=null;me=null;
  if(!quiet)feedback(err.message+"；也可以使用下方账号密码登录。","error");
 }finally{busy=false;$("sso-button").disabled=false;}
}
$("sso-button").addEventListener("click",()=>tryNativeSSO(false));
// On an already signed-in Wiki tab, enter without retyping credentials.
// Failure is quiet, preserving the original password login flow.
void tryNativeSSO(true);

async function showSessions(){
 const out=$("session-list");out.replaceChildren();
 if(!token)return;
 try{
  const data=await call("/v6/sessions");
  for(const entry of data.sessions){
   const div=text("div",undefined,"field");
   div.append(text("small",(entry.session_id===data.current_session_id?"当前设备":"其他设备")+" · "+entry.source));
   const content=text("div",new Date(entry.created_at*1000).toLocaleString());
   if(entry.session_id!==data.current_session_id){
    const btn=text("button","撤销");
    btn.addEventListener("click",async()=>{
     btn.disabled=true;
     try{
      await call("/v6/sessions/revoke",{session_id:entry.session_id});
      await showSessions();feedback("会话已撤销","success");
     }catch(err){feedback(err.message,"error");btn.disabled=false;}
    });
    content.append(btn);
   }
   div.append(content);out.append(div);
  }
  if(!data.sessions.length)out.append(text("p","没有有效会话","empty"));
 }catch(err){out.append(text("p","会话列表无法读取","empty"));}
}
async function showAssets(){
 const out=$("asset-list");out.replaceChildren();
 if(!token)return;
 try{
  const data=await call("/v6/assets");
  for(const entry of data.assets){
   const btn=text("button",entry.original_filename+" · "+entry.visibility);
   btn.addEventListener("click",async()=>{
    btn.disabled=true;
    try{
     const response=await globalThis.fetch(API+"/v6/assets/"+entry.asset_id+"/content",{
      method:"GET",mode:"cors",cache:"no-store",credentials:"omit",
      headers:{Authorization:"Bearer "+token}});
     if(!response.ok)throw Error("没有附件读取权限："+response.status);
     const blob=await response.blob();
     const link=document.createElement("a");
     const url=URL.createObjectURL(blob);
     try{
      link.href=url;link.download="asset-"+entry.asset_id;
      link.hidden=true;document.body.append(link);link.click();link.remove();
     }finally{URL.revokeObjectURL(url);}
     feedback("授权附件已获取","success");
    }catch(err){feedback(err.message,"error");}
    finally{btn.disabled=false;}
   });
   out.append(btn);
  }
  if(!data.assets.length)out.append(text("p","没有可读取的附件","empty"));
 }catch(err){out.append(text("p","附件列表无法读取","empty"));}
}
$("refresh-sessions").addEventListener("click",()=>showSessions());
$("refresh-assets").addEventListener("click",()=>showAssets());
$("logout-all").addEventListener("click",async()=>{
 if(!token)return;
 try{
  await call("/v6/sessions/revoke-all",{});
  token=null;me=null;manifest=null;current=null;
  $("workspace").hidden=true;$("login-panel").hidden=false;
  $("session-list").replaceChildren();$("asset-list").replaceChildren();
  feedback("所有协作会话已注销，旧 Wiki 会话将于下一次请求失效。","success");
 }catch(err){feedback(err.message,"error");}
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
