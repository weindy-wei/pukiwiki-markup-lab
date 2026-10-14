# FilmWiki Step 27 · Controlled Index Rebuild & Synthetic Search Load Test

This remains an **owner-local, read-only FilmWiki**. The canonical source of truth is the WSL ext4 SQLite in `/home/administrator/filmwiki-data/staging`; the separate FTS5 and typed-field index files in `indexes` are disposable derived private data, not authoritative records. The NAS SMB volume is for checked backups and logs, never live SQLite.

## Hourly index check
- `tools/filmwiki_api/index_reconciler.py`: validate the **allowlisted** project registry and check index/source fingerprints. **Dry-run by default**. With `--apply`, build only missing/stale sidecars, retaining an immutable prior index in `indexes/history`. A Linux `flock` blocks concurrent runs. The canonical project databases are opened read-only.
- `tools/filmwiki_api/index_reconcile_supervisor.py`: intended for an owner-controlled local systemd oneshot. It runs the worker with the unprivileged WSL `administrator` account. The root supervisor restarts the API **only when** at least one index is rebuilt successfully and no project has failed. After restart, it checks project lists and one authenticated indexed page **for every registered project**. It never prints or archives bearer secrets.
- `ops/filmwiki/filmwiki-index-reconcile.{service,timer}`: Linux systemd checks approximately hourly, beginning 15 minutes after boot. No build and no service restart on a no-change run. The installed service's writable scope is limited to private `indexes` and `reports`.
- The private owner registry, existing source DB files, and source SHA identifiers must be maintained as an immutable versioned snapshot contract. This is **not** a general multi-user live-editing or transactional indexer. Content edits in place remain out of scope and are rejected by the v3 source-stat check until a reviewed new index is built and the API is reloaded.
- A crash or build error must never silently serve stale search results: if the source changes, the production query layer fails closed. Prior index hardlinks permit recovery of an earlier compatible snapshot, but **must not** be restored against an incompatible revised canonical source.

### Operator commands (local WSL)

Dry-run:
```bash
ROOT=/home/administrator/filmwiki-data
python3 "$ROOT/services/index_reconciler.py" --data-root "$ROOT" \
  --projects-file "$ROOT/private/projects.json" --index-root "$ROOT/indexes"
```

Trigger a controlled systemd run:
```bash
sudo systemctl start filmwiki-index-reconcile.service
systemctl status filmwiki-index-reconcile.service --no-pager
systemctl list-timers filmwiki-index-reconcile.timer
```
Suspend hourly checks:
```bash
sudo systemctl disable --now filmwiki-index-reconcile.timer
```

## Synthetic load test
`bench_search.py` generates a completely artificial 100,000-row, 400,000-cell source SQLite on local WSL ext4 under `bench/` and builds a separate FTS5 sidecar. The benchmark database is **not** registered with the live FilmWiki API; the benchmark launches its own ephemeral `127.0.0.1` HTTP server and exercises 8 concurrent clients over 64 requests. Benchmark data, result JSON, service report and unit backups are recorded locally / in NAS OperationArchive; **never** upload any real workbook or secret.

Observed on this one synthetic dataset:
- Sidecar initial build: about 3 seconds; main source DB about 56.7 MB, index about 122.9 MB (size depends on fixture).
- Rare multi-character Chinese text median old ~71.5 ms vs FTS5 ~38 ms, not a universal speedup.
- A high-frequency exact status field originally regressed to ~13.5 ms. Changing the correlated query plan reduced it to around 0.77 ms in the same dataset. Indexed numeric range from ~19.8 ms to ~1.2 ms.
- Eight concurrent local clients issued 64 HTTP requests with zero errors. The raw p95 response time is higher than the single-query test under concurrency. Do not present this as million-row readiness, real-world multi-user throughput or an SLA.
- Query-plan improvement in Step27 is additive to v3: legacy `/v1` and `/v2` wire formats, Grid/Thumbnail/Detail and signed cursors remain compatible.

## Safety and rollback
- **No live SQLite mutation, no automatic PukiWiki page changes, no external data transmission.**
- Original root-owned production API unit and prior static UI have independent backups under the monthly NAS-first OperationArchive policy.
- To roll back just Step27 scheduling, disable `filmwiki-index-reconcile.timer`. The v3 API and Step26 index files will continue to operate.
- To roll back query-plan optimization, restore the previous `catalog_v3.py` from a verified Git revision and restart the same service. No source data migration is necessary.
- If validation fails, leave the API fail-closed and inspect the private JSON report before rerunning. Do not allow a failed index check to become a silent success.
