# trackers-read — sidecar contract (Option A)

A tiny **read-only** MCP service that closes the one gap in the notes-as-trackers
design: `manage_notes` can't return a note's full content (`list` truncates to 80
chars, there is no `get`). This service reads Odysseus's SQLite directly, parses
each tracker, and exposes it over MCP. **It never writes** — all writes stay on the
native `manage_notes` tool. Its job is read + select + cap + parse; nothing else.

## Deployment

Add to `docker-compose.yml` (shares the existing data volume, mounted read-only):

```yaml
  trackers-read:
    build: ./trackers-read
    volumes:
      - odysseus_data:/data:ro          # same volume Odysseus uses; :ro = can't corrupt it
    restart: unless-stopped
```

Register once in **Settings → MCP**: transport `sse`, url `http://trackers-read:8585/sse`.
Scheduled LLM tasks (the heartbeat) and chat both see its tools after registration.

## Data access

- Open `file:/data/app.db?mode=ro` with `uri=True`, `PRAGMA busy_timeout=3000`.
  Read-only + busy-timeout makes it safe against Odysseus's concurrent writes
  (WAL allows a reader alongside the writer; the timeout rides out a brief lock).
- A "tracker" is a row in `notes` with `label = 'tracker'`. Parse the
  ```` ```tracker ```` JSON fence and the optional `## Readings` section out of
  `content`. Couples only to the stable `notes` table — not to Python internals —
  so it survives `ODYSSEUS_REF` bumps.

## Tools

### `tracker_due(limit=4, now=None, owner=None) -> dict`
The work-gating call the heartbeat uses. Returns the trackers due **right now**,
most-overdue first, capped — already parsed.

Selection (SQL + parse):
- `label='tracker'` AND `archived=0` AND fence `status='active'`
- AND `next_check_at <= now`
- AND (fence `window` is null OR `now` falls within it)
- ORDER BY `next_check_at` ASC, LIMIT `limit`
- (if `owner` given, also `owner = :owner`)

**30-minute throttle (the heartbeat floor).** Keep an in-process `last_served_at`.
On each call, if `now - last_served_at < 1740s` (29 min, tolerant of a 30-min
cron's jitter) return `{ "served": false, "throttled": true, "next_serve_at": …,
"trackers": [] }` and do not advance the timer. Otherwise set `last_served_at = now`
and serve. Result: even a `* * * * *` cron does real work at most once per ~30 min.
(`tracker_read` / `tracker_list` are explicit reads and are NOT throttled.)

Returns:
```json
{ "served": true, "throttled": false, "count": 2,
  "trackers": [ {
    "id": "…", "headline": "…", "kind": "position", "status": "active",
    "conviction": 3, "score": null,
    "scope": "<## Scope text>", "result": "<## Result text>",
    "readings": "<## Readings text or null>",
    "cadence": "4h", "cadence_min": "30m", "cadence_max": "1d",
    "next_check_at": "…", "window": "mon-fri 13:30-20:00Z",
    "last_alert_key": null, "owner": "…"
  } ] }
```

`cadence_min` clamp: when parsing, if `cadence_min` < 30m, treat it as `30m`
(the heartbeat can't serve faster). Belt-and-suspenders with the skill rule.

### `tracker_read(id) -> dict`
Full parsed tracker by id (8-char prefix accepted). Not throttled. Returns the same
shape as one `trackers[]` entry plus raw `content`. Used for "open tracker X" reads
in chat and for re-bucketing `## Readings`.

### `tracker_list(status=None, kind=None, owner=None) -> dict`
Parsed list (id, headline, kind, status, conviction, next_check_at) for browsing /
dashboards. Not throttled.

### `tracker_schedule_stats(now=None, owner=None) -> dict`
Observability for load tuning:
```json
{ "total_active": 18, "due_now": 3, "due_next_hour": 5,
  "by_cadence": {"30m": 1, "4h": 4, "1d": 9, "1w": 4},
  "next_due_at": "…", "throttled_until": "…" }
```
If `due_now` is regularly > the heartbeat's `limit`, you're over-subscribed — raise
`limit`, or relax some trackers' `cadence_max`.

## The heartbeat task

`seed/tracker-sweep.task.json` — `task_type=llm`, `schedule=cron`,
`cron_expression=*/30 * * * *`, `notifications_enabled=false`. It calls
`tracker_due(limit=4)` and processes only what's returned. Because the sidecar
throttles to ~30 min, the cron is a ceiling-with-margin, not the real floor — the
**sidecar is the authority on the 30-minute minimum.**

> Cron schedules are not settable via the agent's `manage_tasks` tool (only
> once/daily/weekly/monthly) — create this task via the Tasks UI or `POST /api/tasks`.

## Boundaries

- **Read-only.** No write tools. Writes (Result, next_check_at, alerts) go through
  native `manage_notes`, so reminders / ownership / the Notes panel stay native.
- **Owner scoping.** Single-user deploys can ignore `owner`. Multi-user: pass the
  heartbeat task's owner so it only sees that user's trackers.
- **Windows (v1).** `tracker_due` simply excludes off-window trackers; the agent
  arms `next_check_at` to the next window-open. Server-side next-window computation
  is a v1.1 nicety.

## Revision — hardening

Added tools:
- `schedule_after(interval, window=None)` → `{next_check_at, interval_used}`. Computes
  the next timestamp, clamped to ≥30m and snapped into `window`. The agent uses this
  instead of doing date math.
- `tracker_lint(owner=None)` → `{db_ok, count, broken:[{id, headline, reason}]}`.
  Surfaces tracker-labelled notes whose fence is missing/invalid (else silently skipped).

Changes:
- Every tool returns `db_ok`; `tracker_due` reports `db_ok:false` on a read failure so
  the heartbeat can raise an alert instead of failing silently.
- Throttle is **per owner** (`tracker_due(owner=…)`), persisted to `/state/throttle.json`
  so it survives restarts. Single-user passes no owner (keyed under `__all__`).
- `tracker_due` clamps `limit` to ≤20 and omits the static `body` (token saving).
- `_parse_tracker` rejects invalid `kind`/`status` (they show up in `tracker_lint`).
- `tracker_read` escapes LIKE wildcards and errors on an ambiguous prefix.
- Runs **non-root** (uid 1000, aligned with the default Odysseus PUID).

## Security note
SQLite has no per-table grants, so this service holds a read handle to the ENTIRE
`app.db` — including sensitive tables (`email_accounts` with encrypted creds,
`api_tokens` hashes). It only ever `SELECT`s `label='tracker'`, the mount is `:ro`,
and the port is internal-only — but treat the container as data-sensitive: keep it off
any public network and never add a write path.
