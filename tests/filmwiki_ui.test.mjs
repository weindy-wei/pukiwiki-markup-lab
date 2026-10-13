import test from "node:test";
import assert from "node:assert/strict";
import {
  parseSnapshot, demoSnapshot, display, preferredColumns,
  rowLabel, filterRecords, sheetSummary, recordDetail
} from "../web/filmwiki/model.js";

test("synthetic FilmWiki snapshot supports wide records and duplicate shot names", () => {
  const raw=parseSnapshot(structuredClone(demoSnapshot));
  assert.equal(raw.sheets[0].fields.length,17);
  assert.equal(raw.sheets[0].records.length,7);
  assert.equal(raw.sheets[0].records.filter(r=>r.shot_number==="SC_06").length,2);
  assert.notEqual(raw.sheets[0].records[4].record_id,raw.sheets[0].records[5].record_id);
  assert.equal(raw.sheets[2].records.length,0);
});
test("eight-column grid is projection only; detail returns all 17 columns",()=>{
  const raw=parseSnapshot(structuredClone(demoSnapshot));
  const sheet=raw.sheets[0];
  assert.equal(preferredColumns(sheet).length,8);
  assert.equal(sheet.fields.length,17);
  const detail=recordDetail(sheet,sheet.records[6],raw.asset_references);
  assert.equal(detail.fields.length,17);
  assert.equal(detail.parent.status,"ambiguous");
  assert.deepEqual(detail.parent.candidate_record_ids,["src_demo_005","src_demo_006"]);
});
test("search operates on all original fields, not just selected grid columns",()=>{
  const sheet=parseSnapshot(structuredClone(demoSnapshot)).sheets[0];
  assert.equal(filterRecords(sheet,"眼神细节").length,1);
  assert.equal(filterRecords(sheet,"SC_06").length,3);
  assert.equal(filterRecords(sheet,"不存在").length,0);
});
test("unverified duplicate file basenames are distinct unresolved assets",()=>{
  const raw=parseSnapshot(structuredClone(demoSnapshot));
  const s=sheetSummary(raw.sheets[0],raw.asset_references);
  assert.equal(s.unresolvedAssets,2);
  assert.equal(raw.asset_references[0].filename,raw.asset_references[1].filename);
  assert.notEqual(raw.asset_references[0].ref_id,raw.asset_references[1].ref_id);
  assert.equal(s.parentLinks.ambiguous,1);
  assert.equal(display(null),"—");
});
test("malformed input is rejected before any render",()=>{
  const raw=structuredClone(demoSnapshot);
  raw.sheets[0].records[1].record_id=raw.sheets[0].records[0].record_id;
  assert.throws(()=>parseSnapshot(raw),/重复或无效记录/);
  const invalid=structuredClone(demoSnapshot);
  invalid.sheets[0].records[1].cells.pop();
  assert.throws(()=>parseSnapshot(invalid),/记录字段数不一致/);
  const weird=structuredClone(demoSnapshot);
  weird.asset_references[0].record_id="src_missing";
  assert.throws(()=>parseSnapshot(weird),/无法关联的附件引用/);
  assert.throws(()=>parseSnapshot({format:"unknown"}),/格式/);
});
test("unknown text stays as data in the model",()=>{
  const raw=structuredClone(demoSnapshot);
  raw.sheets[0].records[0].cells[8].value="Unknown [special] content";
  const safe=parseSnapshot(raw);
  assert.equal(safe.sheets[0].records[0].cells[8].value,"Unknown [special] content");
  assert.equal(rowLabel(safe.sheets[0],safe.sheets[0].records[0]),"SC_01");
});
