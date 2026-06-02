# odysseus-dokploy

A thin deployment wrapper for [Odysseus](https://github.com/pewdiepie-archdaemon/odysseus) that:

- Clones Odysseus from GitHub at build time (pinnable to a commit SHA)
- Pins every stateful path to a **named Docker volume** so redeploys never lose data (when deployed as a Compose service — see below)
- Bundles the supporting services Odysseus needs: ChromaDB, SearXNG, ntfy

Intended for [Dokploy](https://dokploy.com), but works with any host that runs Docker Compose.

## Deploy on Dokploy

> [!IMPORTANT]
> Deploy this as a **Compose** service — **not** an "Application". A Dokploy
> Application builds the Dockerfile on its own: it ignores this repo's
> `docker-compose.yml`, so the named volumes are never created (persistence
> silently falls back to throwaway anonymous volumes) and the ChromaDB / SearXNG
> / ntfy services Odysseus needs never start. If `docker volume ls` on the host
> shows random 64-hex volume names, you've hit this — see [Troubleshooting](#troubleshooting).

1. **Create service**: Project → Create Service → **Compose**
2. **Provider**: Git → point at this repo, branch `main`, compose path `docker-compose.yml`
3. **Environment**: copy values from `.env.example`. At minimum set `ODYSSEUS_ADMIN_PASSWORD` and `SEARXNG_SECRET`.
4. **Domain**: add your hostname → service `odysseus`, container port `7000`, enable HTTPS. Odysseus serves plain HTTP; you need TLS termination at the proxy.
5. **Deploy**. First boot logs the admin password if you left it blank — find it in Dokploy's logs view.

## What persists

Every stateful path lands in a named volume that survives `docker compose down`, image rebuilds, and Dokploy redeploys:

| Volume               | Holds                                              |
| -------------------- | -------------------------------------------------- |
| `odysseus_data`      | SQLite DB, memory.json, settings, uploads, docs   |
| `odysseus_logs`      | App logs                                           |
| `odysseus_ssh`       | Cookbook remote-server SSH identity                |
| `odysseus_hf_cache`  | HuggingFace model cache (can be 10s of GB)         |
| `odysseus_local`     | Cookbook-installed Python CLIs (vLLM, etc.)        |
| `chromadb_data`      | Vector memory store                                |
| `searxng_data`       | SearXNG config + state                             |
| `ntfy_cache`         | ntfy notification cache                            |

The only ways to destroy these are `docker compose down -v`, `docker volume rm`, or deleting the Dokploy service entirely. A normal Dokploy redeploy preserves them.

## Updating Odysseus

The Dockerfile clones at build time, so a fresh `docker compose build` pulls the latest commit on `ODYSSEUS_REF`. To force a rebuild without changing the ref, bump `CACHEBUST` in `.env` (e.g. `CACHEBUST=$(date +%s)`) and redeploy.

For production stability, pin `ODYSSEUS_REF` to a specific commit SHA instead of `main`.

## Backups

Back up the host paths under `/var/lib/docker/volumes/<project>_odysseus_data/_data` and `<project>_chromadb_data/_data`. Exclude `odysseus_hf_cache` and `odysseus_local` — they're large and re-downloadable.

Dokploy has a built-in Backups feature for service volumes; point it at the volumes above.

## Troubleshooting

### `docker volume ls` shows random 64-hex names instead of `<project>_odysseus_data`

Those are **anonymous** volumes, and they mean the stack was deployed as a Dokploy
**Application** (Dockerfile only) rather than a **Compose** service. An Application
build ignores `docker-compose.yml` entirely: the named volumes are never created,
and the supporting services (ChromaDB, SearXNG, ntfy) never start. Your data lands
in anonymous volumes that happen to survive an in-place redeploy but are orphaned by
any full `docker compose down`/`up`, and they can't be targeted by Volume Backups.

**Fix:** recreate the service as a **Compose** service (see [Deploy on Dokploy](#deploy-on-dokploy)),
then confirm on the host:

```bash
docker volume ls       # expect named volumes like <project>_odysseus_data
docker volume prune    # remove the old anonymous volumes (only once the old service is gone)
```

## Security checklist

Before exposing this to the internet:

- [ ] `AUTH_ENABLED=true` (default)
- [ ] `LOCALHOST_BYPASS` not set
- [ ] HTTPS on, plain HTTP off at the proxy
- [ ] Strong `ODYSSEUS_ADMIN_PASSWORD` (or rotate the auto-generated one)
- [ ] 2FA enabled on the admin account (Settings → Security)
- [ ] Disable signup unless you intend it (`data/auth.json` → `signup_enabled: false`)
- [ ] Review the `can_use_bash` privilege — off by default, keep it off for non-admins

## License

The contents of this repo (Dockerfile, compose file, README) are MIT.
Odysseus itself is MIT — see [upstream](https://github.com/pewdiepie-archdaemon/odysseus).
