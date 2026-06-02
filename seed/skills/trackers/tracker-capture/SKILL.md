---
name: tracker-capture
description: Open, update, check, and close "trackers" stored as notes — hypotheses, positions, goals, monitors, conditions, and third-party stances that open, evolve, and may close. Use for any tracker work and for the scheduled tracker sweep.
version: 1.0.0
category: trackers
tags: [trackers, notes, monitor, hypothesis, position, goal, stance, condition]
status: published
confidence: 0.9
source: imported
created: 2026-06-03T00:00:00Z
---

## When to Use
When the user wants to open, update, review, or close a tracker — anything that opens, evolves, and may close: a hypothesis/thesis, a position (something staked), a goal, a monitor (a watched quantity), a managed condition, or a third party's stance. Also when running the recurring "tracker sweep" that checks active trackers and rewrites their status.

## Procedure
1. A tracker is a NOTE managed with the `manage_notes` tool. Identify trackers by `label` = "tracker"; list them by filtering manage_notes to that label.
2. Encode every tracker note as: `title` = the one-line headline; `content` = three markdown sections `## Body` (static framing), `## Scope` (parameters a check reads: win/loss, target/stop, cadence, escalation), `## Result` (the living narrative — how it is going right now); then a fenced block tagged `tracker` holding the machine fields.
3. The tracker fence is ONE line of JSON inside a ```tracker code block: {"kind":"hypothesis|position|goal|monitor|condition|stance","status":"active|paused|succeeded|failed|aborted","conviction":1-5 or null,"score":0-100 or null,"cadence":"1d","cadence_min":"30m","cadence_max":"1w","next_check_at":"ISO-8601 UTC","window":null,"last_check_at":null,"last_alert_key":null,"opened_at":"ISO-8601 UTC","closed_at":null,"group":""}. `cadence` is the normal interval (30m | 4h | 1d | 1w …); `cadence_min`/`cadence_max` bound adaptive re-scheduling and default to `cadence` if omitted; `cadence_min` may never be below 30m. `window` is an optional active window like "mon-fri 13:30-20:00Z" (null = always).
4. `kind` is exactly one of: hypothesis, position, goal, monitor, condition, stance. `status` is exactly one of: active, paused (open) or succeeded, failed, aborted (closed — the value IS the outcome). Never use any other value; fold edge cases into the nearest one and explain in prose.
5. Set the note `color` by status: active=none, paused=#f4c542, succeeded=#3aa657, failed=#d1453b, aborted=#8a8a8a. Set `source`="agent".
6. OPEN: manage_notes create with the encoding above, status "active", and set `cadence` + `next_check_at`. For per-kind headline/Body/Scope templates, call manage_skills action=view name=tracker-capture and read the Reference sections.
7. EVOLVE: get the note's full `content` (tracker_due includes it; or call tracker_read), edit it IN PLACE — rewrite the `## Result` section, adjust the fence (e.g. conviction, next_check_at), leave `## Body`/`## Scope` unchanged — then pass that COMPLETE edited content to manage_notes update. (manage_notes overwrites the whole content field; never send only the changed part.)
8. CLOSE: manage_notes update — set fence `status` to a terminal value and `closed_at` to now, write the final `## Result` (outcome + lessons), set `color`.
9. CHECK (during the sweep): read the tracker's `## Scope`, gather the current picture with your tools (web_search, web_fetch, api_call, email), and compare against the scope.
10. Routine change → rewrite `## Result`, then set `next_check_at` ADAPTIVELY: pick an interval within [cadence_min, cadence_max] by the situation — tighten toward `cadence_min` when volatile or near a threshold, relax toward `cadence_max` when calm or dormant — and call `schedule_after(interval, window)` to get the exact `next_check_at` (it clamps to ≥30m and snaps into `window`). Do NOT compute timestamps by hand. Save. Set `next_check_at` ONLY after a successful update (on a fetch failure leave it so the next heartbeat retries).
11. Keep the note bounded: `## Result` is a REWRITTEN synthesis, never an append log. Do not paste raw time-series into the note — high-frequency data (prices, metrics) stays in its source; carry only the reading(s) that change the picture.
12. Only when a retained series genuinely helps the reader (e.g. a position tracked near its stop), keep a bounded `## Readings` section and downsample it ADAPTIVELY each check: keep recent readings fine-grained, roll older ones into coarser buckets, PIN any reading flagged notable (a threshold touch, a regime change), and drop ancient non-notable points. Tune granularity to the situation — finer when volatile or near a threshold, coarser when dormant. See "Reference — data retention" (manage_skills view).
13. Threshold crossing (stop hit, invalidator confirmed, conviction flip, tracked actor unwinds, condition worsening) → ALSO create a SEPARATE note labelled "tracker-alert", `due_date`="in 1 minute" (a RELATIVE time — Odysseus computes the absolute moment from the real clock at write time, so it lands inside the reminder dispatch window and actually pings; do NOT stamp a wall-clock "today at HH:MM" or "now": during a multi-tracker sweep that timestamp goes stale and the note reminder is silently skipped), containing four parts: What changed / Why it crosses the threshold / Severity (low|normal|high|critical, calibrated) / Recommended next step — plus a line `Tracker: <headline>`. Then write an identifier for this crossing into the tracker fence `last_alert_key` so it is not re-alerted next sweep.
14. If nothing material changed, do nothing and write no note. Silence on quiet days is correct — never emit an all-clear.

