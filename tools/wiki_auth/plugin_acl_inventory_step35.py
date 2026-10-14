#!/usr/bin/env python3
"""Read-only inventory for native PukiWiki plugin data-access patterns.

This is a static triage report, NOT an assurance that all plugins are secure.
Re-run after a new PukiWiki plugin or version is deployed.
"""
import argparse,json,re
from datetime import datetime,timezone
from pathlib import Path

PATTERNS={
 "page_enumeration":r"get_existpages\s*\(|get_existfiles\s*\(",
 "raw_source_read":r"get_source\s*\(|file_get_contents\s*\(",
 "file_io":r"readfile\s*\(|fopen\s*\(",
 "recent_or_feed":r"RECENT_DIR|RecentChanges|recent\.dat|get_recent_files\s*\(",
 "attachment_backup_storage":r"ATTACH_DIR|UPLOAD_DIR|BACKUP_DIR|DIFF_DIR",
}
CONFIRMED_FIXED={"rss","ls2","map","yetlist","deleted"}
PRIOR_SCOPED={"list","backup","ref","attach","recent"}
def run(site):
    site=Path(site).resolve(strict=True)
    entries=[]
    plugins=list((site/"plugin").glob("*.inc.php"))
    for file in plugins:
        source=file.read_text(encoding="utf8",errors="replace")
        patterns=[name for name,regex in PATTERNS.items() if re.search(regex,source)]
        if not patterns:continue
        stem=file.stem.removesuffix(".inc")
        if stem in CONFIRMED_FIXED:label="scoped_acl_patch_step35"
        elif stem in PRIOR_SCOPED:label="scoped_acl_patch_prior"
        else:label="requires_review"
        entries.append({"plugin":file.name,"flags":patterns,"review_status":label,
                        "has_page_readability_gate":"is_page_readable" in source})
    return {"scan_utc":datetime.now(timezone.utc).isoformat(),
            "site":str(site),"plugins_scanned":len(plugins),
            "flagged_for_triage":len(entries),
            "confirmed_step35_http_rechecks":["rss","ls2","map","yetlist","deleted"],
            "full_legacy_plugin_audit_complete":False,
            "caution":"Matches indicate possible data paths, not verified vulnerabilities.",
            "items":sorted(entries,key=lambda x:x["plugin"])}
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--site",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    a=p.parse_args()
    output=run(a.site)
    if a.output.exists():p.error("Refusing to replace an existing report")
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(output,indent=2,ensure_ascii=False)+"\n")
    print(json.dumps({"scanned":output["plugins_scanned"],
        "flagged":output["flagged_for_triage"],
        "needs_review":sum(x["review_status"]=="requires_review" for x in output["items"])},
        ensure_ascii=False))
