# Infra config backups

Point-in-time copies of config that lives *outside* this repo, on the
droplet itself, because it's shared with an unrelated project
(`scalp8.xyz`) and hand-edited directly on the box (see
`india-news-app-handoff.md` §8 Phase 6c and §10). These are **not** synced
automatically — pull a fresh copy with:

```
ssh vps "cat /etc/caddy/Caddyfile" > backend/infra/Caddyfile
```

- `Caddyfile` — system-level Caddy config (`/etc/caddy/Caddyfile`), last
  pulled 2026-08-09. Routes `openindiannews.com` → `127.0.0.1:8080` (this
  backend) and `www.openindiannews.com` → a redirect; the `scalp8.xyz`
  blocks belong to the other project on this host and are unrelated to
  this app, kept here only because they live in the same file.

Re-pull and commit whenever the box's Caddy config changes — closes
production-readiness-gaps.md gap #9 ("lives outside version control").
This is a backup, not a deploy target: don't wire this file into any
script that pushes it back to the droplet without deliberately deciding
to bring Caddy under real config management first.
