# Seed assets (trackers)

Wrapper-level "trackers" feature for Odysseus — **no upstream fork**. A tracker is
just a Note wearing a convention; Odysseus's agent + scheduler + Notes panel do most
of the work, with one tiny read-only sidecar closing the gap where `manage_notes`
can't return full note content.

## What's here

| Path | What it is | How it's installed |
|---|---|---|
| `skills/trackers/tracker-capture/SKILL.md` | The spec the agent follows to read/write trackers (encoding, kinds, alerts, retention, cadence) | Copied into `/app/data/skills` on boot by `../seed-entrypoint.sh` (only if absent) |
| `trackers-read/SPEC.md` | Contract for the read-only MCP sidecar (`tracker_due` / `tracker_read` / `tracker_list` / `tracker_schedule_stats`) | Build the service, add it to compose, register in Settings → MCP |
| `tracker-sweep.task.json` | The recurring 30-min heartbeat task body | Create **once** via the Tasks UI or `POST /api/tasks` (cron is not settable via the agent) |

## Architecture in one line

**Odysseus scheduler = heartbeat clock · sidecar = read+select+cap · `manage_notes` = write · the heartbeat task = orchestrate.**

The heartbeat (every 30 min) asks the sidecar `tracker_due(limit=N)` for the trackers
actually due now (capped, most-overdue first). It checks each with the agent's normal
tools, writes results back via `manage_notes`, and re-arms each tracker's
`next_check_at` adaptively within its `[cadence_min, cadence_max]`. Bursts smear
across ticks; quiet ticks do nothing.

## The 30-minute floor

The heartbeat runs **at most every 30 minutes**, enforced in two places:
1. the sidecar throttles `tracker_due` to serve due-work once per ~30 min (so even a
   faster cron gains nothing), and
2. `cadence_min` is clamped to ≥ 30m (a tracker can't be checked faster than the
   heartbeat fires).

## Multi-user

The sidecar scopes everything by `owner` when you pass it. Single-user deploys (the
common case) pass nothing and just work. For multi-user, create **one heartbeat task
per user** and pass `owner=<username>` to `tracker_due` — otherwise one user's
heartbeat would see, and rate-limit, everyone's trackers (the throttle is per-owner).

Two helper tools the skill/heartbeat use automatically: `tracker_lint` (find trackers
whose fence is broken, so they don't silently drop out of sweeps) and `schedule_after`
(compute a correct, ≥30m, window-aware `next_check_at` without LLM date math). If the
store is unreachable the heartbeat raises one `tracker-alert` (via `db_ok`) instead of
failing silently — but also glance at Tasks → run history if trackers seem stale, since
a *down sidecar* shows up there, not as a notification.

## How the skill gets in

The repo `Dockerfile` bundles `seed/` to `/opt/odysseus-seed/` and sets
`seed-entrypoint.sh` as the entrypoint. On every boot it copies any missing skill
dir into `/app/data/skills/...`, then hands off to Odysseus's real entrypoint.
Because it only fills **missing** dirs, your later edits survive redeploys.

> Edit the SKILL.md as a raw file (or delete the volume copy to re-seed), **not**
> through the Skills UI — saving via the UI re-emits the file and drops the custom
> `## Reference` headings.

## Bring-up order

1. **Skill** — seeded automatically on boot (above), or drop it in by hand.
2. **Sidecar** — build `trackers-read/` per its SPEC, add the compose service, register it in Settings → MCP.
3. **Heartbeat task** — create from `tracker-sweep.task.json` via the Tasks UI or:
   ```bash
   curl -X POST https://<your-host>/api/tasks \
     -H "Authorization: Bearer <api-token>" -H "Content-Type: application/json" \
     --data @seed/tracker-sweep.task.json
   ```

## First-run checks (heartbeat reliability)

The heartbeat is a normal scheduled LLM task, so two Odysseus behaviours matter:

- **Tools are RAG-selected (top-8).** `manage_notes` / `web_search` / `api_call` are always-available; `tracker_due` and `schedule_after` are named in the prompt so they rank high; the skill auto-injects (procedure + pitfalls). After the first run, open **Tasks → run history** and confirm the run actually called `tracker_due`. If it didn't: the sidecar isn't registered in Settings → MCP, or its tools haven't been indexed yet (re-run after a few minutes).
- **Round cap is 20.** `max_steps` is *not* a `POST /api/tasks` field, so the loop caps at 20 rounds — that's why the prompt uses `limit=2`. To process more per tick, raise `max_steps` on the task in the Tasks UI *and* bump `limit` in the prompt together.
- **Model.** The task uses your default task model unless you set one in the UI.

## Verify

1. In chat: *"Open a hypothesis tracker: Llama-3.3-70B beats my eval default; win = +5% over 3 runs by Jul 1; check weekly."* → a card appears in **Notes** under label `tracker` with a valid `tracker` fence.
2. `tracker_due` from the sidecar returns it once `next_check_at` passes; the heartbeat updates `## Result` and re-arms `next_check_at`.
3. A genuine crossing produces one `tracker-alert` note; a quiet heartbeat creates no notes and sends no notification.
