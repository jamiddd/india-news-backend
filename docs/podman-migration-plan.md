# Podman + Service Naming Migration Plan

Discussion context: prompted by two problems hit during the 2026-09-16 timeline-audio
incident — service names don't match what they actually do (`crossword_scheduler`
runs 7 different things), and the deploy command is easy to get wrong (`docker cp`
was used as a deploy shortcut for `timeline_scheduler`, silently left the running
process on stale in-memory code for 2 days because `docker cp` never restarts the
process). Podman migration is motivated by a longer-term goal of building
Kubernetes-transferable skills, not an urgent operational need — see §2.

## 1. New service names

One word, no underscores.

| Old | New | Why |
|---|---|---|
| `app` | `app` | already accurate, already one word |
| `crossword_scheduler` / `news_crossword_scheduler_prod` | `contentworker` / `news_contentworker_prod` | it runs crossword, sudoku, word search, spelling bee, word ladder, quiz, editorial backgrounds, horoscope — not just crossword |
| `poll_scheduler` / `news_poll_scheduler_prod` | `pollworker` / `news_pollworker_prod` | already accurate, just de-underscored |
| `timeline_scheduler` / `news_timeline_scheduler_prod` | `narrator` / `news_narrator_prod` | does narrative generation **and** audio synthesis for the Timeline tab; kept distinctive rather than `timelineworker` since it's the one worth remembering by name |

Container names keep the `news_<service>_prod` convention, just the service part
changes.

## 2. Podman structure — quadlets, not plain podman-compose

Each service becomes a systemd **quadlet** (`.container` unit file), not a
`podman-compose.yml`. Reasoning:

- `systemctl restart <unit>` re-reads `EnvironmentFile=.env` on every restart —
  fixes the ".env edits don't get picked up without a recreate" problem
  structurally, not by remembering the right command.
- Quadlets are the k8s-shaped path (`podman generate kube` works cleanly off a
  running quadlet-managed container/pod) — matches the longer-term k8s learning
  goal. Docker Compose's YAML doesn't translate to k8s manifests; Podman's pod
  abstraction (one network namespace, multiple containers) is the same concept
  k8s pods use.
- Systemd gives uniform `systemctl status` / `restart` / `enable` — standard
  Linux tooling instead of compose-specific commands.

Layout on each droplet (`/etc/containers/systemd/`):
```
app.container
contentworker.container
pollworker.container
narrator.container   # newsapp ONLY — see §3
```

Each `.container` file replaces one `services:` block from
`docker-compose.prod.yml` — same image build context, same env var list,
translated to quadlet syntax (`Image=`, `Environment=`, `EnvironmentFile=`,
`Volume=`, `PublishPort=`).

Quadlets consume a pre-built image; they don't build inline the way compose's
`build:` context does. The canonical deploy script (§5) handles the build step
explicitly.

## 3. Structural singleton guard for narrator

Don't rely on a comment (this is exactly what failed — a rogue duplicate ran on
`newsapp-2` for 4 days, 2026-09-12 to 2026-09-16, racing writes against
`newsapp`'s scheduler and doubling Claude API spend). Two layers:

1. **The quadlet file for this service only exists in `/etc/containers/systemd/`
   on `newsapp`.** `newsapp-2` never has the unit file — nothing to accidentally
   start.
