# Cloud Container Foundation

This is packaging groundwork for architecture baseline 1.0, **not completed P09,
a running VPS, or an authorized production cutover**. No host has been purchased
or deployed. P09 still needs integrated portable credentials, ledger/service
wiring, P06 authenticated UI/HTTPS, delivery acceptance, spending enforcement,
restore/restart evidence, and single-scheduler cutover evidence. The owner must
approve the all-in OVHcloud quote within the 100 NIS/month total ceiling before
provisioning. Model limits alone do not enforce that total.

Integration checkpoint, 1 November 2026: the image was built on Linux ARM64.
The offline packaging check passed cleanly. `scripts/cloud_smoke.py` passed
LibreOffice DOCX/PDF extraction, file-token roundtrip, ledger restart, headed
Chromium rendering/pixels, saved fixture proof and duplicate-attempt prevention.
All content was synthetic and the test network was disabled. No VPS or real
Google authorization was used. The source suite passed 706 tests.

## Container Contract

- Python 3.12 and `uv sync --locked --no-dev --no-editable` use the existing lock.
  Build-time OS packages provide LibreOffice Writer, Carlito (Calibri-compatible),
  DejaVu/Liberation/Noto fonts, Playwright Chromium and its Linux dependencies,
  Xvfb, xauth and flock. Linux fonts/rendering still require CV visual acceptance.
- Runtime UID/GID is `10001:10001`, with a read-only image filesystem. `/tmp` is
  ephemeral. `/var/lib/backendscout` is the persistent `state` named volume and
  working directory. Its initialized home, SQLite databases, applications,
  artifacts, submission proofs, Telegram offsets, browser profile and Gmail
  refresh credentials survive container replacement. Never use `down --volumes`
  against production. Do not move SQLite to NFS or share it across hosts.
- Code and six example YAML files are allowlisted twice: in `.dockerignore` and
  explicit Dockerfile COPY sources. `.env`, filled config, CVs, Git, old virtual
  environments, launchd templates and Mac helper scripts are not build inputs.
  Never add broad `COPY . .`, build arguments with secrets, or private symlinks.
- Filled config lives in an explicit absolute host private directory, outside
  the checkout. Its `config/` and `secrets/` subdirectories mount read-only.
  Mutable Gmail credentials live separately in the state volume so atomic
  token refresh can work. No container receives a mounted `.env` file.
- `check` is the only default service: no credentials, private mounts, API calls,
  CLI callback, schedules or networking. It checks dependencies, not live health.
- `general`, `browser` and `telegram` require the `automation` profile **and**
  both exact acknowledgement values below. Only `general` owns DBOS schedules;
  the browser worker is sequential, headed under Xvfb, with its own persistent
  profile and 1 GiB shared memory. Telegram only polls and enqueues work.
  Fixed container names prevent Compose scaling; per-lane `flock` locks on the
  shared volume also reject duplicate processes using that same state.
- There are no published/exposed ports, VNC servers or web services. Verification
  handoff is disabled. Xvfb has TCP disabled; it is not remote desktop. Human
  verification and phone access remain blocked until an approved private UI
  exists. Do not submit real applications through this foundation.
- Automation deliberately has `restart: "no"`: an operator must inspect failure
  and reauthorize restart. This is not an unattended availability configuration.
  Locks do not fence another Mac or host: the ownership acknowledgement is an
  operational gate, not a distributed lease. Do not override entrypoints to
  bypass it for workflows. A running process is not evidence of a fresh digest.
- Run installed `backend-scout` commands directly. Do not run the macOS
  `scripts/run_*.sh`, `system runtime deploy/doctor`, or launchd commands on Linux.

## Safe Local Verification

From this repository, with a compatible Docker engine and Compose installed:

```sh
COMPOSE_PROFILES= docker compose --env-file /dev/null config --quiet
COMPOSE_PROFILES= docker compose --env-file /dev/null --profile automation config --quiet
COMPOSE_PROFILES= docker compose --env-file /dev/null build check
COMPOSE_PROFILES= docker compose --env-file /dev/null run --rm --no-deps check
uv run --locked --extra dev pytest -p no:cacheprovider tests/test_cloud_packaging.py
```

