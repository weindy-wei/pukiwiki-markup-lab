"""FilmWiki Step26 search: read-only FTS5 sidecar + typed field indices.

The source database is never migrated. A verified immutable sidecar is attached
read-only. Short 1–2 Unicode-codepoint searches retain the original v2 scan path.
"""
from contextlib import closing
import json
import re
import sqlite3

from server import open_readonly
from catalog_v2 import (
    MAX_PAGE, MAX_SEARCH, MAX_EXACT_VALUE, _decode_cursor, _encode_cursor,
    _search_context, _materialize
)
from search_index import validate_index

OPS={"eq","lt","le","gt","ge"}

def read_indexed_page(project,secret,sheet,limit=50,cursor=None,q="",field=None,
                      value=None,op="eq",kind="string"):
    if type(sheet) is not int or not 1<=sheet<=100 or type(limit) is not int or not 1<=limit<=MAX_PAGE:
        raise ValueError("Invalid sheet/page limit")
    if not isinstance(q,str) or len(q)>MAX_SEARCH or "\x00" in q:
        raise ValueError("Invalid search")
    if (field is None)!=(value is None):
        raise ValueError("Field and value must be paired")
    if kind not in ("string","number") or op not in OPS:
        raise ValueError("Invalid typed filter")
    if field is None and (op!="eq" or kind!="string"):
        raise ValueError("Operator requires a field")
    if field is not None and (type(field) is not int or not 1<=field<=256 or
                              not isinstance(value,str) or len(value)>MAX_EXACT_VALUE or "\x00" in value):
        raise ValueError("Invalid field")
    if kind=="string" and op!="eq":
        raise ValueError("Range comparisons require numeric fields")
    number=None
    if kind=="number":
        if not re.fullmatch(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d{1,3})?",value):
            raise ValueError("Invalid numeric input")
        number=float(value)
        if not -1e300<number<1e300:raise ValueError("Numeric bound outside supported range")
    # Keep v2 cursor signatures for existing string equality/page queries.
    context_value=value if kind=="string" and op=="eq" else [kind,op,value]
    context=_search_context(project["id"],sheet,q,field,context_value)
    after=1 if cursor is None else _decode_cursor(secret,cursor,context)
    index=project.get("index_db")
    if not index:
        raise ValueError("Project has no verified sidecar index")
    with closing(open_readonly(project["db"])) as con:
        con.execute("ATTACH DATABASE ? AS idx",(index.resolve().as_uri()+"?mode=ro",))
        columns=con.execute(
            "SELECT column_index,name FROM fields WHERE source_sha256=? AND sheet_index=? ORDER BY column_index",
            (project["source_sha"],sheet)).fetchall()
        check=con.execute("SELECT name FROM sheets WHERE source_sha256=? AND sheet_index=?",
                          (project["source_sha"],sheet)).fetchone()
        if check is None:raise KeyError("Unknown sheet")
        if field is not None and field not in {col["column_index"] for col in columns}:
            raise ValueError("Unknown column")
        params=[project["source_sha"],sheet,after]
        sql=("SELECT r.record_id,r.source_row,r.shot_number_json FROM records r "
             "WHERE r.source_sha256=? AND r.sheet_index=? AND r.source_row>?")
        mode="indexed-keyset"
        if q and len(q)>=3:
            # Cheap capped index probe. Common terms hit thousands of cells:
            # materializing all MATCH results can be far slower than walking
            # indexed source rows and checking the first matching page.
            phrase='"'+q.replace('"','""')+'"'
            sample=[r[0] for r in con.execute(
                "SELECT rowid FROM idx.cell_fts WHERE cell_fts MATCH ? LIMIT 257",
                (phrase,)
            )]
            if len(sample)>=257:
                sql+=(
                    " AND EXISTS (SELECT 1 FROM cells c WHERE c.record_id=r.record_id "
                    "AND instr(c.value_json,?)>0)"
                )
                params.append(q)
                mode="fts5-high-frequency-scan"
            elif sample:
                # Only 1–256 candidate cells; look them up by numeric doc id,
                # then verify exact, case-sensitive substring semantics.
                marks=",".join("?" for _ in sample)
                sql+=(
                    " AND r.record_id IN (SELECT d.record_id FROM idx.cell_docs d "
                    "WHERE d.doc_id IN ("+marks+") "
                    "AND d.sheet_index=? AND instr(d.value_json,?)>0)"
                )
                params.extend(sample+[sheet,q])
                mode="fts5-trigram"
            else:
                sql+=" AND 0"
                mode="fts5-trigram"
        elif q:
            # Trigram cannot index sub-3-character terms (including Chinese).
            sql+=(
                " AND EXISTS (SELECT 1 FROM cells c WHERE c.record_id=r.record_id "
                "AND instr(c.value_json,?)>0)"
            )
            params.append(q)
            mode="short-query-scan"
        if field is not None:
            if kind=="string":
                sql+=(
                    " AND r.record_id IN (SELECT e.record_id FROM idx.exact_fields e "
                    "WHERE e.sheet_index=? AND e.column_index=? "
                    "AND e.value_type='string' AND e.value_json=?)"
                )
                params.extend([sheet,field,json.dumps(value,ensure_ascii=False,separators=(",",":"))])
            else:
                sql+=(
                    " AND r.record_id IN (SELECT e.record_id FROM idx.exact_fields e "
                    "WHERE e.sheet_index=? AND e.column_index=? "
                    "AND e.value_type='number' AND e.number_value "+{
                      "eq":"=","lt":"<","le":"<=","gt":">","ge":">="
                    }[op]+" ?)"
                )
                params.extend([sheet,field,number])
            mode += "+typed-field"
        sql+=" ORDER BY r.source_row LIMIT ?"
        params.append(limit+1)
        found=con.execute(sql,params).fetchall()
        selected=found[:limit]
        records,assets=_materialize(con,selected,columns,project["source_sha"])
        next_cursor=(_encode_cursor(secret,{"context":context,"last":selected[-1]["source_row"]})
                     if len(found)>limit and selected else None)
        return {
            "format":"filmwiki-record-page","version":2,
            "project_id":project["id"],"sheet_index":sheet,"name":check["name"],
            "query":q,"filter_field":field,"filter_value":value,
            "limit":limit,"records":records,"asset_references":assets,
            "next_cursor":next_cursor,"has_more":len(found)>limit,
            "count":len(records),"search_backend":mode,
            "note":"FTS5 trigrams probe 3+ Unicode terms; highly frequent and short terms use a bounded row-scan fallback. Numeric comparisons require actual JSON number cells."
        }


def attach_validated_indices(projects,index_root,data_root):
    root=__import__("pathlib").Path(data_root).resolve(strict=True)
    folder=__import__("pathlib").Path(index_root).resolve(strict=True)
    if not folder.is_dir() or not folder.is_relative_to(root):
        raise ValueError("Search index folder must be within private local data root")
    for project in projects.values():
        filename=folder/(project["id"]+".search.sqlite")
        candidate=filename.resolve(strict=True)
        if not candidate.is_relative_to(folder) or candidate.suffix!=".sqlite":
            raise ValueError("Unsafe index path")
        validate_index(project["db"],project["source_sha"],project["id"],candidate)
        project["index_db"]=candidate
    return projects
