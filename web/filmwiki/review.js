/* Step37 reviewer panel, standalone from native PukiWiki UI. */
"use strict";
const API="http://127.0.0.1:8098", el=id=>document.getElementById(id);
let bearer=null,identity=null;
function node(tag,label){
  const x=document.createElement(tag);
  if(label!==undefined)x.textContent=String(label);
  return x;
}
function feedback(msg){el("feedback").textContent=String(msg)}
async function api(path,body=null,credential=bearer){
 const headers={};
 if(credential)headers.Authorization="Bearer "+credential;
 if(body!==null)headers["Content-Type"]="application/json";
 const res=await globalThis.fetch(API+path,{
  method:body===null?"GET":"POST",mode:"cors",credentials:"omit",
  cache:"no-store",referrerPolicy:"no-referrer",redirect:"error",headers,
  ...(body===null?{}:{body:JSON.stringify(body)})
 });
 if(!res.ok)throw Error("请求被拒绝，HTTP "+res.status);
 return res.json();
}
async function useCredential(token){
 const user=await api("/v5/me",null,token);
 if(!user.memberships.some(x=>x.project_id==="demo-editor" &&
       ["owner","reviewer","publisher"].includes(x.role)))
  throw Error("需要项目审核权限");
 bearer=token;identity=user;
 el("login-panel").hidden=true;el("workspace").hidden=false;
 el("who").textContent=user.username+" · "+user.memberships.map(x=>x.role).join("、");
 await refresh();
}
async function nativeSSO(showError=false){
 let tempToken=null;
 try{
  const opts={cache:"no-store",credentials:"same-origin",redirect:"error",
              referrerPolicy:"no-referrer"};
  const get=await globalThis.fetch("/index.php?plugin=fwsso",{...opts,method:"GET"});
  if(!get.ok)throw Error("请先登录 PukiWiki");
  const hello=await get.json();
  const post=await globalThis.fetch("/index.php?plugin=fwsso",{
    ...opts,method:"POST",
    headers:{"Content-Type":"application/x-www-form-urlencoded"},
    body:new URLSearchParams({csrf:hello.csrf}).toString()
  });
  if(!post.ok)throw Error("Wiki 会话交换被拒绝");
  const proof=await post.json();
  const result=await api("/v5/sso",{assertion:proof.assertion,signature:proof.signature},null);
  tempToken=result.access_token;
  await useCredential(tempToken);
  feedback("已通过 PukiWiki 身份进入审核台");
 }catch(err){
  if(tempToken)try{await api("/v5/logout",{},tempToken)}catch{}
  if(showError)feedback(err.message);
 }
}
async function refresh(){
 if(!bearer)return;
 try{
  const [jobs,review]=await Promise.all([api("/v7/scans"),api("/v7/reviews")]);
  const list=el("jobs");list.replaceChildren();
  for(const job of jobs.jobs){
   const item=node("div");item.className="field";
   item.append(node("strong",job.status+" · "+job.asset_id.slice(0,8)),
               node("small","版本 "+job.version+" · 尝试次数 "+job.attempts));
   list.append(item);
  }
  if(!jobs.jobs.length)list.append(node("p","目前没有扫描任务"));
  const inbox=el("inbox");inbox.replaceChildren();
  for(const asset of review.pending){
    const item=node("div");item.className="field";
    item.append(node("strong",asset.original_filename),
                node("small","扫描："+asset.scan_state+" · 版本 "+asset.version));
    const preview=node("button","安全预览");
    preview.type="button";
    preview.addEventListener("click",()=>showPreview(asset.asset_id));
    item.append(preview);
    const retry=node("button","提交扫描任务");
    retry.type="button";
    retry.addEventListener("click",async()=>{
      retry.disabled=true;
      try{
        const result=await api("/v7/scans/queue",{asset_id:asset.asset_id});
        feedback("扫描任务："+result.status);await refresh();
      }catch(e){feedback(e.message)}finally{retry.disabled=false}
    });
    item.append(retry);
    const approve=node("button","批准");
    approve.type="button";
    approve.disabled=!asset.can_approve;
    approve.addEventListener("click",()=>decision(asset.asset_id,"approved"));
    item.append(approve);
    const reject=node("button","拒绝");
    reject.type="button";
    reject.addEventListener("click",()=>decision(asset.asset_id,"rejected"));
    item.append(reject);inbox.append(item);
  }
  if(!review.pending.length)inbox.append(node("p","暂无待审核附件"));
  feedback("扫描任务 "+jobs.jobs.length+"，待审核 "+review.pending.length+"。");
 }catch(e){feedback(e.message)}
}
async function showPreview(assetId){
 el("preview").textContent="读取安全文本摘录…";
 try{
  const result=await api("/v7/assets/"+assetId+"/preview");
  el("preview").textContent=result.available?result.text:"不支持安全预览或尚未通过扫描";
 }catch(e){el("preview").textContent="预览被拒绝："+e.message}
}
async function decision(assetId,kind){
 try{
  const result=await api("/v7/reviews/decision",{asset_id:assetId,decision:kind});
  el("preview").textContent="";
  await refresh();feedback(result.decision==="approved"?"审核通过":"已拒绝提交");
 }catch(e){feedback(e.message)}
}
el("login-form").addEventListener("submit",async e=>{
 e.preventDefault();
 try{
  const response=await api("/v5/login",{
    username:el("username").value.trim(),password:el("password").value},null);
  el("password").value="";
  try{await useCredential(response.access_token)}
  catch(err){
   try{await api("/v5/logout",{},response.access_token)}catch{}
   throw err;
  }
 }catch(err){feedback(err.message)}
});
el("sso-button").addEventListener("click",()=>nativeSSO(true));
el("refresh").addEventListener("click",()=>refresh());
el("logout").addEventListener("click",async()=>{
 const old=bearer;bearer=null;identity=null;
 el("workspace").hidden=true;el("login-panel").hidden=false;
 el("jobs").replaceChildren();el("inbox").replaceChildren();
 el("preview").textContent="";
 if(old)try{await api("/v5/logout",{},old)}catch{}
 feedback("已退出当前审核会话");
});
void nativeSSO(false);