`--env-file /dev/null` avoids implicitly reading the checkout's `.env`. A plain
`up` with no profiles also runs only `check`. Explicitly naming an automation
service can activate its profile, so the in-container guard remains necessary.
On this Mac, `docker-compose` is available even if `docker compose` is not;
substitute that executable. Do not start, stop, reconfigure or remove the unrelated
Colima/homeassistant VM. Main integration owns daemon/build verification.

Optional **offline** Linux smoke tests, after building (neither uses real CVs):

The integrated acceptance check can be repeated from the source checkout:

```sh
COMPOSE_PROJECT_NAME=backendscout-offline-check COMPOSE_PROFILES= \
docker compose --env-file /dev/null run --rm --no-deps \
  --volume "$PWD/scripts/cloud_smoke.py:/tmp/cloud_smoke.py:ro" \
  --entrypoint xvfb-run check -a \
  '--server-args=-screen 0 1280x900x24 -nolisten tcp' python /tmp/cloud_smoke.py
```

Use the test/check lane's 1 GiB shared memory. The integration test brings its
fixture page forward and waits for two animation frames before capturing proof.
Immediate screenshot capture before first paint failed in the combined Linux
test. P07 must validate this readiness behavior in the real portal adapter before
production; the offline fixture is not proof of a real application submission.

```sh
docker compose --env-file /dev/null run --rm --no-deps --entrypoint xvfb-run check \
  -a '--server-args=-screen 0 1280x900x24 -nolisten tcp' python -c \
  'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=False); page=b.new_page(); page.set_content("<h1>Offline smoke</h1>"); assert page.title()==""; assert page.locator("h1").inner_text()=="Offline smoke"; b.close(); p.stop()'
docker compose --env-file /dev/null run --rm --no-deps --entrypoint /bin/sh check -ec \
  'printf "Synthetic CV smoke test\n" > /tmp/smoke.txt; soffice --headless -env:UserInstallation=file:///tmp/lo-smoke --convert-to pdf --outdir /tmp /tmp/smoke.txt; test -s /tmp/smoke.pdf'
```

These do not prove production browser sandboxing, visual CV quality, authenticated
UI access, portable credential refresh, restart recovery or real delivery.

## Private Host Setup

The commands below are a future operator runbook, not actions already performed.
Use an owner-approved Linux Docker host and an authorized checkout at
`/srv/BackendScout-recovery`. Do not copy the Mac runtime or browser profile.
Docker-daemon access is root-equivalent; restrict membership and host access.

```sh
cd /srv/BackendScout-recovery
umask 077
sudo install -d -m 0700 -o "$(id -u)" -g "$(id -g)" /srv/backendscout-private
test ! -e /srv/backendscout-private/cloud.env
cp deploy/cloud.env.example /srv/backendscout-private/cloud.env
chmod 0600 /srv/backendscout-private/cloud.env
sudo install -d -m 0700 -o 10001 -g 10001 \
  /srv/backendscout-private/config /srv/backendscout-private/secrets
dc() { docker compose --env-file /srv/backendscout-private/cloud.env "$@"; }
dc config --quiet
dc build check
dc run --rm --no-deps check
dc run --rm --no-deps \
  --volume /srv/backendscout-private/config:/bootstrap-config \
  --entrypoint /bin/sh check -ec \
  'umask 077; for src in /app/examples/*.example.yaml; do name=${src##*/}; dst=/bootstrap-config/${name%.example.yaml}.yaml; test ! -e "$dst"; cp "$src" "$dst"; done'
```

Review/edit all six files with `sudoedit`; replace TODOs with confirmed facts and
Linux paths. In `repo_sources.yaml`, disable unavailable Mac repositories and
unapproved scans. Do not assume examples are production-ready. Import any
approved archives into the **state volume**, updating stored paths consistently;
do not bind the parent CV folder into a container or overwrite immutable CVs.
Migration of existing DB rows/artifact references is owned by integration.

Place the **new cloud authorization's** Desktop OAuth client JSON at
`/srv/backendscout-private/secrets/gmail-client.json`, using the owner's approved
Google OAuth project. Do not print it or include it in the build context. Then:

