#!/bin/sh
# Wrapper entrypoint (odysseus-dokploy).
#
# Seeds bundled skills into the /app/data named volume on boot, then hands off
# to Odysseus's real entrypoint (which repairs PUID/PGID ownership and execs the
# CMD). Seeding is best-effort and only fills MISSING skill dirs, so it never
# clobbers user edits and never blocks startup if it fails.
#
# Why a wrapper instead of baking the file into the image: /app/data is a named
# volume that masks anything COPYed to that path at build time, so the skill must
# be bundled OUTSIDE /app (here: /opt/odysseus-seed) and copied in at runtime.
#
# Ownership: Odysseus's SkillsManager HIDES skills whose `owner` doesn't match the
# logged-in user (owner-less files are invisible). So we stamp the admin username
# into the freshly-seeded SKILL.md; the app's backfill_owner sweep corrects it if
# that name is ever wrong.

SEED_SRC="/opt/odysseus-seed/skills"
SKILLS_DST="/app/data/skills"
ADMIN="${ODYSSEUS_ADMIN_USER:-admin}"

# Insert `owner: <admin>` right after the frontmatter `name:` line (once), in
# pure POSIX sh — no sed/awk, so it works regardless of the base image's tooling
# (debian-slim ships sed+grep but not necessarily awk, and BSD vs GNU sed differ).
stamp_owner() {
    _sf="$1"; _owner="$2"; _tmp="$1.seedtmp"; _added=0
    while IFS= read -r _line || [ -n "$_line" ]; do
        printf '%s\n' "$_line"
        if [ "$_added" = 0 ]; then
            case "$_line" in
                name:*) printf 'owner: %s\n' "$_owner"; _added=1 ;;
            esac
        fi
    done < "$_sf" > "$_tmp" && mv "$_tmp" "$_sf"
}

if [ -d "$SEED_SRC" ]; then
    find "$SEED_SRC" -name SKILL.md 2>/dev/null | while IFS= read -r f; do
        sdir=$(dirname "$f")                 # /opt/odysseus-seed/skills/trackers/tracker-capture
        rel=${sdir#"$SEED_SRC"/}             # trackers/tracker-capture
        dst="$SKILLS_DST/$rel"
        if [ ! -e "$dst" ]; then
            mkdir -p "$(dirname "$dst")" 2>/dev/null || true
            if cp -a "$sdir" "$(dirname "$dst")/" 2>/dev/null; then
                sf="$dst/SKILL.md"
                if [ -f "$sf" ] && ! grep -q '^owner:' "$sf"; then
                    stamp_owner "$sf" "$ADMIN" 2>/dev/null || true
                fi
                echo "[seed] installed skill: $rel (owner=${ADMIN})"
            else
                echo "[seed] WARN could not install $rel (continuing)"
            fi
        fi
    done
fi

# Hand off to Odysseus's real entrypoint with the original CMD.
exec /app/docker/entrypoint.sh "$@"