2. **Code-level check**: `run_timeline_scheduler.py` reads a
   `PRIMARY_SCHEDULER_HOST` env var (set only in `newsapp`'s `.env`) and refuses
   to start (loud log line + `sys.exit(1)`) if unset/mismatched — so even a
   copy-pasted unit file or manual `podman run` on the wrong host fails fast
   instead of silently racing.

## 4. Deploy discipline — kill `docker cp` / `podman cp` as a deploy step

Root cause of the 2026-09-16 incident: `docker cp` was used to push updated
`.py` files straight into the running `timeline_scheduler` container's
filesystem. This looked like it worked (file visibly updated) but the
already-running Python process had the old module in memory from process
start — `docker cp` never restarts anything, so the fix silently didn't apply
for ~2 days despite the fix being merged and even despite multiple `up -d
--build app` runs (which never touched `timeline_scheduler` at all — the two
services were conflated).

**New rule, enforced by the deploy script itself, not just documented:**
`podman cp` / `docker cp` is for one-off inspection/debugging only, never for
shipping a code change. Every code change goes through the single canonical
command in §5.

## 5. One canonical redeploy command per service, no `-f` file flag

Fixes "the command is hard to remember." Two pieces:

**a. `COMPOSE_FILE` set once per droplet** so `-f docker-compose.prod.yml` is
never typed manually (it's only needed because `docker-compose.yml`, the dev
file, also exists in the repo and would otherwise be the default):
```bash
echo 'export COMPOSE_FILE=docker-compose.prod.yml' >> ~/.bashrc
```
Local dev machines are untouched — this is a production-droplet-only setting.

**b. Wrapper script**, `~/india-news-backend/deploy.sh`, on each droplet:
```bash
./deploy.sh <service-name>
```
Does, in order, every time, same shape:
1. `git pull`
2. `podman build -t india-news-backend-<service> .`
3. `systemctl restart <service>.service`
4. Prints the new `StartedAt` so a restart is confirmed, not assumed — the
   exact manual check that was needed to catch the 2026-09-16 incident.

**`.env`-only changes** (no code change) don't need a rebuild:
```bash
nano .env
systemctl restart <service>.service   # env re-read automatically via EnvironmentFile=
```

## 6. Migration staging — not a same-day swap on both droplets

1. **Pilot on `newsapp-2` first** — currently lighter (no singleton risk to
   break there).
2. Convert one low-stakes service first (`pollworker`) — validate quadlet +
   `.env` reread + `deploy.sh` end to end.
3. Convert remaining `newsapp-2` services (`app`, `contentworker`).
4. Only once `newsapp-2` has run clean for a few days, migrate `newsapp`
   (higher stakes — the only droplet allowed to run `narrator`).
5. Docker Compose stays installed and untouched on both droplets until Podman
   has fully proven itself — rollback is `systemctl stop <quadlet>` +
   `docker compose up -d <old service>`.

## 7. Rollback plan

At every stage, don't delete the Docker image/container for a service until
its Podman quadlet replacement has been stable for a defined minimum (suggest:
3 days). Rollback = `systemctl stop <quadlet>` + `docker compose up -d <old
service>`.

## 8. Day-to-day workflow after migration

**Deploy:**
```bash
ssh newsapp
cd ~/india-news-backend
./deploy.sh app   # or contentworker, pollworker, narrator
```

**Env-only change:**
```bash
nano .env
systemctl restart <service>.service
```

**Status / logs** (systemd verbs, not engine-specific commands):
```bash
systemctl status narrator.service
journalctl -u narrator.service --since 24h -f
```

**One-off script / DB query inside a running container:**
```bash
podman exec -it news_app_prod python3 scripts/backfill_all_timeline_audio.py --dry-run
podman exec -it news_app_prod python3 -c "..."
```
Container names (`news_<service>_prod`) stay the same convention — only the
binary changes from `docker` to `podman`.

## 9. Notes ruled out during discussion

- **Rootless permission risk**: checked — the only volume mount across all 4
  services is the Firebase service-account key, and it's read-only (`:ro`). No
  service writes to a host-mounted path. The Dockerfile has no `USER`
  directive (root inside the container, which is the default for this image),
  but under rootless Podman that maps to the unprivileged host user via a user
  namespace anyway — no permission-mismatch risk identified for this codebase.
- **Memory savings**: real but secondary. Docker's persistent `dockerd` +
  `containerd` + per-container `containerd-shim` carries fixed baseline
  overhead (tens of MB+) that Podman's daemonless model (tiny `conmon` per
  container, no background daemon) avoids. Worth checking `free -h` on both
  droplets before/after to quantify for the actual instance sizes, but not the
  primary reason for this migration.

## 10. Implementation checklist

- [x] Rename services in `docker-compose.prod.yml` (repo change, reversible) — 2026-09-16
- [x] Add `PRIMARY_SCHEDULER_HOST` check to `run_timeline_scheduler.py` — 2026-09-16
- [x] Write quadlet `.container` files (`backend/deploy/quadlets/`) — 2026-09-16, drafts, host paths are placeholders (`<ENV_PATH>`, `<FIREBASE_HOST_PATH>`), fill in per droplet before use
- [x] Write `deploy.sh` (`backend/deploy/deploy.sh`) — 2026-09-16
- [ ] **Before next deploy of `docker-compose.prod.yml` to `newsapp`: add
      `PRIMARY_SCHEDULER_HOST=newsapp` to newsapp's `.env`.** The renamed
      `narrator` service now hard-exits at startup without it (by design —
      see §3) — deploying the renamed compose file without this env var set
      first would take the Timeline narration scheduler down entirely. Not
      needed on `newsapp-2` (must stay unset there).
- [ ] Install Podman on `newsapp-2`
- [ ] Pilot: migrate `pollworker` on `newsapp-2`, verify for a few days
- [ ] Migrate `app`, `contentworker` on `newsapp-2`
- [ ] Migrate all 4 services on `newsapp` (only droplet that gets `narrator`)
- [ ] Remove Docker Compose services once stable (3+ days per service)
