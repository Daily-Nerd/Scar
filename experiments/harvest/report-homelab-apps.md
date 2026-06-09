# History of Pain — `homelab-apps`

Candidate scars mined from git history. Each needs human confirmation.


## H1 — Revert-shaped commits (deadend candidates) (13)

- `7ce3116b` 2026-05-27 — chore(edgepipe): retire validation infra after #149/#150 confirmed live
- `1715c250` 2026-05-27 — test(edgepipe): restore webhook-echo to unblock #149/#150 validation
- `3a158a74` 2026-05-27 — test(edgepipe): wire NODE_NAME + restore webhook for #149/#150 validation
- `1dac4be4` 2026-05-27 — chore(edgepipe): retire webhook-echo + low budget cap (validation cleanup)
- `b6d831cb` 2026-05-25 — fix(zigbee2mqtt): set replicas back to 1 to ensure instance runs
- `b7062432` 2026-05-23 — revert(litellm): keep litellm off bifrost, spread across ragnarok hosts only
- `5d892444` 2026-05-19 — chore(authentik): scale 2025.10.0 back to 1 after fresh DB restore
- `5c47bd56` 2026-05-19 — feat(authentik): scale services back to 1 after PG12 -> PG16 migration
- `bb523261` 2026-04-29 — fix(longhorn): revert defaultReplicaCount and defaultClassReplicaCount to 1
- `01bdafb1` 2025-09-23 — Move homeassistant back to kmaster
- `11306e28` 2025-09-22 — Fix Zigbee2MQTT deployment to use restored configuration instead of ConfigMap
- `728bd0f4` 2025-09-22 — Scale Frigate deployment back to 1 replica
- `49a0ee4a` 2025-09-21 — Update PersistentVolumeClaim for Frigate to set storage size back to 50Gi

## H2 — Version downgrades (deadend candidates) (2)

- `c15b353` 2026-05-19 `apps/authentik/deployment-server.yaml`
  - image: authentik/server:2026.2.2  ->  image: ghcr.io/goauthentik/server:2025.10.0
  - _chore(authentik): scale to 0 and pin 2025.10.0 for staged upgrade_
- `c15b353` 2026-05-19 `apps/authentik/deployment-worker.yaml`
  - image: authentik/server:2026.2.2  ->  image: ghcr.io/goauthentik/server:2025.10.0
  - _chore(authentik): scale to 0 and pin 2025.10.0 for staged upgrade_

## H3 — Components tried then deleted (deadend candidates) (26)