```sh
sudo chown -R 10001:10001 /srv/backendscout-private/config /srv/backendscout-private/secrets
sudo find /srv/backendscout-private/config /srv/backendscout-private/secrets -type d -exec chmod 0700 {} +
sudo find /srv/backendscout-private/config /srv/backendscout-private/secrets -type f -exec chmod 0600 {} +
```

Edit `/srv/backendscout-private/cloud.env` privately. Supply API keys, authorized
Telegram IDs, an integer default chat ID, the correct Notion tracker IDs and the
currently approved three model identifiers. Both acknowledgements stay empty.
Compose passes an explicit environment allowlist, not the whole host environment.
Do not paste `config` output, `docker inspect`, tokens or private answers into logs.
Use `config --quiet` instead. These credentials are visible to Docker administrators.

## Fresh Gmail Authorization

The file credential provider and `gmail connect --port 8765 --no-browser` are
implemented and tested locally. The live consent flow still needs exercising on
the actual target host. Never export Keychain tokens or use a Mac login/browser profile.
`GMAIL_CREDENTIAL_STORE=file` and the absolute container credential-file path are
fixed in Compose; the containing private directory is writable only by UID 10001.

On the owner's workstation, keep this tunnel open (replace `OWNER@VPS_HOST`):

```sh
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:8765:127.0.0.1:8765 OWNER@VPS_HOST
```

On the Linux host, run this one-shot command **only after that integration gate**:

```sh
docker run --rm -it --init --network host --read-only --cap-drop ALL \
  --security-opt no-new-privileges --tmpfs /tmp:rw,nosuid,size=256m,mode=1777 \
  --user 10001:10001 --workdir /var/lib/backendscout \
  --mount type=volume,source=backendscout_state,target=/var/lib/backendscout \
  --mount type=bind,source=/srv/backendscout-private/secrets,target=/run/backendscout/secrets,readonly \
  -e GMAIL_CREDENTIAL_STORE=file \
  -e GMAIL_CREDENTIAL_FILE=/var/lib/backendscout/secrets/gmail.json \
  -e GMAIL_OAUTH_CLIENT_SECRET_PATH=/run/backendscout/secrets/gmail-client.json \
  backendscout:cloud-foundation backend-scout gmail connect --port 8765 --no-browser
```

This assumes the example's `COMPOSE_PROJECT_NAME=backendscout`, hence volume
`backendscout_state`. If renamed, use the matching existing volume, not a new one.
The one-shot Linux host-network mode lets the callback bind **127.0.0.1:8765** on
the host without a container-loopback/NAT mismatch. Verify the integrated CLI
binds loopback only before using it. No `-p` or firewall opening is needed, and no
browser/UI port is published. Open the printed Google consent URL on the
workstation, approve only the existing send/read scopes, complete the callback
through the tunnel, then close the tunnel. Do not save OAuth URLs/codes in logs.
After owner authorization, verify file-provider refresh using an approved Gmail
read check before any scheduler cutover. This has not been exercised here.

## Future Automation Cutover

Do not perform this section until P09 acceptance, owner-approved host access,
restore/shadow evidence, budget controls and private interface/delivery gates pass.
Inspect and disable **all** BackendScout Mac daily/mailbox triggers, general and
browser workers, Telegram listener, and any other cloud schedulers first. Do not
touch unrelated services. Confirm no in-flight/uncertain application delivery.
Use one host, one Compose project and one state volume, with no `--scale` override.

Only then set these two values in the external `cloud.env`:

```dotenv
BACKENDSCOUT_ENABLE_AUTOMATION=yes-i-understand
BACKENDSCOUT_SCHEDULER_OWNERSHIP=cloud-is-sole-scheduler
```

```sh
dc --profile automation config --quiet
dc --profile automation up -d --no-deps general browser telegram
dc --profile automation ps --all
```

Starting these services can call real APIs and enqueue production scout/mailbox
work immediately. Existing approval gates remain mandatory. Check fresh outcome
timestamps, source health, credential health and the 08:00 Asia/Hebron digest;
container liveness alone is insufficient. After failure/reboot, inspect durable
attempts before explicitly restarting. For rollback, stop all cloud lanes and
clear both acknowledgements before considering re-enabling the Mac owner; never
overlap owners or replay uncertain deliveries.