## Pitfalls
- manage_notes `update` OVERWRITES the entire `content` field. Always pass the COMPLETE note text (Body + Scope + Result + Readings + fence) with only the changed parts edited — never send just the new Result, or you erase Body/Scope/the fence. Get current content from tracker_due (it includes `content`) or tracker_read.
- Never set `due_date` on a tracker note — it makes Odysseus ping that tracker every cadence. Only "tracker-alert" notes carry `due_date`.
- Never invent a `kind` or `status` outside the closed sets.
- `## Result` answers "how is it going right now", not "what is it" (that is `## Body`).
- Advance `next_check_at` only after a successful update; never advance it without doing the check.
- Do not re-alert the same crossing: check the fence `last_alert_key` before creating a tracker-alert.
- During a sweep, process EVERY due tracker; do not stop early or summarise. With many due, do the most-overdue first.
- Calibrate severity. A false or inflated alert erodes trust faster than a missed one.
- Never paste a raw time-series into a tracker note; keep `## Result` a synthesis and let the source hold the full history. An ever-growing note is a smell.
- When you keep a `## Readings` section, re-cap and downsample it on EVERY check so it never grows without bound. If exact numeric history is the point, notes are the wrong store — say so.
- `cadence_min` may never be below 30m — the heartbeat runs at most every 30 minutes, so anything faster is meaningless (the sidecar clamps it anyway).
- When opening several trackers at once, stagger their first `next_check_at` — don't arm them all to the same minute, or they bunch into one heartbeat.
- Set `next_check_at` via the `schedule_after(interval, window)` sidecar tool, never by hand — hand-computed timestamps drift and risk sub-30m scheduling. If a tracker "disappears" from sweeps, run `tracker_lint` — its fence is probably malformed.

## Verification
- The note has `label` "tracker" and a parseable ```tracker fence with a valid kind and status.
- Filtering notes by label "tracker" returns it; opening it shows Body / Scope / Result.
- A sweep on a quiet day creates no notes and sends no notification.

## Reference — the six kinds
| kind | use when | headline shape | Body sections | Scope holds |
|---|---|---|---|---|
| hypothesis | your own falsifiable claim | the claim | Rationale / Predictions / Reasoning | win condition, loss condition, horizon |
| position | something you've staked effort/capital on | what's held | Entry rationale / Sizing / Exit plan | target, stop, max size (or revaluation cadence) |
| goal | a target you're working toward | target + criterion | Why this matters / Plan / Approach | definition-of-done, deadline |
| monitor | a quantity you watch, don't influence | the subject | What I'm tracking / Why I care | source, cadence, what's notable |
| condition | an ongoing situation you manage | the situation | Symptoms-context / Management / History | escalation triggers, re-eval cadence |
| stance | what a THIRD PARTY holds/believes | `<actor> — <attribution>` | Source / Why this attribution / What would change it | source(s), update cadence, what would close it |

Distinctions: hypothesis = your belief; stance = someone else's, attributed. position = you've staked something; goal = a target. monitor = passive watching; condition = active management.

## Reference — score rubric
Optional `score` 0-100, a self-assessment of row quality at write time: 20 = baseline, 50 = notable, 80 = pin-worthy.

## Reference — data retention
Default: keep NO raw series in the note. `## Result` is a rolling synthesis; the external source (price API, feed, sheet) stays system-of-record for the full history. Most trackers need nothing more — a synthesis IS lossy compression.

