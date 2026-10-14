#!/usr/bin/env python3
"""Step37 single-process scan queue poller. Suitable for systemd oneshot timer."""
import argparse,json
from pathlib import Path
from asset_queue_v10 import setup_queue,run_once
ROOT=Path("/home/administrator/filmwiki-data")
def main():
 p=argparse.ArgumentParser()
 p.add_argument("--limit",type=int,default=2)
 a=p.parse_args()
 if not 1<=a.limit<=3:p.error("limit 1..3")
 root=ROOT/"private/assets-step34"
 setup_queue(root,ROOT)
 for _ in range(a.limit):
  result=run_once(root,ROOT/"private/collaboration-accounts.sqlite")
  if result is None:break
  print(json.dumps(result,sort_keys=True,ensure_ascii=False),flush=True)
if __name__=="__main__":main()
