#!/usr/bin/env python3
"""Owner-local account provisioning CLI. No HTTP registration, secrets never in argv."""
import argparse,getpass,json
from pathlib import Path
import sqlite3
from collab_auth import create_store,create_user,grant,revoke,reset_password,disable,list_active_members
from editor_store import get_meta

ROOT=Path("/home/administrator/filmwiki-data")
AUTH=ROOT/"private/collaboration-accounts.sqlite"
EDITOR=ROOT/"editing/step31-collaboration.sqlite"
PROJECT="demo-editor"

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    actions=parser.add_subparsers(dest="action",required=True)
    actions.add_parser("init")
    u=actions.add_parser("add-user");u.add_argument("username")
    g=actions.add_parser("grant");g.add_argument("username");g.add_argument("role",choices=("viewer","editor","reviewer","publisher","owner"))
    rev=actions.add_parser("revoke");rev.add_argument("username");rev.add_argument("role",choices=("viewer","editor","reviewer","publisher","owner"))
    reset=actions.add_parser("reset-password");reset.add_argument("username")
    dis=actions.add_parser("disable");dis.add_argument("username")
    actions.add_parser("members")
    opts=parser.parse_args()
    if opts.action=="init":
        print(json.dumps({"db":create_store(AUTH,ROOT),"secret_exported":False}))
    elif opts.action=="add-user":
        a=getpass.getpass("New password (12-128 chars): ")
        b=getpass.getpass("Confirm password: ")
        if a!=b:parser.error("Passwords differ")
        print(json.dumps(create_user(AUTH,opts.username,a)))
    elif opts.action=="grant":
        if get_meta(EDITOR)["project_id"]!=PROJECT:raise SystemExit("Wrong editor project")
        uid=grant(AUTH,PROJECT,opts.username,opts.role)
        if opts.role in ("editor","owner"):
            with sqlite3.connect(EDITOR) as con:
                con.execute("INSERT OR IGNORE INTO workflow_role_grants "
                            "(project_id,actor,role) VALUES (?,?,?)",(PROJECT,uid,"editor"))
        print(json.dumps({"username":opts.username,"role":opts.role,"project":PROJECT}))
    elif opts.action=="revoke":
        uid=revoke(AUTH,PROJECT,opts.username,opts.role)
        if opts.role in ("editor","owner"):
            current=list_active_members(AUTH,PROJECT)
            remaining={m["role"] for m in current if m["user_id"]==uid}
            if not remaining.intersection({"editor","owner"}):
                with sqlite3.connect(EDITOR) as con:
                    con.execute("DELETE FROM workflow_role_grants "
                                "WHERE project_id=? AND actor=? AND role='editor'",
                                (PROJECT,uid))
        print(json.dumps({"username":opts.username,"revoked":opts.role,"sessions_revoked":True}))
    elif opts.action=="reset-password":
        a=getpass.getpass("New password (12-128 chars): ")
        b=getpass.getpass("Confirm password: ")
        if a!=b:parser.error("Passwords differ")
        reset_password(AUTH,opts.username,a)
        print(json.dumps({"username":opts.username,"password_reset":True,"sessions_revoked":True}))
    elif opts.action=="disable":
        uid=disable(AUTH,opts.username)
        with sqlite3.connect(EDITOR) as con:
            con.execute("DELETE FROM workflow_role_grants WHERE actor=?",(uid,))
        print(json.dumps({"username":opts.username,"disabled":True}))
    else:
        print(json.dumps({"project":PROJECT,"members":list_active_members(AUTH,PROJECT)},
                         ensure_ascii=False,indent=2))

if __name__=="__main__":main()
