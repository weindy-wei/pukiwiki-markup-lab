/* FilmWiki v2 private project query client. Token lives in memory only. */
export const API="http://127.0.0.1:8091";
export const MAX_PAGE=100;
const PROJECT=/^[a-z][a-z0-9_-]{1,39}$/;
const CURSOR=/^[A-Za-z0-9_-]{1,380}$/;
function check(test,message){if(!test)throw new Error(message);}

export class ProjectClient {
  constructor(fetcher=fetch){this.fetcher=fetcher;this.token=null;this.projectIds=new Set();}
  authenticate(token){
    check(typeof token==="string" && token.length>=40 && token.length<=200,"本地令牌格式无效");
    this.token=token;
  }
  disconnect(){this.token=null;this.projectIds.clear();}
  async get(route,params={},signal){
    check(this.token,"尚未提供本地访问令牌");
    const qs=new URLSearchParams();
    for(const [key,value] of Object.entries(params))if(value!==null && value!==undefined)qs.set(key,String(value));
    const url=API+route+(qs.size?"?"+qs:"");
    const res=await this.fetcher(url,{method:"GET",mode:"cors",credentials:"omit",cache:"no-store",
      redirect:"error",referrerPolicy:"no-referrer",signal,headers:{Authorization:"Bearer "+this.token}});
    if(res.status===401)throw new Error("本地 API 访问令牌无效");
    if(res.status===403)throw new Error("页面未获授权访问本地 API");
    if(!res.ok)throw new Error("接口请求失败 HTTP "+res.status);
    return res.json();
  }
  project(id){
    check(typeof id==="string" && PROJECT.test(id) && this.projectIds.has(id),"项目未在本地白名单内");
    return "/v2/projects/"+id;
  }
  async projects(signal){
    const doc=await this.get("/v2/projects",{},signal);
    check(doc?.format==="filmwiki-project-list" && doc.version===2 &&
      Array.isArray(doc.projects) && doc.projects.length<=32,"项目清单格式无效");
    const ids=new Set();
    for(const project of doc.projects){
      check(PROJECT.test(project.id) && typeof project.title==="string" &&
        !ids.has(project.id),"项目白名单无效");
      ids.add(project.id);
    }
    this.projectIds=ids;
    return doc.projects;
  }
  async manifest(projectId,signal){
    const doc=await this.get(this.project(projectId)+"/manifest",{},signal);
    check(doc?.format==="filmwiki-project-manifest" && doc.version===2 &&
      doc.project?.id===projectId && Array.isArray(doc.sheets) && doc.sheets.length<=100,
      "工作表清单格式无效");
    for(const s of doc.sheets)check(Number.isSafeInteger(s.sheet_index) &&
      Number.isSafeInteger(s.record_count) && s.record_count>=0 &&
      Array.isArray(s.fields) && s.fields.length<=256,"工作表元数据格式无效");
    return doc;
  }
  async page(projectId,{sheet,limit=50,cursor=null,q="",field=null,value=null},signal){
    check(Number.isInteger(sheet) && sheet>=1 && sheet<=100,"工作表编号无效");
    check(Number.isInteger(limit) && limit>=1 && limit<=MAX_PAGE,"分页大小超限");
    check(typeof q==="string" && q.length<=80,"搜索内容过长");
    check(cursor===null || CURSOR.test(cursor),"游标格式无效");
    check((field===null && value===null) ||
      (Number.isInteger(field) && field>=1 && field<=256 &&
       typeof value==="string" && value.length<=160),"字段筛选条件无效");
    const path=this.project(projectId)+"/records";
    const data=await this.get(path,{sheet,limit,cursor,q,field,value},signal);
    check(data?.format==="filmwiki-record-page" && data.version===2 &&
      data.project_id===projectId && data.sheet_index===sheet &&
      Array.isArray(data.records) && Array.isArray(data.asset_references) &&
      data.records.length<=limit && typeof data.has_more==="boolean" &&
      ((data.has_more && typeof data.next_cursor==="string" && CURSOR.test(data.next_cursor)) ||
       (!data.has_more && data.next_cursor===null)),"服务器分页结果不一致");
    return data;
  }
  async record(projectId,sheet,recordId,signal){
    check(Number.isInteger(sheet) && sheet>=1 && sheet<=100 &&
      typeof recordId==="string" && /^src_[a-zA-Z0-9_-]{1,95}$/.test(recordId),
      "记录编号无效");
    const data=await this.get(this.project(projectId)+"/record",{sheet,id:recordId},signal);
    check(data?.format==="filmwiki-record-detail" && data.version===2 &&
      data.project_id===projectId && data.sheet_index===sheet &&
      data.record?.record_id===recordId && Array.isArray(data.asset_references),
      "记录详情数据不一致");
    return data;
  }
}