- **apps/vikunja** lived 2026-05-20 → 2026-06-06 (`2501c50` chore(vikunja): remove unused task manager (#118), 9 files)
- **apps/obsidian** lived 2026-05-19 → 2026-06-02 (`dee701f` feat(obsidian): remove CouchDB deployment, service, config, ingress, PVC, and secrets feat(wyoming): remove deployments, services, PVCs, and kustomization for openwakeword, piper, and whisper, 13 files)
- **apps/wyoming-openwakeword** lived 2026-05-20 → 2026-06-02 (`dee701f` feat(obsidian): remove CouchDB deployment, service, config, ingress, PVC, and secrets feat(wyoming): remove deployments, services, PVCs, and kustomization for openwakeword, piper, and whisper, 3 files)
- **apps/wyoming-piper** lived 2026-05-20 → 2026-06-02 (`dee701f` feat(obsidian): remove CouchDB deployment, service, config, ingress, PVC, and secrets feat(wyoming): remove deployments, services, PVCs, and kustomization for openwakeword, piper, and whisper, 4 files)
- **apps/wyoming-whisper** lived 2026-05-20 → 2026-06-02 (`dee701f` feat(obsidian): remove CouchDB deployment, service, config, ingress, PVC, and secrets feat(wyoming): remove deployments, services, PVCs, and kustomization for openwakeword, piper, and whisper, 4 files)
- **apps/falkordb** lived 2026-05-30 → 2026-06-01 (`d64b7ad` feat(falkordb): remove FalkorDB deployment and related resources, 4 files)
- **apps/edgepipe** lived 2026-05-26 → 2026-05-27 (`0f308d6` chore(edgepipe): remove deprecated configuration and resource files, 11 files)
- **apps/devtroncd** lived 2025-09-23 → 2026-05-21 (`abdb115` feat: update resource requests and limits for various deployments, 19 files)
- **apps/cloudflared** lived 2026-05-20 → 2026-05-20 (`f7ae091` feat: remove deprecated cloudflared configurations and add new Wyoming applications, 4 files)
- **apps/prometheus** lived 2025-09-25 → 2026-04-30 (`42e41f8` chore(prometheus): remove deprecated configuration files and resources, 8 files)
- **apps/README-MEDIA.md** lived 2026-01-14 → 2026-04-28 (`d662f20` chore: Remove deprecated media server and related applications from the deployment, 1 files)
- **apps/jellyfin** lived 2026-01-14 → 2026-04-28 (`d662f20` chore: Remove deprecated media server and related applications from the deployment, 5 files)
- **apps/jellyseerr** lived 2026-01-15 → 2026-04-28 (`d662f20` chore: Remove deprecated media server and related applications from the deployment, 5 files)
- **apps/prowlarr** lived 2026-01-14 → 2026-04-28 (`d662f20` chore: Remove deprecated media server and related applications from the deployment, 5 files)
- **apps/qbittorrent** lived 2026-01-14 → 2026-04-28 (`d662f20` chore: Remove deprecated media server and related applications from the deployment, 6 files)
- **apps/radarr** lived 2026-01-14 → 2026-04-28 (`d662f20` chore: Remove deprecated media server and related applications from the deployment, 5 files)
- **apps/sonarr** lived 2026-01-14 → 2026-04-28 (`d662f20` chore: Remove deprecated media server and related applications from the deployment, 5 files)
- **root/devtron.yaml** lived 2025-09-23 → 2026-01-07 (`ce739c0` chore: Remove deprecated Devtron configuration files and CronJobs, 3 files)
- **apps/github-runners** lived 2025-09-27 → 2025-09-27 (`4b67079` fix: Remove deprecated GitHub Actions Runner configuration files and related resources, 22 files)
- **root/github-runners-controller.yaml** lived 2025-09-27 → 2025-09-27 (`4b67079` fix: Remove deprecated GitHub Actions Runner configuration files and related resources, 2 files)
- **root/github-runners-homelab-apps.yaml** lived 2025-09-27 → 2025-09-27 (`4b67079` fix: Remove deprecated GitHub Actions Runner configuration files and related resources, 2 files)
- **root/github-runners-storagex.yaml** lived 2025-09-27 → 2025-09-27 (`4b67079` fix: Remove deprecated GitHub Actions Runner configuration files and related resources, 2 files)
- **root/github-runners-controller-oci.yaml** lived 2025-09-27 → 2025-09-27 (`b398bc3` feat: Migrate to official GitHub Actions Runner Controller OCI charts, 1 files)
- **root/github-runners-homelab-apps-oci.yaml** lived 2025-09-27 → 2025-09-27 (`b398bc3` feat: Migrate to official GitHub Actions Runner Controller OCI charts, 1 files)
- **root/github-runners-storagex-oci.yaml** lived 2025-09-27 → 2025-09-27 (`b398bc3` feat: Migrate to official GitHub Actions Runner Controller OCI charts, 1 files)
- **apps/longhorn** lived 2025-03-23 → 2025-09-20 (`f14671f` Remove Longhorn chart and add Frigate and Ingress configurations, 1 files)

## H4 — Flapping values A→B→A (fence candidates: the A is load-bearing) (34)

- `apps/frigate/deployment.yaml` **replicas**: 1 -> 0 -> 1 (2025-09-20 .. 2025-09-22, 856dcba/7668c08/728bd0f)
- `apps/zigbee2mqtt/deployment.yaml` **replicas**: 1 -> 0 -> 1 (2025-09-22 .. 2025-09-22, 5f8282e/caae509/d7f2da4)
- `apps/mosquitto/deployment.yaml` **replicas**: 1 -> 0 -> 1 (2025-09-22 .. 2026-01-06, 6ffeddb/8ffae02/def9d7c)
- `apps/homeassistant/deployment.yaml` **replicas**: 0  # Start at 0 for migration -> 1  # Start at 0 for migration -> 0  # Start at 0 for migration (2025-09-23 .. 2025-09-23, 70e0c66/26999b5/e9f41b1)
- `apps/tailscale/connector.yaml` **cpu**: 100m -> 500m -> 100m (2025-09-23 .. 2026-04-29, 0933581/0933581/82d2bf7)
- `apps/tailscale/connector.yaml` **memory**: 128Mi -> 256Mi -> 128Mi (2025-09-23 .. 2026-04-29, 0933581/0933581/82d2bf7)
- `apps/tailscale/image-puller-kmaster.yaml` **image**: tailscale/tailscale:v1.88.2 -> busybox:latest -> tailscale/tailscale:v1.88.2 (2025-09-24 .. 2026-04-29, 74fdd6b/74fdd6b/82d2bf7)
- `apps/prometheus/deployment.yaml` **replicas**: 1 -> 0 -> 1 (2025-09-25 .. 2025-09-25, b485c51/f5cdb9b/14b67ae)
- `apps/prometheus/deployment.yaml` **cpu**: 500m -> 2000m -> 500m (2025-09-25 .. 2025-09-25, b485c51/b485c51/7231f1e)
- `apps/prometheus/deployment.yaml` **memory**: 1Gi -> 4Gi -> 1Gi (2025-09-25 .. 2025-09-25, b485c51/b485c51/7231f1e)
- `apps/qbittorrent/deployment.yaml` **image**: lscr.io/linuxserver/qbittorrent:latest -> qmcgaw/gluetun:latest -> lscr.io/linuxserver/qbittorrent:latest (2026-01-15 .. 2026-01-15, 78b3fb8/7f18206/7f18206)
- `apps/radarr/deployment.yaml` **replicas**: 1 -> 0 -> 1 (2026-01-14 .. 2026-01-15, baae5b2/2035106/a380b01)
- `apps/authentik/deployment-postgres.yaml` **replicas**: 1 -> 0 -> 1 (2026-01-15 .. 2026-05-19, db738d9/2e4075e/8e0f060)
- `apps/authentik/deployment-redis.yaml` **replicas**: 1 -> 0 -> 1 (2026-01-15 .. 2026-05-19, db738d9/2e4075e/0f8686d)
- `apps/authentik/deployment-server.yaml` **replicas**: 1 -> 0  # scaled to 0 for OMV VLAN cutover (nfs-shared PVC) -> 1 (2026-01-15 .. 2026-05-16, db738d9/7c2ec19/695d33e)
- `apps/authentik/deployment-worker.yaml` **replicas**: 1 -> 0  # scaled to 0 for OMV VLAN cutover (nfs-shared PVC) -> 1 (2026-01-15 .. 2026-05-16, db738d9/7c2ec19/695d33e)
- `apps/home-assistant-matter-hub/deployment.yaml` **replicas**: 1 -> 0 -> 1 (2026-04-30 .. 2026-05-16, 5f17c5c/8088f3b/171c84f)
- `apps/homepage/deployment.yaml` **replicas**: 1 -> 0 -> 1 (2026-05-18 .. 2026-06-06, 9d48720/007477e/ebdee4d)
- `apps/wyoming-piper/deployment.yaml` **replicas**: 1 -> 0 -> 1 (2026-05-20 .. 2026-05-23, f7ae091/4e800e3/9014aa0)
- `apps/hermes-agent/deployment.yaml` **replicas**: 1 -> 0 -> 1 (2026-05-21 .. 2026-05-21, b8e4f2d/3300655/f1425e1)
- `apps/hermes-agent/deployment.yaml` **cpu**: 5m -> 50m -> 5m (2026-05-23 .. 2026-05-23, 46e2e17/46e2e17/f519d3c)
- `apps/hermes-agent/deployment.yaml` **memory**: 16Mi -> 64Mi -> 16Mi (2026-05-23 .. 2026-05-23, 46e2e17/46e2e17/f519d3c)
- `apps/litellm/job-teams-reconcile.yaml` **cpu**: 50m -> 500m -> 50m (2026-05-22 .. 2026-05-22, 7f5fc54/7f5fc54/aad1e64)
- `apps/litellm/job-teams-reconcile.yaml` **memory**: 128Mi -> 64Mi -> 128Mi (2026-05-22 .. 2026-05-22, 7f5fc54/aad1e64/aad1e64)
- `apps/edgepipe/webhook-echo.yaml` **cpu**: 10m -> 100m -> 10m (2026-05-27 .. 2026-05-27, db488af/db488af/1715c25)
- `apps/edgepipe/webhook-echo.yaml` **memory**: 32Mi -> 128Mi -> 32Mi (2026-05-27 .. 2026-05-27, db488af/db488af/1715c25)
- `apps/heimdall/deployment-api.yaml` **cpu**: 50m -> 500m -> 50m (2026-06-01 .. 2026-06-04, 9f95f36/9f95f36/84978e7)
- `apps/heimdall/deployment-api.yaml` **memory**: 128Mi -> 512Mi -> 128Mi (2026-06-01 .. 2026-06-04, 9f95f36/9f95f36/84978e7)
- `apps/heimdall/deployment-timescaledb.yaml` **cpu**: 100m -> 1000m -> 100m (2026-06-01 .. 2026-06-04, 9f95f36/9f95f36/84978e7)
- `apps/heimdall/deployment-timescaledb.yaml` **memory**: 256Mi -> 1Gi -> 256Mi (2026-06-01 .. 2026-06-04, 9f95f36/9f95f36/84978e7)
- `apps/heimdall/deployment-worker.yaml` **cpu**: 50m -> 500m -> 50m (2026-06-01 .. 2026-06-04, 9f95f36/9f95f36/84978e7)
- `apps/heimdall/deployment-worker.yaml` **memory**: 128Mi -> 512Mi -> 128Mi (2026-06-01 .. 2026-06-04, 9f95f36/9f95f36/84978e7)
- `apps/heimdall/job-migrate.yaml` **cpu**: 50m -> 200m -> 50m (2026-06-01 .. 2026-06-04, 9f95f36/9f95f36/84978e7)
- `apps/heimdall/job-migrate.yaml` **memory**: 128Mi -> 256Mi -> 128Mi (2026-06-01 .. 2026-06-04, 9f95f36/9f95f36/84978e7)

## H5 — Fix-chains (landmine candidates: something here bites repeatedly) (16)

- **apps/hermes-agent** — 14 fixes 2026-05-21..2026-06-02
  - 0fad64d6 fix(hermes-agent): route dashboard through entrypoint to drop privileges
  - 33006552 fix(hermes-agent): set replicas to 0 for single-user agent deployment
  - f1425e16 fix(hermes-agent): set replicas to 1 for proper single-user agent deployment
  - 03c1c9d3 fix(deployment): increase memory request to 512Mi for better resource allocation
  - 261d117b fix(hermes-agent): drop IPv6 excepts from IPv4 ipBlock
- **apps/litellm** — 12 fixes 2026-05-21..2026-05-24
  - 30f1ef2a fix(litellm): URL-encode DATABASE_URL at boot, allow unauth /metrics
  - 4a55a669 fix(litellm): increase memory limit from 2Gi to 4Gi for improved performance
  - 772e0820 fix(litellm): bump memory limit to 2Gi, drop blocked server-snippet
  - 8a962eae fix(litellm): use individual DATABASE_* env vars for URL-safe encoding
  - 9b65e356 fix(litellm): ServiceMonitor scrape /metrics/ to skip 307
- **apps/prometheus** — 9 fixes 2025-09-25..2025-09-25
  - 06fee9d6 fix: Uncomment prometheus-config volume and mount to enable configuration
  - 09010143 fix: Comment out prometheus-config volume and mount to prevent deployment issues
  - 14b67aea fix: Update Prometheus deployment replicas from 0 to 1
  - 324d5d1e fix: Update Prometheus deployment replicas from 0 to 1
  - 4aa15f54 fix: Comment out deployment.yaml in kustomization to prevent deployment
- **apps/authentik** — 8 fixes 2026-01-15..2026-01-16
  - 4f783c1f fix(authentik): Update PostgreSQL readiness and liveness probes to use internal_cloudops user
  - 5f67b489 fix(authentik): Copy pg_hba.conf via initContainer instead of direct mount
  - 704e3099 fix(authentik): Increase PostgreSQL probe delays for slow NFS initialization
  - 9a44070a fix: Increase initial delay for PostgreSQL liveness and readiness probes
  - ad48a011 fix(authentik): Only copy pg_hba on restart, not first init
- **apps/zigbee2mqtt** — 7 fixes 2026-01-06..2026-01-06
  - 344a231a fix: Update zigbee2mqtt ConfigMap with new configuration options and device mappings
  - 69b60808 fix: Set replicas to 0 for zigbee2mqtt deployment to prevent instance from running
  - 6ffe8047 fix: Rename PersistentVolumeClaim from zigbee2mqtt-data to zigbee2mqtt-config
  - 7943d547 fix: Set replicas to 1 for zigbee2mqtt deployment to enable instance running
  - b87507f6 fix: Remove initContainers from zigbee2mqtt deployment and update storageClassName in PVC to nfs-shared
- **apps/github-runners** — 7 fixes 2025-09-27..2025-09-27
  - 023d38b8 fix: Remove deprecated GitHub runners configuration files and related resources
  - 2c972f4e fix: Update GitHub token references and Vault secret paths to use 'gh_runner_creds'
  - 459a6d71 fix: Update remote reference key for vault secrets to use 'gh' instead of 'vault'
  - 4b67079a fix: Remove deprecated GitHub Actions Runner configuration files and related resources
  - 7fc4fe8c fix: Update apiVersion to v1 in vault-secret.yaml for consistency
- **apps/heimdall** — 4 fixes 2026-06-04..2026-06-04
  - a45e3a37 fix(heimdall): migrate Job uses python -m alembic + re-pin image (#103)
  - b75b6d52 fix(heimdall): pin runAsUser 1000 on api+worker (image USER is non-numeric) (#104)
  - d49ca42a fix(heimdall): export the live Zeek log dir directly over NFS (#105)
  - fc355f0e fix(heimdall): re-pin image to sha-62c9521 (protocol VARCHAR(20) migration) (#106)
- **apps/frigate** — 4 fixes 2026-05-16..2026-05-23
  - 6eecfa84 fix(frigate): set replicas to 1 to enable deployment
  - 7c2ec197 fix(authentik, frigate, home-assistant-matter-hub, homeassistant, mosquitto, zigbee2mqtt): scale replicas to 0 for OMV VLAN cutover
  - 15706ecd fix(frigate): correct image tag format in deployment.yaml (#62)
  - 66257a65 fix(apps): pin all floating image tags + busybox to digest (#61)
- **apps/qbittorrent** — 4 fixes 2026-01-14..2026-01-15
  - e0e73ff8 fix: Update qBittorrent ExternalSecret to match working pattern
  - 4eda6ea7 fix: Update qBittorrent firewall settings to allow K8s internal networks and specify VPN input ports
  - 78b3fb8c fix: Remove Gluetun VPN container and related configurations from qBittorrent deployment
  - da2780cb fix: Update qBittorrent firewall settings to allow all outbound traffic through VPN
- **apps/devtroncd** — 4 fixes 2026-01-06..2026-01-07
  - e61d2615 fix: Remove unused devtron configuration files including external secrets and ingress definitions
  - ffa3717e fix: Remove unused devtron configuration files including external secrets and ingress definitions
  - 89a8624b fix: Correct GitHub organization reference format in DEX configuration
  - 9d6d49cd fix: Update RBAC rules to include 'watch' verb for secrets and pods
- **apps/homepage** — 3 fixes 2026-06-05..2026-06-06
  - 578d570e fix(homepage): pass host validation and proxmox skeleton init on v1+
  - 007477e2 fix(deployment): set replicas to 0 for homepage deployment
  - ebdee4da fix(deployment): set replicas to 1 for homepage deployment
- **apps/wyoming-piper** — 3 fixes 2026-05-23..2026-05-23
  - 4e800e3d fix(deployment): set replicas to 0 for wyoming-piper deployment
  - 66257a65 fix(apps): pin all floating image tags + busybox to digest (#61)
  - 9014aa00 fix(deployment): update replicas to 1 for wyoming-piper deployment
- **apps/homeassistant** — 3 fixes 2026-01-06..2026-01-06
  - 0e9f8bb1 fix: Update storageClassName in PersistentVolumeClaim to use nfs-shared
  - b7ee5d16 fix: Set replicas to 0 for homeassistant deployment to prevent instance from running
  - fbf6a69f fix: Set replicas to 1 for homeassistant deployment to enable instance running
- **apps/mosquitto** — 3 fixes 2026-05-16..2026-05-23
  - 7c2ec197 fix(authentik, frigate, home-assistant-matter-hub, homeassistant, mosquitto, zigbee2mqtt): scale replicas to 0 for OMV VLAN cutover
  - d3a431cc fix(mosquitto, zigbee2mqtt): scale replicas to 1 to enable deployment
  - 66257a65 fix(apps): pin all floating image tags + busybox to digest (#61)
- **apps/home-assistant-matter-hub** — 3 fixes 2026-04-30..2026-04-30
  - 47369622 fix(home-assistant-matter-hub): add startupProbe to ensure container readiness before HTTP server is available
  - ead38a54 fix(home-assistant-matter-hub): correct image tag format in deployment configuration
  - f2faf85e fix(home-assistant-matter-hub): update README with bridge creation guidelines and timeout handling fix(home-assistant-matter-hub): add timeout annotations to ingress configuration
- **apps/radarr** — 3 fixes 2026-01-14..2026-01-15
  - 27451c41 fix: Increase liveness probe delays for media services
  - 20351068 fix: Set Radarr deployment replicas to 0
  - a380b01f fix: Set Radarr deployment replicas to 1

## H6 — Comment archaeology (fence candidates already in the code) (0)

_nothing found_