Optional `## Readings` section — only when a short retained series helps the reader (e.g. a position near its stop). Keep it bounded and downsample ADAPTIVELY on every check:
- Recent: keep each reading raw within the active window (≈ cadence × 10 — about 2 weeks for a weekly tracker).
- Older: collapse to one point per coarser bucket — weekly, then monthly.
- Ancient: drop — UNLESS the point is pinned.
- Pin notable points (threshold touch, regime change, entry/exit) so compression never loses what mattered.
- Adapt to the situation: widen retention when volatile or near a threshold; coarsen hard when flat, dormant, or far from any threshold (a quiet monitor may collapse to monthly).

Example `## Readings` (newest first):
- 2026-06-03 — 191.40  *(pinned: closed below 200d MA)*
- 2026-06-02 — 196.10
- 2026-06-01 — 197.30
- … (last ~10 raw) …
- 2026-W21 — ~198 avg
- 2026-05 — ~205 avg

When exact numeric history is the point (valuation / PnL series), the note is the wrong store — keep the series in the source tool and synthesize here. That is the signal you've outgrown notes-as-trackers and want the deferred snapshot store.

## Reference — scheduling & cadence
Checks are driven by a HEARTBEAT task (a scheduled LLM task on a fixed interval) that asks the sidecar `tracker_due(limit=N)` for the trackers actually due right now — capped at N, most-overdue first. Work happens per-tracker, never all-at-once.
- Heartbeat floor: the heartbeat runs at most every 30 minutes, and the sidecar ENFORCES this (it serves due-work at most once per ~30 min even if the task fires more often). 30m is the fastest any tracker is ever checked; set `cadence_min` no lower.
- Per-tick cap: only N trackers are processed per heartbeat; the rest stay due and drain over the next ticks. Bursts smear across time automatically — never "schedule everything at 8am".
- Adaptive cadence: after each check, set `next_check_at` to now + an interval chosen within [cadence_min, cadence_max] by the situation (tighten when volatile / near a threshold, relax when calm / dormant). A fixed-cadence tracker sets min = max = cadence.
- Windows: if `window` is set (e.g. "mon-fri 13:30-20:00Z"), only check inside it; otherwise arm `next_check_at` to the next window-open.
- Spread: when opening several trackers, stagger their first `next_check_at` so they don't bunch into one tick.

## Reference — worked example (note content)
```
## Body
Testing whether Llama-3.3-70B should replace my eval-suite default. Rationale: cheaper to serve, similar reported quality.

## Scope
Win: >=5% higher pass-rate over 3 runs by Jul 1. Loss: within noise. Check weekly. Source: my eval harness.

## Result
Two of three runs in; +3.1% so far, within noise on run 2. Leaning inconclusive.

```tracker
{"kind":"hypothesis","status":"active","conviction":3,"score":null,"cadence":"1w","cadence_min":"1d","cadence_max":"2w","next_check_at":"2026-06-10T09:00:00Z","window":null,"last_check_at":null,"last_alert_key":null,"opened_at":"2026-06-03T14:00:00Z","closed_at":null,"group":"eval-bench"}
```
```

## Reference — alert note (tracker-alert)
```
title: AAPL closed below its 200-day MA — long-thesis invalidator hit
label: tracker-alert
due_date: in 1 minute
content:
**What changed:** AAPL closed 191.40, below the 200-day MA (≈196) named as the invalidator.
**Why it crosses the threshold:** Scope says "exit-watch if it closes below the 200-day MA two days running" — second close today.
**Severity:** high
**Recommended next step:** Decide whether to trim or exit per the Exit plan; or revise the invalidator if the thesis still holds.
Tracker: AAPL Q4 long, 200 shares
```
