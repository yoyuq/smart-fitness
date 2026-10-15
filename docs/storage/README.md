# Storage deployment and recovery

This package keeps SQLite as a local option and adds PostgreSQL, Alembic and private COS media. It does not automatically switch an existing installation. IDs and existing API shapes remain; media URLs now require ownership and expire after five minutes. The monitoring page uses the login token from `/app`.

## Fresh isolated deployment

Use Python 3.11, or Docker Compose. Copy `deploy/storage.env.example` to a private file outside Git. Set independent private database/JWT values (JWT at least 32 random characters). Keep `MEDIA_BACKEND=local` until a private COS bucket and credentials are available. Never commit the resulting environment file.

From the repository root (replace `PRIVATE_ENV` with that file):

```sh
docker compose --env-file PRIVATE_ENV -f deploy/storage-compose.yml build
docker compose --env-file PRIVATE_ENV -f deploy/storage-compose.yml up -d postgres
docker compose --env-file PRIVATE_ENV -f deploy/storage-compose.yml run --rm backend python -m storage upgrade
docker compose --env-file PRIVATE_ENV -f deploy/storage-compose.yml run --rm backend python -m storage doctor
docker compose --env-file PRIVATE_ENV -f deploy/storage-compose.yml up -d backend
```

The API binds loopback 8080; PostgreSQL has no published port. Put an existing HTTPS reverse proxy in front if remote access is needed. Volumes persist DB and local cache. Do not run `down -v` against data you need. Limits total about 1.8 GiB; a 2 GiB server also hosts the OS, so measure available RAM and model inference before enabling pose warmup/AI/MQTT. These optional processes are disabled in this storage-only Compose example; enable/configure them deliberately for actual training. COS and language model credentials are separate.

Without Docker, install `backend/requirements.txt` into a Python 3.11 venv. Set `DATABASE_URL=postgresql+psycopg://...` (percent-encode credentials), or `DB_ENGINE=postgresql` plus `POSTGRES_HOST/PORT/DB/USER/PASSWORD`. `DATABASE_URL` wins. SQLite uses `DB_PATH`, then `FITNESS_DB_PATH`, then `backend/fitness.db`. All API, jobs, MCP and training entrypoints share this configuration. In `backend`, run `python -m storage upgrade`, `python -m storage doctor`, then `python -m uvicorn main:app --host 127.0.0.1 --port 8080 --no-access-log`. Production requires `ENV=production` and a private `JWT_SECRET`; `MEDIA_SIGNING_SECRET` may be separately configured. Do not log signed URL query strings at the proxy.

## Existing SQLite migration

1. Inventory both `backend/fitness.db` and root `fitness_v2.db`. Do not assume the second file is empty. Pause **all** writers: API, training, MQTT bridge, AI scheduler, MCP and scripts. Confirm every old process/container has exited. Media shutdown explicitly fails if upload I/O remains active; cancellation is not proof that a worker stopped. Keep a copy of old configuration and media root.
2. From `backend`, `python -m storage snapshot --source OLD_DB --output NEW_BACKUP` for each source. Online SQLite backup includes committed WAL. Use new destinations; never copy a live `.db` file without its WAL.
3. With the empty isolated PostgreSQL target selected, run `python -m storage upgrade`, then:

```sh
python -m storage classify --source BACKUP_MAIN --source BACKUP_SECOND --report NEW_CLASSIFICATION_JSON
python -m storage migrate --source BACKUP_MAIN --source BACKUP_SECOND --writers-stopped --report NEW_MIGRATION_JSON
```

Unknown columns/tables, conflicting IDs/content, nonempty target and invalid relations block migration. Reports contain counts/hashes, not row contents. The trial found 7,374 pre-existing orphan relationships in the supplied personal snapshots. Migration rejects these by default. Investigate them before production; `--allow-legacy-orphans` explicitly preserves/reports them without inventing parent sessions or deleting rows. The isolated rehearsal used that option; it is not a production approval.

