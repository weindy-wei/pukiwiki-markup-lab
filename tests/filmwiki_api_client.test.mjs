import test from "node:test";
import assert from "node:assert/strict";
import { readLocalSnapshot, LOCAL_API } from "../web/filmwiki/api-client.js";

const SOURCE_SHA="e".repeat(64);
const fields=Array.from({length:17}, (_,i)=>({index:i+1,name:i===0?"镜头号":"字段"+(i+1)}));
const record=(id,n)=>({
  record_id:"src_"+id,source_row:n,shot_number:"SC_06",
  cells:fields.map((f,i)=>({index:f.index,field:f.name,value:i===0?"SC_06":null})),
  parent_link:null
});
function replies() {
  return {
    "/v1/manifest":{
      format:"filmwiki-read-api-manifest",version:1,
      source:{filename:"synthetic.xlsx",sha256:SOURCE_SHA},
      sheets:[
        {sheet_index:1,name:"第1集",fields,record_count:2},
        {sheet_index:2,name:"道具设定",fields:[{index:1,name:"道具名称"}],record_count:0}
      ]
    },
    "/v1/records?sheet=1&offset=0&limit=200":{
      sheet_index:1,total:2,offset:0,next_offset:null,
      records:[record("first",2),record("second",3)],asset_references:[
        {ref_id:"ref_1",record_id:"src_first",filename:"image.png",source_column:2,status:"unresolved"}
      ]
    }
  };
}
test("reads manifest and paginated original fields without leaking auth to storage",async()=>{
  const data=replies(),calls=[];
  const fetcher=async(url,opts)=>{
    assert.ok(url.startsWith(LOCAL_API+"/v1/"));
    assert.equal(opts.method,"GET");
    assert.equal(opts.credentials,"omit");
    assert.equal(opts.cache,"no-store");
    assert.equal(opts.headers.Authorization,"Bearer "+"t".repeat(64));
    calls.push(url);
    const route=url.slice(LOCAL_API.length);
    assert.ok(data[route],route);
    return {ok:true,json:async()=>data[route]};
  };
  const result=await readLocalSnapshot("t".repeat(64),fetcher);
  assert.equal(result.sheets.length,2);
  assert.equal(result.sheets[0].fields.length,17);
  assert.equal(result.sheets[0].records.length,2);
  assert.equal(result.sheets[1].records.length,0);
  assert.equal(result.sheets[0].records[0].cells[1].value,null);
  assert.equal(result.asset_references[0].filename,"image.png");
  assert.equal(calls.length,2);
});
test("401 and bad pagination fail closed",async()=>{
  const bad=async()=>({ok:false,status:401});
  await assert.rejects(()=>readLocalSnapshot("t".repeat(64),bad),/访问令牌/);
  const data=replies();
  data["/v1/records?sheet=1&offset=0&limit=200"].next_offset=1;
  const fetcher=async(url)=>({ok:true,json:async()=>data[url.slice(LOCAL_API.length)]});
  await assert.rejects(()=>readLocalSnapshot("t".repeat(64),fetcher),/分页游标/);
});
test("rejects more than 5000 records before bulk fetch",async()=>{
  const data=replies();
  data["/v1/manifest"].sheets[0].record_count=5001;
  const calls=[];
  const fetcher=async(url)=>{calls.push(url);return {ok:true,json:async()=>data[url.slice(LOCAL_API.length)]}};
  await assert.rejects(()=>readLocalSnapshot("t".repeat(64),fetcher),/5000/);
  assert.equal(calls.length,1);
});