## Consistent Backup And Restore

Back up before upgrades/cutover. Keep an encrypted off-host copy in an
owner-approved destination within budget; the local example below is **not**
off-host protection. Backups include tokens, private answers and browser cookies.
Restrict them to mode 0600, do not commit them, and rotate/revoke leaked secrets.
Use host encryption or approved encrypted storage, not a password in shell history.

Stop all three lanes and every other writer before taking the entire volume.
This cold-backup procedure preserves SQLite databases **with their WAL/SHM files**,
DBOS tables, artifacts and associated metadata at the same quiescent point.
Do not copy only a live `.sqlite3` file. Do not modify config during the snapshot.

```sh
dc --profile automation stop telegram general browser
umask 077
sudo install -d -m 0700 -o "$(id -u)" -g "$(id -g)" /srv/backendscout-backups
BACKUP="/srv/backendscout-backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP"
chmod 0700 "$BACKUP"
dc run --rm --no-deps -T --entrypoint tar check \
  -C /var/lib/backendscout -czf - . > "$BACKUP/state.tar.gz"
sudo tar -C /srv/backendscout-private -czf - . > "$BACKUP/private.tar.gz"
docker image inspect backendscout:cloud-foundation --format '{{.Id}}' > "$BACKUP/image-id.txt"
git rev-parse HEAD > "$BACKUP/source-revision.txt"
cd "$BACKUP"
sha256sum state.tar.gz private.tar.gz image-id.txt source-revision.txt > SHA256SUMS
chmod 0600 ./*
cd /srv/BackendScout-recovery
```

Confirm each command succeeds before proceeding. Retain the corresponding image
and actual source changes as well as its revision: a dirty checkout's Git SHA is
not a complete source snapshot. Do not automatically restart automation after a
failed backup. When healthy and still the sole owner, restart only by the explicit
cutover command above. Cold backups trade a short outage for a consistent snapshot.

Test restoration into a **fresh isolated project**, with no automation profile,
no real private mounts and no networking. Never restore over the live volume:

```sh
cd "$BACKUP"
sha256sum --check SHA256SUMS
cd /srv/BackendScout-recovery
dc -p backendscout-restore run --rm --no-deps -T --user 0:0 --entrypoint tar check \
  -C /var/lib/backendscout -xzf - < "$BACKUP/state.tar.gz"
dc -p backendscout-restore run --rm --no-deps check
dc -p backendscout-restore run --rm --no-deps --entrypoint python check -c \
  'import sqlite3
from pathlib import Path
paths = list(Path("data/runtime").glob("*.sqlite3"))
assert paths, "No database restored"
for path in paths:
    with sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True) as connection:
        result = connection.execute("PRAGMA quick_check").fetchall()
        assert result == [("ok",)], (path, result)
        print(path, "ok")'
```

Root is used **only** for this explicit restore helper to preserve archive UID/GID;
normal processes remain UID 10001. Check each SQLite result is `ok`. Before reusing
the restore project name, ensure its state volume is absent or choose a new name.
Compare artifact checksums/reference resolution using the integrated ledger,
then exercise container replacement/restart and record evidence. Those integrated
checks are still pending; an offline packaging pass does not complete P09.

For an actual host recovery, keep automation disabled; use the recorded image and
restore the verified state archive into an empty production volume. Restore
`private.tar.gz` into `/srv/backendscout-private` with host root permissions, then
clear the archived acknowledgement values and recheck ownership/modes. Reauthorize
expired Gmail credentials, validate paths/artifact checksums and sole ownership,
and only then follow the cutover gates. Do not delete or overwrite the old host's
state while diagnosing a failure. Retain evidence of a tested restore.

## Implementation References

The build follows [uv's Docker integration](https://docs.astral.sh/uv/guides/integration/docker/).
Profile guards account for [Compose's explicit-service profile activation](https://docs.docker.com/compose/how-tos/profiles/).
Read-only binds use [Compose long-form mount settings](https://docs.docker.com/reference/compose-file/services/#volumes).
Browser installation and headed Linux execution follow
[Playwright's container guidance](https://playwright.dev/python/docs/docker).