4. Preserve the full old media directory. Dry-run `python -m storage media-import --source-root OLD_MEDIA_ROOT --report NEW_MEDIA_REPORT`. Review unowned/tombstoned counts. Repeat with `--apply` and a new report to catalog owned JPEG/PNG assets; existing identical keys are idempotent. With COS configured, this queues uploads; `python -m storage retry-media --limit 100` processes a bounded batch and exits 2 while unresolved states remain. The background pump resumes pending work after restart. `media-status` groups durable states. Nine total SDK attempts are allowed per upload/delete object; exhausted/missing cache needs diagnosis, not an endless reset loop.
5. Verify `/health`, login, history, plans/body metrics, a new training session, private clip playback and AI frame loading against the **new** storage. Stop and restart, then repeat. Keep old snapshots untouched. Reopen writers only after successful verification. Do not silently point back at a stale SQLite file after PostgreSQL has accepted new writes.

## COS media

`MEDIA_DATA_ROOT` is a persistent writable cache. `COS_REGION/BUCKET/SECRET_ID/SECRET_KEY` select a private HTTPS bucket; temporary credentials may use `COS_SESSION_TOKEN`. Restrict credentials to the application object prefixes. `python -m storage doctor --probe-cos` performs a real temporary put/get/sign/delete and requires explicit configured credentials; plain `doctor` does not contact COS. It checks required table/sequence privileges and rolls back a real database INSERT/UPDATE/DELETE probe; no test row remains. A passing SDK contract test is not a live bucket test.

Stable `rep_frames/...` and `rep_clips/...` keys replace machine paths. New camera writes first stage durable local bytes, then enter the upload outbox; cloud I/O does not block ingestion. Restart recovers complete staging files. An incomplete staging file becomes a reported terminal failure after a grace period. Checksums verify cached/downloaded data. AI tools materialize cloud frames into the same cache. Changing the cache root still permits ownership-checked reads from the legacy `backend/data` root; use `media-import` to make those assets explicit before moving to another machine.

Deletion has persistent prefix tombstones so late clip frames and in-flight uploads cannot resurrect deleted rep media. A late ambiguous upload schedules compensating deletion and invalidates earlier DELETE acknowledgements. Existing COS signed links may last until their expiry or object deletion; their maximum lifetime is 300 seconds. Keep the catalog and cache until queue work completes. No automatic cache eviction is enabled.

## Recovery preserving post-switch writes

Stop and confirm exit of **every PostgreSQL writer**, including the media pump. Select the current PostgreSQL DB and current deployed application version, then:

```sh
python -m storage export-recovery --output NEW_RECOVERY_SQLITE --report NEW_RECOVERY_REPORT --writers-stopped
```

The exporter verifies complete current content, IDs, media catalog and historical sequence high-water values. Unknown target schema/columns fail before publishing output. It never overwrites an existing DB/report. Keep the current media cache and COS bucket; a DB export does not copy object bytes. Set `DB_PATH` to the verified **new** recovery DB, remove `DATABASE_URL` and `DB_ENGINE=postgresql`, run `upgrade`/`doctor`, then repeat playback/AI/new-write/restart checks before reopening traffic. Existing COS catalog rows still require COS configuration or a complete verified local cache. Returning to local-only media requires materializing needed objects first. Record the rollback point; do not use destructive Alembic downgrade or force-push history.

## Batching and retention

`POST /api/sessions/{session_id}/telemetry/batch` accepts `poses` and `sensors` lists with the same record fields as the legacy single-record endpoints, JWT session ownership, 1–1000 combined records and one transaction. PostgreSQL uses real executemany; existing producers remain compatible and can adopt batching separately. No claim is made that old devices automatically batch.

`python -m storage retention --before UNIX_TIMESTAMP --report NEW_REPORT` is a dry run. Only `--apply` deletes old pose/sensor rows for **completed** sessions, in bounded transactions (at most 100,000 rows per table per invocation). Active sessions, orphan records, scores, history and media are retained. Back up first; choose the retention cutoff deliberately. There is no automatic production deletion schedule.

## Verification boundaries

See the accompanying validation report and GitHub Actions for exact commits and outcomes. Personal data never enters Git or CI. Fake COS tests exercise retries, access control and concurrency; no live COS account was available for this delivery. Docker runtime must be checked on a working Linux daemon; local native PostgreSQL tests alone do not certify a container image. Model usage/cost is not inferred from test time.
