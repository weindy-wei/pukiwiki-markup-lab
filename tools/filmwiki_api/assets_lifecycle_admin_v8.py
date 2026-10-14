#!/usr/bin/env python3
"""Step35 owner-local synthetic asset lifecycle administrator CLI.

No user-provided file content is accepted over HTTP. Review permission is
checked against real active project membership on every operation.
"""
import argparse,json,sqlite3
from pathlib import Path
from asset_lifecycle_v8 import (setup_lifecycle,stage_new,stage_revision,review,
                                trash_restore,link,audit,history)
ROOT=Path("/home/administrator/filmwiki-data")
IDENTITY=ROOT/"private/collaboration-accounts.sqlite"
ASSETS=ROOT/"private/assets-step34"
EDITOR=ROOT/"editing/step31-collaboration.sqlite"
def main():
    p=argparse.ArgumentParser(description=__doc__)
    modes=p.add_subparsers(dest="action",required=True)
    modes.add_parser("init")
    for x in ("stage","stage-revision","scan","approve","reject","soft-delete",
              "restore","link","unlink","audit","history"):
        sub=modes.add_parser(x)
        if x!="audit":
            sub.add_argument("--actor",required=True,help="Existing active user ID")
        if x in ("stage","stage-revision"):
            sub.add_argument("--source",required=True)
            sub.add_argument("--mime",required=True)
        if x=="stage":
            sub.add_argument("--name",required=True)
            sub.add_argument("--visibility",choices=("private","project"),required=True)
        if x in ("stage-revision","scan","approve","reject","soft-delete","restore","link","unlink"):
            sub.add_argument("--asset",required=True)
        if x in ("link","unlink"):
            sub.add_argument("--record",required=True)
            sub.add_argument("--relation",choices=("illustrates","reference","source"),
                             required=True)
        if x in ("audit","history"):
            sub.add_argument("--asset",required=(x=="history"))
    a=p.parse_args()
    from asset_security_v9 import setup_security,scan_pending
    setup_security(ASSETS,ROOT)
    if a.action=="init":out={"state":"ready","assets_root":str(ASSETS)}
    elif a.action=="stage":
        out=stage_new(ASSETS,IDENTITY,Path(a.source),"demo-editor",a.actor,
                      a.name,a.mime,a.visibility)
    elif a.action=="stage-revision":
        out=stage_revision(ASSETS,IDENTITY,Path(a.source),a.asset,a.actor,a.mime)
    elif a.action=="scan":
        out=scan_pending(ASSETS,IDENTITY,a.asset,a.actor)
    elif a.action in ("approve","reject"):
        out=review(ASSETS,IDENTITY,a.asset,a.actor,
                   "approved" if a.action=="approve" else "rejected")
    elif a.action in ("soft-delete","restore"):
        out=trash_restore(ASSETS,IDENTITY,a.asset,a.actor,
                          restore=(a.action=="restore"))
    elif a.action in ("link","unlink"):
        out=link(ASSETS,IDENTITY,EDITOR,a.asset,a.actor,
                 a.record,a.relation,remove=(a.action=="unlink"))
    elif a.action=="history":
        from asset_lifecycle_v8 import _actor
        _actor(IDENTITY,a.actor,"demo-editor")
        out=history(ASSETS,a.asset)
    else:out=audit(ASSETS,a.asset)
    print(json.dumps(out,ensure_ascii=False,sort_keys=True))
if __name__=="__main__":main()
