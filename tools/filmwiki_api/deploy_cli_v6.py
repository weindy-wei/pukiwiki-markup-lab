#!/usr/bin/env python3
"""FilmWiki Step30 owner-local synthetic release deployment CLI.

PLAN can run unprivileged. ACTIVATE/ROLLBACK/RECOVER require local root, hold
the index reconciler lock, stop the read-only API, and use a durable journal.
"""
import argparse
import json
from pathlib import Path
from deploy_v6 import plan,activate,rollback,recover

ROOT=Path("/home/administrator/filmwiki-data")


def main():
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest="action",required=True)
    one=sub.add_parser("plan",help="Validate an approved release and build canary index")
    one.add_argument("--release-id",required=True)
    one=sub.add_parser("activate",help="Maintenance-stop and activate validated release")
    one.add_argument("--release-id",required=True)
    for label in ("rollback","recover"):
        one=sub.add_parser(label,help="Restore an earlier registry and matching index")
        one.add_argument("--transaction",required=True)
    args=p.parse_args()
    if args.action=="plan":result=plan(ROOT,args.release_id)
    elif args.action=="activate":result=activate(ROOT,args.release_id)
    elif args.action=="rollback":result=rollback(ROOT,args.transaction)
    else:result=recover(ROOT,args.transaction)
    print(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True))


if __name__=="__main__":main()
