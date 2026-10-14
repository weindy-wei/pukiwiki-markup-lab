#!/usr/bin/env python3
"""Reproducible private local FilmWiki FTS5 benchmark, synthetic data ONLY."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
import datetime as dt
import json
from pathlib import Path
import resource
import sqlite3
import statistics
import time
import urllib.request

from catalog_v2 import read_page
from catalog_v3 import read_indexed_page
from search_index import build_index,validate_index
from server_v3 import ProjectServer
from local_store import SCHEMA,stable_json

SHA="f"*64
PROJECT="synthetic-scale"
TOKEN="benchmark-token-"+("s"*48)
FIELDS=["镜头号","画面描述","帧序号","审核状态"]

def ensure_synthetic(db,rows):
    db=Path(db)
    if db.exists():
        with closing(sqlite3.connect(db.as_uri()+"?mode=ro",uri=True)) as c:
            count=c.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            source=c.execute("SELECT source_sha256 FROM imports").fetchone()[0]
            if count!=rows or source!=SHA:
                raise RuntimeError("Existing benchmark file incompatible; refuse overwrite")
        return False
    with closing(sqlite3.connect(db)) as con:
        con.executescript(SCHEMA)
        with con:
            con.execute("INSERT INTO imports VALUES(?,?,?,?,?)",
                 (SHA,"synthetic-scale-only.xlsx","0"*64,dt.datetime.now(dt.timezone.utc).isoformat(),
                  "filmwiki-import-preview"))
            con.execute("INSERT INTO sheets VALUES(?,?,?)",(SHA,1,"合成压力分镜"))
            con.executemany("INSERT INTO fields VALUES(?,?,?,?)",
                   [(SHA,1,j+1,name) for j,name in enumerate(FIELDS)])
            batch_records=[];batch_cells=[]
            for i in range(rows):
                rid=f"src_bench_{i:08d}"
                cells=[
                    f"SC_{i:08d}",
                    ("星舰科研计划 特殊稀有画面 " if i%4096==0 else "街区常见分镜 远景人物运动 ")
                        +"第"+str(i)+"帧",
                    i,
                    "待审核" if i%3==0 else "已通过"
                ]
                batch_records.append((rid,SHA,1,i+2,stable_json(cells[0])))
                batch_cells.extend((rid,j+1,stable_json(v),0) for j,v in enumerate(cells))
                if len(batch_records)>=2000:
                    con.executemany("INSERT INTO records VALUES(?,?,?,?,?)",batch_records)
                    con.executemany("INSERT INTO cells VALUES(?,?,?,?)",batch_cells)
                    batch_records.clear();batch_cells.clear()
            if batch_records:
                con.executemany("INSERT INTO records VALUES(?,?,?,?,?)",batch_records)
                con.executemany("INSERT INTO cells VALUES(?,?,?,?)",batch_cells)
        if con.execute("PRAGMA integrity_check").fetchone()[0]!="ok":
            raise RuntimeError("Synthetic database failed integrity check")
    return True


def pct(values,p):
    seq=sorted(values)
    if not seq:return None
    return round(seq[min(len(seq)-1,max(0,int((len(seq)-1)*p)))],3)


def run_benchmark(root,rows=100000,clients=8,requests=64):
    root=Path(root).resolve(strict=True)
    if rows<1000 or rows>200000 or clients<1 or clients>16 or requests<1 or requests>400:
        raise ValueError("Invalid bounded benchmark workload")
    folder=root/"bench"
    folder.mkdir(mode=0o700,exist_ok=True)
    index_folder=folder/"indexes"
    index_folder.mkdir(mode=0o700,exist_ok=True)
    db=folder/("synthetic-"+str(rows)+".sqlite")
    index=index_folder/(PROJECT+".search.sqlite")
    t0=time.perf_counter()
    created=ensure_synthetic(db,rows)
    fixture_sec=time.perf_counter()-t0
    t0=time.perf_counter()
    if not index.exists():
        report=build_index(db,SHA,PROJECT,index,root)
        index_sec=time.perf_counter()-t0
    else:
        validate_index(db,SHA,PROJECT,index)
        index_sec=0
    stat=db.stat()
    project={"id":PROJECT,"title":"Synthetic benchmark","db":db,"source_sha":SHA,
             "index_db":index,"source_stat":(stat.st_size,stat.st_mtime_ns),
             "manifest":{
                "source":{"filename":"synthetic-only","sha256":SHA},
                "sheets":[{"sheet_index":1,"name":"合成压力分镜","record_count":rows,
                           "fields":[{"index":n+1,"name":f} for n,f in enumerate(FIELDS)]}]}}
    # Compute correctness and warm median/p95 per query, before concurrent HTTP.
    cases={
        "rare_fts":{"q":"星舰科研计划"},
        "common_fallback":{"q":"街区常见分镜"},
        "short_chinese":{"q":"街区"},
        "exact_status":{"field":4,"value":"待审核"},
        "numeric_tail":{"field":3,"value":str(rows-5000),"kind":"number","op":"ge"},
    }
    outcome=[]
    for label,kwargs in cases.items():
        legacy=None
        if "kind" not in kwargs:
            legacy=read_page(project,TOKEN,1,limit=50,**kwargs)
        indexed=read_indexed_page(project,TOKEN,1,limit=50,**kwargs)
        if legacy and [x["record_id"] for x in indexed["records"]]!=[x["record_id"] for x in legacy["records"]]:
            raise RuntimeError("Mismatch between old and indexed query: "+label)
        old_times=[];new_times=[]
        for k in range(12):
            if legacy is not None:
                t=time.perf_counter();read_page(project,TOKEN,1,limit=50,**kwargs)
                old_times.append((time.perf_counter()-t)*1000)
            t=time.perf_counter();read_indexed_page(project,TOKEN,1,limit=50,**kwargs)
            new_times.append((time.perf_counter()-t)*1000)
        outcome.append({"case":label,"backend":indexed["search_backend"],
            "first_page_rows":len(indexed["records"]),
            "old_median_ms":round(statistics.median(old_times),3) if old_times else None,
            "v3_median_ms":round(statistics.median(new_times),3),
            "v3_p95_ms":pct(new_times,0.95)})
    server=ProjectServer(("127.0.0.1",0),{PROJECT:project},TOKEN)
    import threading
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    port=server.server_port
    api_paths=[
        f"/v2/projects/{PROJECT}/records?sheet=1&q=星舰科研计划",
        f"/v2/projects/{PROJECT}/records?sheet=1&q=街区常见分镜",
        f"/v2/projects/{PROJECT}/records?sheet=1&q=街区",
        f"/v2/projects/{PROJECT}/records?sheet=1&field=4&value=待审核",
        f"/v2/projects/{PROJECT}/records?sheet=1&kind=number&op=ge&field=3&value={rows-5000}",
        f"/v2/projects/{PROJECT}/manifest",
    ]
    from urllib.parse import quote
    paths=[quote(x,safe="/?=&") for x in api_paths]
    def request(index):
        path=paths[index%len(paths)]
        req=urllib.request.Request(
            f"http://127.0.0.1:{port}"+path,
            headers={"Authorization":"Bearer "+TOKEN,"Origin":"http://127.0.0.1:8080"}
        )
        tick=time.perf_counter()
        with urllib.request.urlopen(req,timeout=20) as response:
            body=json.load(response)
            if response.status!=200:raise RuntimeError("Unexpected HTTP status")
            if "records" in body and len(body["records"])>50:raise RuntimeError("Page > 50")
        return (time.perf_counter()-tick)*1000
    latencies=[];errors=[]
    try:
        with ThreadPoolExecutor(max_workers=clients) as pool:
            futures=[pool.submit(request,i) for i in range(requests)]
            for future in as_completed(futures):
                try:latencies.append(future.result())
                except Exception as exc:errors.append(type(exc).__name__)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
    report={
        "format":"filmwiki-step27-synthetic-benchmark","timestamp_utc":dt.datetime.now(dt.timezone.utc).isoformat(),
        "rows":rows,"indexed_cells":rows*len(FIELDS),
        "clients":clients,"requests":requests,"errors":errors,
        "fixture_created":created,"fixture_seconds":round(fixture_sec,2),
        "index_build_seconds":round(index_sec,2),
        "source_db_bytes":db.stat().st_size,"index_bytes":index.stat().st_size,
        "queries":outcome,
        "http_latency_p50_ms":pct(latencies,0.5),
        "http_latency_p95_ms":pct(latencies,0.95),
        "max_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "notes":"Owner-local synthetic only; localhost concurrent HTTP. No million-record or network-scale claim."
    }
    dest=folder/("step27-benchmark-"+str(rows)+".json")
    dest.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if errors:raise RuntimeError("Concurrent benchmark had errors: "+str(len(errors)))
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-root",type=Path,required=True)
    p.add_argument("--rows",type=int,default=100000)
    p.add_argument("--clients",type=int,default=8)
    p.add_argument("--requests",type=int,default=64)
    a=p.parse_args()
    print(json.dumps(run_benchmark(a.data_root,a.rows,a.clients,a.requests),
                     ensure_ascii=False,indent=2))


if __name__=="__main__":main()
