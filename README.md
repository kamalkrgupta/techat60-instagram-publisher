# TECH AT 60 — Instagram cloud publisher

One Reel daily for `@tech_at_60`, targeting **19:00 Asia/Kolkata**. Free deterministic English captions, relevant hashtags and hash-based duplicate protection. The original HTML test Reel is already recorded and is not reposted.

## Free hosting and timing

This uses only standard Ubuntu GitHub-hosted runners in a **public repository**, GitHub Release assets, repository secrets and git-backed encrypted state. No larger runners, Git LFS, caches, Actions artifacts or paid scheduling services are configured. GitHub documents standard public-repository runner compute as free. Release inputs are copies of Shorts already published by the account owner on YouTube; no YouTube source, records or schedules are modified.

Cron runs at 18:45, 19:00 and 19:15 IST (13:15/13:30/13:45 UTC). The early job downloads, validates and privately uploads/processes the next video, then waits toward 19:00 before issuing the public publish request. A private preflight container is never a published Reel. Later jobs retry safely. **GitHub can delay or drop scheduled events, so exact 19:00 publication cannot be guaranteed.** The publisher retains a 19:00–22:00 IST grace window and never bulk-posts a missed backlog.

## Security and persistence

Repository secrets:

- `META_ACCESS_TOKEN`: verified Facebook Login user token, kept out of source/logs.
- `INSTAGRAM_STATE_KEY`: random 32-byte AES-GCM key encoded in base64.
- `INSTAGRAM_CLOUD_ENABLED`: `false` until deployment verification and the local handoff; `true` enables the already-approved daily publisher.

`GITHUB_TOKEN` is issued separately to each run with repository contents-write permission. Only the publishing step receives Meta credentials. No third-party publishing service, browser automation or unofficial Instagram API is used.

The SQLite history is encrypted and stored at `instagram-state:state.enc`. A runner must restore this existing ledger; missing, corrupt or wrongly keyed state blocks publication instead of starting fresh. Every critical database commit (including PUBLISHING) is encrypted and saved with a revision precondition **before** proceeding to the next Meta operation. A failed or uncertain remote checkpoint stops the run. Workflow concurrency prevents simultaneous jobs.

If Meta's publish response is lost, the next runner reconciles the saved container and exact caption against that account's media. It does not resend the publish request blindly. Already-published source hashes stay excluded. Source downloads are SHA-256 checked before upload.

The Instagram token can expire or be revoked and must be renewed when necessary. The state key must be backed up securely; replacing it without migrating the ledger is unsafe.

## Video inputs

`cloud/videos.json` lists versioned input files and hashes. MP4s are hosted as repository Release assets under `reel-inputs`, not in git history or Git LFS. The Mac's Instagram-only asset-sync job uploads new numbered MP4s every 15 minutes while the Mac is online. Once synced, the cloud publisher needs no Mac. Videos that exist only on an offline Mac cannot be discovered or published until synced.

After handoff, the local Meta publisher is disabled and its configuration blocks local posting. Only the asset/history-sync job remains on the Mac. It also downloads a read-only history mirror for the local queue display. The source folder and all YouTube automation remain untouched.

## Operations

Use Actions → **Instagram daily publisher** → Run workflow → **check** for a read-only Meta validation, or **publish** to run the approved daily job with its day/time guards. A successful check does not upload or publish a Reel. Inspect the run logs for confirmation.

On the Mac:

```sh
cd /Users/impulsiveweb/Techat60-Instagram
.venv/bin/python github_deploy.py status
.venv/bin/python github_deploy.py sync
.venv/bin/python publisher.py --queue
```

Do not re-enable the local Meta publisher while cloud publishing is enabled. A rollback must first disable the cloud secret/workflow, wait for active cloud jobs to finish and restore the latest encrypted ledger, then explicitly switch backend and re-enable the local job.

Official references:
- https://docs.github.com/en/billing/concepts/product-billing/github-actions
- https://docs.github.com/en/actions/how-tos/troubleshoot-workflows
- https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases
- https://github.com/fbsamples/reels_publishing_apis/tree/main/insta_reels_publishing_api_sample
