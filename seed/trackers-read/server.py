"""trackers-read — read-only MCP sidecar for the Odysseus "trackers" feature.

Closes the one gap in the notes-as-trackers design: `manage_notes` can't return a
note's full content (`list` truncates to 80 chars; there is no `get`). This service
opens Odysseus's SQLite READ-ONLY, parses each tracker note, and exposes it over MCP
(SSE). It NEVER writes app.db — all tracker writes stay on native `manage_notes`.

Tools: tracker_due · tracker_read · tracker_list · tracker_schedule_stats ·
       schedule_after · tracker_lint

Security: SQLite has no per-table grants, so this holds a read handle to the WHOLE
app.db. It only SELECTs label='tracker', the mount is :ro, and the port is
internal-only — keep it that way. See SPEC.md.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from mcp.server.fastmcp import FastMCP

# --- config -----------------------------------------------------------------
DB_PATH = os.environ.get("TRACKERS_DB", "/data/app.db")
HOST = os.environ.get("MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("MCP_PORT", "8585"))
STATE_DIR = os.environ.get("STATE_DIR", "/state")

MIN_CADENCE_SEC = 1800        # 30 min — per-tracker cadence floor
SERVE_THROTTLE_SEC = 1740     # ~29 min — tolerant of a 30-min cron's jitter
SERVE_WINDOW_SEC = 1800       # advertised next-serve spacing
MAX_LIMIT = 20                # cap on tracker_due batch size

KINDS = {"hypothesis", "position", "goal", "monitor", "condition", "stance"}
STATUSES = {"active", "paused", "succeeded", "failed", "aborted"}

mcp = FastMCP("trackers-read", host=HOST, port=PORT)

# --- throttle state: per-owner, persisted best-effort across restarts -------
_lock = threading.Lock()
_last_served: dict[str, datetime] = {}
_STATE_FILE = os.path.join(STATE_DIR, "throttle.json")


def _load_state() -> None:
    try:
        with open(_STATE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        for k, v in (data or {}).items():
            dt = _parse_dt(v)
            if dt:
                _last_served[k] = dt
    except Exception:
        pass


def _save_state() -> None:
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = _STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: v.isoformat() for k, v in _last_served.items()}, f)
        os.replace(tmp, _STATE_FILE)
    except Exception:
        pass  # best-effort; degrades to in-memory


# --- parsing helpers --------------------------------------------------------
_FENCE_RE = re.compile(r"```tracker\s*(.+?)```", re.DOTALL)
_HEADING_RE = re.compile(r"^##\s+(.*?)\s*$")
_DUR_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([mhdw])\s*$", re.IGNORECASE)
_UNIT_SEC = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
_DOW = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _parse_fence(content: str) -> Optional[dict]:
    m = _FENCE_RE.search(content or "")
    if not m:
        return None
    try:
        return json.loads(m.group(1).strip())
    except Exception:
        return None


def _sections(content: str) -> dict:
    if not content:
        return {}
    body = _FENCE_RE.sub("", content)
    out: dict[str, str] = {}
    cur: Optional[str] = None
    buf: list[str] = []
    for line in body.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            if cur is not None:
                out[cur] = "\n".join(buf).strip()
            cur = m.group(1).strip().lower()
            buf = []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        out[cur] = "\n".join(buf).strip()
    return out


def _dur_seconds(s) -> Optional[float]:
    if not s:
        return None
    m = _DUR_RE.match(str(s))
    return float(m.group(1)) * _UNIT_SEC[m.group(2).lower()] if m else None


def _fmt_dur(secs: float) -> str:
    secs = int(secs)
    for unit, s in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= s and secs % s == 0:
            return f"{secs // s}{unit}"
    return f"{max(1, secs // 60)}m"


def _parse_dt(s) -> Optional[datetime]:
    if not s:
        return None
    try:
        txt = str(s).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(txt)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _in_window(window, now: datetime) -> bool:
    """Cheap parser for windows like 'mon-fri 13:30-20:00Z' (UTC). Fails OPEN."""
    if not window:
        return True
    try:
        days = timerange = None
        for tok in str(window).strip().lower().rstrip("z").split():
            if "-" in tok and ":" in tok:
                timerange = tok
            elif "-" in tok:
                days = tok
        if days:
            a, b = days.split("-")
            if a in _DOW and b in _DOW:
                lo, hi, wd = _DOW[a], _DOW[b], now.weekday()
                if not ((lo <= wd <= hi) if lo <= hi else (wd >= lo or wd <= hi)):
                    return False
        if timerange:
            t0, t1 = timerange.split("-")
            h0, m0 = map(int, t0.split(":"))
            h1, m1 = map(int, t1.split(":"))
            cur, lo, hi = now.hour * 60 + now.minute, h0 * 60 + m0, h1 * 60 + m1
            if not ((lo <= cur <= hi) if lo <= hi else (cur >= lo or cur <= hi)):
                return False
        return True
    except Exception:
        return True


def _next_window_open(dt: datetime, window, step_min: int = 15, max_days: int = 14) -> datetime:
    if _in_window(window, dt):
        return dt
    t, end = dt, dt + timedelta(days=max_days)
    while t < end:
        t += timedelta(minutes=step_min)
        if _in_window(window, t):
            return t
    return dt  # fail open


# --- DB access (read-only) --------------------------------------------------
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _query_notes(owner: Optional[str] = None) -> tuple[bool, list]:
    """Return (db_ok, rows) for label='tracker' notes. db_ok=False on any DB error."""
    try:
        conn = _connect()
    except Exception:
        return False, []  # DB not ready / unreadable
    try:
        q = ("SELECT id, title, content, label, owner, color, archived "
             "FROM notes WHERE label = 'tracker' AND archived = 0")
        params: list = []
        if owner:
            q += " AND owner = ?"
            params.append(owner)
        return True, conn.execute(q, params).fetchall()
    except Exception:
        return False, []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _parse_tracker(row: sqlite3.Row) -> Optional[dict]:
    """Parse a note row into a tracker dict, or None if the fence is missing/invalid."""
    content = row["content"] or ""
    fence = _parse_fence(content)
    if fence is None or fence.get("kind") not in KINDS or fence.get("status") not in STATUSES:
        return None
    secs = _sections(content)
    cmin = fence.get("cadence_min") or fence.get("cadence")
    cmin_sec = _dur_seconds(cmin)
    if cmin_sec is not None and cmin_sec < MIN_CADENCE_SEC:
        cmin = "30m"  # clamp: nothing checks faster than the heartbeat
    return {
        "id": row["id"], "headline": row["title"] or "",
        "kind": fence.get("kind"), "status": fence.get("status"),
        "conviction": fence.get("conviction"), "score": fence.get("score"),
        "scope": secs.get("scope"), "result": secs.get("result"),
        "readings": secs.get("readings"), "body": secs.get("body"),
        "cadence": fence.get("cadence"), "cadence_min": cmin,
        "cadence_max": fence.get("cadence_max") or fence.get("cadence"),
        "next_check_at": fence.get("next_check_at"), "window": fence.get("window"),
        "last_check_at": fence.get("last_check_at"),
        "last_alert_key": fence.get("last_alert_key"), "owner": row["owner"],
        "content": content,  # raw note text — edit THIS and write it back whole
    }


def _load_trackers(owner=None, status=None, kind=None) -> tuple[bool, list[dict]]:
    ok, rows = _query_notes(owner)
    if not ok:
        return False, []
    out = []
    for r in rows:
        t = _parse_tracker(r)
        if t is None:
            continue
        if status and t["status"] != status:
            continue
        if kind and t["kind"] != kind:
            continue
        out.append(t)
    return True, out


# --- tools ------------------------------------------------------------------
@mcp.tool()
def tracker_due(limit: int = 4, owner: Optional[str] = None) -> dict:
    """Trackers due to be checked RIGHT NOW (active, next_check_at passed, in-window),
    most-overdue first, capped at `limit` (max 20). Each item includes the raw
    `content` (plus parsed scope + scheduling fields) — EDIT that content and write
    it back via manage_notes update; do not reconstruct the note from parts.
    Throttled to once per ~30 minutes PER owner. Pass `owner` in multi-user deploys.
    Check `db_ok` — false means the tracker store is unreachable.
    """
    key = owner or "__all__"
    now = datetime.now(timezone.utc)
    with _lock:
        last = _last_served.get(key)
        if last is not None and (now - last).total_seconds() < SERVE_THROTTLE_SEC:
            return {"served": False, "throttled": True, "db_ok": True,
                    "next_serve_at": (last + timedelta(seconds=SERVE_WINDOW_SEC)).isoformat(),
                    "count": 0, "trackers": []}
        _last_served[key] = now
        _save_state()

    ok, trackers = _load_trackers(owner=owner, status="active")
    if not ok:
        return {"served": True, "throttled": False, "db_ok": False,
                "error": "tracker store (app.db) unavailable", "count": 0, "trackers": []}

    epoch = datetime.min.replace(tzinfo=timezone.utc)
    due = []
    for t in trackers:
        nca = _parse_dt(t.get("next_check_at"))
        if (nca is None or nca <= now) and _in_window(t.get("window"), now):
            due.append((nca or epoch, t))
    due.sort(key=lambda x: x[0])
    lim = max(1, min(int(limit or 1), MAX_LIMIT))
    # keep raw `content` + parsed `scope`; drop fields already inside content
    drop = ("body", "result", "readings")
    picked = [{k: v for k, v in t.items() if k not in drop} for _, t in due[:lim]]
    return {"served": True, "throttled": False, "db_ok": True,
            "count": len(picked), "trackers": picked}


@mcp.tool()
def tracker_read(id: str) -> dict:
    """Full parsed tracker by id (8-char prefix accepted), including raw content.
    Not throttled. Returns an 'ambiguous' error if a prefix matches more than one."""
    try:
        conn = _connect()
    except Exception:
        return {"error": "tracker store (app.db) unavailable"}
    try:
        esc = str(id).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = conn.execute(
            "SELECT id, title, content, label, owner, color, archived FROM notes "
            "WHERE label = 'tracker' AND id LIKE ? ESCAPE '\\' LIMIT 2", (esc + "%",)
        ).fetchall()
    except Exception as e:
        return {"error": f"query failed: {e}"}
    finally:
        try:
            conn.close()
        except Exception:
            pass
    if not rows:
        return {"error": "tracker not found", "id": id}
    if len(rows) > 1:
        return {"error": "ambiguous id prefix; provide more characters", "id": id}
    t = _parse_tracker(rows[0])
    if t is None:
        return {"error": "note is not a well-formed tracker (bad/missing fence)", "id": rows[0]["id"]}
    return t  # already includes raw `content`


@mcp.tool()
def tracker_list(status: Optional[str] = None, kind: Optional[str] = None,
                 owner: Optional[str] = None) -> dict:
    """Parsed list of trackers (id, headline, kind, status, conviction, next_check_at)
    for browsing/dashboards. Optional filters. Not throttled."""
    ok, rows = _load_trackers(owner=owner, status=status, kind=kind)
    items = [{"id": t["id"], "headline": t["headline"], "kind": t["kind"],
              "status": t["status"], "conviction": t["conviction"],
              "next_check_at": t["next_check_at"]} for t in rows]
    return {"db_ok": ok, "count": len(items), "trackers": items}


@mcp.tool()
def tracker_schedule_stats(owner: Optional[str] = None) -> dict:
    """Load snapshot for tuning the heartbeat: due now / next hour, counts by cadence,
    earliest upcoming, throttle window. Not throttled."""
    now = datetime.now(timezone.utc)
    ok, rows = _load_trackers(owner=owner, status="active")
    due_now = due_hr = 0
    by_cadence: dict[str, int] = {}
    next_due: Optional[datetime] = None
    for t in rows:
        nca = _parse_dt(t.get("next_check_at"))
        if nca is None or nca <= now:
            due_now += 1
        else:
            if nca <= now + timedelta(hours=1):
                due_hr += 1
            if next_due is None or nca < next_due:
                next_due = nca
        c = t.get("cadence") or "?"
        by_cadence[c] = by_cadence.get(c, 0) + 1
    key = owner or "__all__"
    last = _last_served.get(key)
    throttled_until = None
    if last is not None:
        tu = last + timedelta(seconds=SERVE_WINDOW_SEC)
        if tu > now:
            throttled_until = tu.isoformat()
    return {"db_ok": ok, "total_active": len(rows), "due_now": due_now,
            "due_next_hour": due_hr, "by_cadence": by_cadence,
            "next_due_at": next_due.isoformat() if next_due else None,
            "throttled_until": throttled_until}


@mcp.tool()
def schedule_after(interval: str, window: Optional[str] = None) -> dict:
    """Compute the next_check_at ISO timestamp `interval` (e.g. 30m, 4h, 1d, 1w) from
    now — clamped to the 30-minute floor and snapped into `window` if given. Use this
    instead of doing date math by hand."""
    secs = _dur_seconds(interval)
    if secs is None:
        return {"error": f"bad interval {interval!r}; use e.g. 30m, 4h, 1d, 1w"}
    secs = max(secs, MIN_CADENCE_SEC)
    base = (datetime.now(timezone.utc) + timedelta(seconds=secs)).replace(second=0, microsecond=0)
    target = _next_window_open(base, window) if window else base
    return {"next_check_at": target.isoformat(), "interval_used": _fmt_dur(secs)}


@mcp.tool()
def tracker_lint(owner: Optional[str] = None) -> dict:
    """List notes labelled 'tracker' whose ```tracker fence is missing or invalid —
    these are silently skipped by tracker_due/tracker_list, so this surfaces them for
    repair."""
    ok, rows = _query_notes(owner)
    if not ok:
        return {"db_ok": False, "count": 0, "broken": []}
    broken = []
    for r in rows:
        fence = _parse_fence(r["content"] or "")
        reason = None
        if fence is None:
            reason = "no parseable ```tracker fence"
        elif fence.get("kind") not in KINDS:
            reason = f"invalid kind: {fence.get('kind')!r}"
        elif fence.get("status") not in STATUSES:
            reason = f"invalid status: {fence.get('status')!r}"
        if reason:
            broken.append({"id": r["id"], "headline": r["title"] or "", "reason": reason})
    return {"db_ok": True, "count": len(broken), "broken": broken}


if __name__ == "__main__":
    _load_state()
    mcp.run(transport="sse")
