"""Step37 durable queues, retry, role-filtered inbox, safely escaped previews."""
import hashlib,json,sqlite3,sys,tempfile,threading,unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/"tools/filmwiki_api"),str(ROOT/"tools/table_import")]
from collab_auth import create_store,create_user,grant,revoke
from asset_lifecycle_v8 import stage_new,review,trash_restore,audit
from asset_queue_v10 import (setup_queue,enqueue,claim_one,run_once,scan_jobs,
                             review_inbox,decide,inert_text_preview,_finish)
from asset_catalog_v7 import download_asset
PROJECT="demo-editor"
class QueueTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory();self.addCleanup(self.t.cleanup)
  self.root=Path(self.t.name);(self.root/"private").mkdir()
  staging=self.root/"staging/synthetic-assets";staging.mkdir(parents=True)
  self.src=staging/"snippet.txt"
  self.src.write_text('<script>alert("x")</script>\nFilmWiki sample\n',encoding="utf8")
  self.db=self.root/"private/users.sqlite"
  create_store(self.db,self.root)
  self.owner=create_user(self.db,"owner","Owner2026Strong!")
  self.reviewer=create_user(self.db,"reviewer","Reviewer2026Strong!")
  self.viewer=create_user(self.db,"viewer","Viewer2026Strong!")
  self.editor=create_user(self.db,"editor","Editor2026Strong!")
  for user,role in (("owner","owner"),("reviewer","reviewer"),
                    ("viewer","viewer"),("editor","editor")):
   grant(self.db,PROJECT,user,role)
  self.assets=self.root/"private/assets"
  setup_queue(self.assets,self.root)
  self.o={"user_id":self.owner["user_id"],
          "memberships":[{"project_id":PROJECT,"role":"owner"}]}
  self.r={"user_id":self.reviewer["user_id"],
          "memberships":[{"project_id":PROJECT,"role":"reviewer"}]}
  self.v={"user_id":self.viewer["user_id"],
          "memberships":[{"project_id":PROJECT,"role":"viewer"}]}
 def stage(self,who=None):
  return stage_new(self.assets,self.db,self.src,PROJECT,
                   who or self.owner["user_id"],"snippet.txt","text/plain","project")["asset_id"]
 def pass_scan(self,aid):
  from asset_security_v9 import _engine
  with patch("asset_security_v9._engine",return_value=("clean","synthetic_clean","test-only")):
   return run_once(self.assets,self.db)
 def test_01_duplicate_enqueue_is_idempotent_even_parallel(self):
  aid=self.stage()
  with ThreadPoolExecutor(5) as ex:
   results=list(ex.map(lambda _:enqueue(self.assets,self.db,aid,self.owner["user_id"]),range(5)))
  ids={x["job_id"] for x in results}
  self.assertEqual(len(ids),1)
  self.assertEqual(sum(not x["deduplicated"] for x in results),1)
 def test_02_claim_is_atomic_across_workers(self):
  aid=self.stage()
  enqueue(self.assets,self.db,aid,self.owner["user_id"])
  with ThreadPoolExecutor(4) as ex:
   claims=list(ex.map(lambda _:claim_one(self.assets),range(4)))
  self.assertEqual(sum(x is not None for x in claims),1)
 def test_03_successful_worker_then_inbox_review_then_download(self):
  aid=self.stage()
  enqueue(self.assets,self.db,aid,self.owner["user_id"])
  self.assertIsNone(inert_text_preview(self.assets,self.v,aid))
  self.assertFalse(inert_text_preview(self.assets,self.r,aid)["available"])
  result=self.pass_scan(aid)
  self.assertEqual(result["status"],"done")
  inbox=review_inbox(self.assets,self.r)
  self.assertEqual(len(inbox),1)
  self.assertTrue(inbox[0]["can_approve"])
  self.assertFalse(review_inbox(self.assets,self.o)[0]["can_approve"])
  with self.assertRaises(PermissionError):decide(self.assets,self.db,self.o,aid,"approved")
  approved=decide(self.assets,self.db,self.r,aid,"approved")
  self.assertEqual(approved["decision"],"approved")
  self.assertIsNotNone(download_asset(self.assets,self.o,PROJECT,aid))
  self.assertTrue(audit(self.assets,aid)["valid"])
 def test_04_broken_scanner_retries_fail_closed(self):
  aid=self.stage()
  enqueue(self.assets,self.db,aid,self.owner["user_id"])
  with patch("asset_security_v9._engine",return_value=("error","offline","test-only")):
   first=run_once(self.assets,self.db)
  self.assertEqual(first["status"],"queued")
  self.assertIsNone(download_asset(self.assets,self.o,PROJECT,aid))
  with self.assertRaises(PermissionError):decide(self.assets,self.db,self.r,aid,"approved")
  with sqlite3.connect(self.assets/"catalog.sqlite") as c:
   c.execute("UPDATE asset_scan_jobs SET next_run=0 WHERE asset_id=?",(aid,))
  with patch("asset_security_v9._engine",return_value=("error","offline","test-only")):
   second=run_once(self.assets,self.db)
  self.assertEqual(second["status"],"queued")
  with sqlite3.connect(self.assets/"catalog.sqlite") as c:
   c.execute("UPDATE asset_scan_jobs SET next_run=0 WHERE asset_id=?",(aid,))
  with patch("asset_security_v9._engine",return_value=("error","offline","test-only")):
   third=run_once(self.assets,self.db)
  self.assertEqual(third["status"],"failed")
  self.assertIsNone(download_asset(self.assets,self.o,PROJECT,aid))
 def test_05_infected_result_blocked_no_retry(self):
  aid=self.stage();enqueue(self.assets,self.db,aid,self.owner["user_id"])
  with patch("asset_security_v9._engine",return_value=("infected","signature","test-only")):
   result=run_once(self.assets,self.db)
  self.assertEqual(result["status"],"blocked")
  with self.assertRaises(PermissionError):decide(self.assets,self.db,self.r,aid,"approved")
 def test_06_editor_can_queue_owned_pending_file(self):
  aid=self.stage(self.editor["user_id"])
  self.assertEqual(enqueue(self.assets,self.db,aid,self.editor["user_id"])["status"],"queued")
  result=self.pass_scan(aid)
  self.assertEqual(result["status"],"done")
  self.assertTrue(review_inbox(self.assets,self.r)[0]["can_approve"])
 def test_07_viewer_cannot_enumerate_pending_work_or_scan(self):
  aid=self.stage()
  with self.assertRaises(PermissionError):enqueue(self.assets,self.db,aid,self.viewer["user_id"])
  with self.assertRaises(PermissionError):review_inbox(self.assets,self.v)
  self.assertEqual(scan_jobs(self.assets,self.v),[])
  self.assertIsNone(inert_text_preview(self.assets,self.v,aid))
 def test_08_escaped_preview_never_renders_untrusted_html(self):
  aid=self.stage();enqueue(self.assets,self.db,aid,self.owner["user_id"])
  self.pass_scan(aid)
  result=inert_text_preview(self.assets,self.r,aid)
  self.assertTrue(result["available"])
  self.assertIn("&lt;script&gt;",result["text"])
  self.assertNotIn("<script>",result["text"])
  self.assertEqual(result["preview_format"],"escaped_plain_text")
 def test_09_revoked_requester_job_blocked_not_cleared(self):
  aid=self.stage();enqueue(self.assets,self.db,aid,self.owner["user_id"])
  revoke(self.db,PROJECT,"owner","owner")
  result=run_once(self.assets,self.db)
  self.assertEqual(result["status"],"blocked")
  self.assertEqual(scan_jobs(self.assets,self.r)[0]["status"],"blocked")
 def test_10_duplicate_after_done_scans_already_clean(self):
  aid=self.stage();enqueue(self.assets,self.db,aid,self.owner["user_id"])
  self.pass_scan(aid)
  result=enqueue(self.assets,self.db,aid,self.owner["user_id"])
  self.assertEqual(result["status"],"already_clean")
 def test_11_no_preview_for_pdf_or_legacy_unscanned(self):
  aid=self.stage()
  self.assertFalse(inert_text_preview(self.assets,self.r,aid)["available"])
  with sqlite3.connect(self.assets/"catalog.sqlite") as c:
   c.execute("UPDATE asset_versions SET media_type='application/pdf' WHERE asset_id=?",(aid,))
  self.assertFalse(inert_text_preview(self.assets,self.r,aid)["available"])
 def test_12_stale_lease_result_must_not_update_new_lease(self):
  aid=self.stage();enqueue(self.assets,self.db,aid,self.owner["user_id"])
  job=claim_one(self.assets,now=1000)
  with sqlite3.connect(self.assets/"catalog.sqlite") as c:
   c.execute("UPDATE asset_scan_jobs SET leased_at=0 WHERE job_id=?",(job["job_id"],))
  newer=claim_one(self.assets,now=1200)
  self.assertNotEqual(job["lease_id"],newer["lease_id"])
  with self.assertRaises(RuntimeError):_finish(self.assets,job,"done","clean",now=1200)
 def test_13_unknown_or_removed_asset_not_exposed(self):
  aid=self.stage()
  self.assertIsNone(inert_text_preview(self.assets,self.r,"abcd"))
  self.assertEqual(scan_jobs(self.assets,self.r),[])
if __name__=="__main__":unittest.main()
