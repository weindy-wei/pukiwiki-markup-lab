"""Isolated supervisor logic tests, never invokes real systemctl."""
import json
from pathlib import Path
import sys
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"tools/filmwiki_api"))
import index_reconcile_supervisor as sup

class SupervisorTests(unittest.TestCase):
    @mock.patch.object(sup.pwd,"getpwnam")
    @mock.patch.object(sup.subprocess,"run")
    def test_no_change_never_restarts(self,run,user):
        user.return_value=mock.Mock(pw_uid=1000,pw_gid=1000)
        run.return_value=mock.Mock(returncode=0,stdout=json.dumps({
            "failures":[],"restart_required":False,"mode":"apply","projects":2,
            "changed":[],"unchanged":["alpha","beta"]
        }))
        result=sup.one_pass(apply=True,restart=True)
        self.assertFalse(result["restart_required"])
        self.assertEqual(run.call_count,1)
        self.assertEqual(run.call_args.kwargs["user"],1000)
        self.assertEqual(run.call_args.kwargs["extra_groups"],[])

    @mock.patch.object(sup,"verify_live",return_value=2)
    @mock.patch.object(sup.pwd,"getpwnam")
    @mock.patch.object(sup.subprocess,"run")
    def test_modified_index_triggers_restart_and_http_verification(self,run,user,verify):
        user.return_value=mock.Mock(pw_uid=1000,pw_gid=1000)
        worker=mock.Mock(returncode=0,stdout=json.dumps({
            "failures":[],"restart_required":True,"mode":"apply","projects":2,
            "changed":[{"project":"alpha","status":"rebuilt"}]
        }))
        systemd=mock.Mock(returncode=0)
        run.side_effect=[worker,systemd]
        result=sup.one_pass(apply=True,restart=True)
        self.assertEqual(run.call_count,2)
        self.assertEqual(run.call_args.args[0][:2],["/usr/bin/systemctl","restart"])
        self.assertEqual(result["reloaded_projects"],2)
        verify.assert_called_once_with()

    @mock.patch.object(sup.pwd,"getpwnam")
    @mock.patch.object(sup.subprocess,"run")
    def test_worker_failure_does_not_restart_service(self,run,user):
        user.return_value=mock.Mock(pw_uid=1000,pw_gid=1000)
        run.return_value=mock.Mock(returncode=2,stdout="")
        with self.assertRaises(RuntimeError):
            sup.one_pass(apply=True)
        self.assertEqual(run.call_count,1)

    @mock.patch.object(sup.pwd,"getpwnam")
    @mock.patch.object(sup.subprocess,"run")
    def test_dry_run_cannot_restart(self,run,user):
        user.return_value=mock.Mock(pw_uid=1000,pw_gid=1000)
        run.return_value=mock.Mock(returncode=0,stdout=json.dumps({
            "failures":[],"restart_required":False,"mode":"dry_run","projects":2,
            "changed":[{"project":"alpha","status":"needs_rebuild"}]
        }))
        sup.one_pass(apply=False)
        self.assertIn("--report-file",run.call_args.args[0])
        self.assertNotIn("--apply",run.call_args.args[0])
        self.assertEqual(run.call_count,1)

if __name__=="__main__":unittest.main()
