#!/usr/bin/env python3
"""Local operator CLI to stage, review, and seal versioned FilmWiki releases.

NEVER updates live read-only API project registry or original imported data.
"""
import argparse
import json
from pathlib import Path
import uuid

from release_v5 import prepare_release,approve_release,publish_release,inspect_release

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("prepare","approve","publish","verify"))
    parser.add_argument("--data-root",type=Path,required=True)
    parser.add_argument("--project-id",default="demo-editor")
    parser.add_argument("--release-id",required=True)
    parser.add_argument("--db",type=Path)
    args=parser.parse_args()
    root=args.data_root.resolve(strict=True)
    release_root=root/"releases"
    if args.action=="prepare":
        if args.db is None:parser.error("--db is required for prepare")
        result=prepare_release(args.db,release_root,args.project_id,args.release_id)
    elif args.action=="approve":
        result=approve_release(release_root,args.release_id)
    elif args.action=="publish":
        result=publish_release(release_root,args.release_id)
    else:
        result=inspect_release(release_root,args.release_id)
    print(json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2))

if __name__=="__main__":main()
