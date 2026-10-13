import test from "node:test";
import assert from "node:assert/strict";
import { ProjectClient, API } from "../web/filmwiki/v2-client.js";

const projects=[{id:"demo-a",title:"项目甲",sheet_count:2},
                {id:"demo-b",title:"项目乙",sheet_count:1}];
const fields=[{index:1,name:"镜头号"},{index:2,name:"镜头内容"}];
const manifest=(id)=>({
  format:"filmwiki-project-manifest",version:2,project:{id,title:id},
  source:{filename:id+".xlsx",sha256:"a".repeat(64)},
  sheets:[{sheet_index:1,name:"第1集",fields,record_count:500000}]
});
const row=(n)=>({record_id:"src_demo_"+n,source_row:n,shot_number:"SC_"+n,
  cells:[{index:1,field:"镜头号",value:"SC_"+n},{index:2,field:"镜头内容",value:"内容"+n}],
  parent_link:null});
function endpoints(){
 const calls=[];
 const fetcher=async(url,opts)=>{
  assert.equal(opts.credentials,"omit");
  assert.equal(opts.method,"GET");
  assert.equal(opts.headers.Authorization,"Bearer "+"t".repeat(64));
  assert.ok(url.startsWith(API+"/v2/"));
  calls.push(url);
  const uri=new URL(url);
  const parts=uri.pathname.split("/");
  if(uri.pathname==="/v2/projects")return {ok:true,json:async()=>({format:"filmwiki-project-list",version:2,projects})};
  if(parts[4]==="manifest")return {ok:true,json:async()=>manifest(parts[3])};
  if(parts[4]==="records"){
    const after=uri.searchParams.get("cursor");
    const rows=after?[row(52)]:[row(2),row(3)];
    return {ok:true,json:async()=>({
       format:"filmwiki-record-page",version:2,project_id:parts[3],
       sheet_index:Number(uri.searchParams.get("sheet")),records:rows,
       asset_references:[],has_more:!after,next_cursor:after?null:"signedcursor"
    })};
  }
  if(parts[4]==="record")return {ok:true,json:async()=>({
    format:"filmwiki-record-detail",version:2,project_id:parts[3],
    sheet_index:Number(uri.searchParams.get("sheet")),
    record:row(2),asset_references:[]
  })};
  throw new Error("Unexpected url: "+url);
 };
 return {fetcher,calls};
}
test("project list is allowlisted, manifest contains 500k rows only as metadata",async()=>{
 const {fetcher,calls}=endpoints();
 const client=new ProjectClient(fetcher);
 client.authenticate("t".repeat(64));
 assert.equal((await client.projects()).length,2);
 const m=await client.manifest("demo-a");
 assert.equal(m.sheets[0].record_count,500000);
 assert.equal(calls.length,2);
 await assert.rejects(()=>client.manifest("unregistered"),/项目/);
});
test("keyset paging only fetches one page at a time",async()=>{
 const {fetcher,calls}=endpoints();const client=new ProjectClient(fetcher);
 client.authenticate("t".repeat(64));await client.projects();
 let page=await client.page("demo-a",{sheet:1,limit:2,q:"SC_06",field:1,value:"SC_06"});
 assert.equal(page.records.length,2);
 assert.equal(page.has_more,true);
 assert.equal(page.next_cursor,"signedcursor");
 page=await client.page("demo-a",{sheet:1,cursor:page.next_cursor,limit:2});
 assert.equal(page.records.length,1);
 assert.equal(page.has_more,false);
 const urls=calls.filter(u=>u.includes("/records?"));
 assert.equal(urls.length,2);
 assert.ok(urls[0].includes("field=1"));
 assert.ok(urls[0].includes("value=SC_06"));
});
test("reject server project confusion, bad cursor, impossible page",async()=>{
 const {fetcher}=endpoints();const client=new ProjectClient(fetcher);
 client.authenticate("t".repeat(64));await client.projects();
 await assert.rejects(()=>client.page("demo-b",{sheet:1,cursor:"bad!"}),/游标/);
 await assert.rejects(()=>client.page("demo-a",{sheet:1,limit:300}),/分页/);
 client.disconnect();
 await assert.rejects(()=>client.projects(),/令牌/);
});
