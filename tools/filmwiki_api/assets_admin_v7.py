#!/usr/bin/env python3
"""Step34 synthetic asset import. Admin-only, no browser upload endpoint."""
import argparse,json
from pathlib import Path
from collab_auth import setup_session_lifecycle
from asset_catalog_v7 import setup_assets,import_asset,list_assets
ROOT=Path("/home/administrator/filmwiki-data")
AUTH=ROOT/"private/collaboration-accounts.sqlite"
ASSETS=ROOT/"private/assets-step34"
def main():
    p=argparse.ArgumentParser()
    p.add_argument("action",choices=("init","import"))
    p.add_argument("--source")
    p.add_argument("--owner-user-id")
    p.add_argument("--filename")
    p.add_argument("--mime",choices=("image/png","image/jpeg","image/webp",
                                     "application/pdf","video/mp4","text/plain"))
    p.add_argument("--visibility",choices=("project","private"))
    a=p.parse_args()
    setup_assets(ASSETS,ROOT)
    if a.action=="init":
        print(json.dumps({"assets_root":str(ASSETS),"upload_api":False}))
        return
    if not all((a.source,a.owner_user_id,a.filename,a.mime,a.visibility)):
        p.error("Import requires --source --owner-user-id --filename --mime --visibility")
    result=import_asset(ASSETS,AUTH,Path(a.source),"demo-editor",a.owner_user_id,
                        a.filename,a.mime,a.visibility)
    print(json.dumps(result,ensure_ascii=False))
if __name__=="__main__":main()
