# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Project Overview

A Forex monitoring service that tracks the EUR/USD exchange rate and sends
Telegram alerts when the rate moves by more than 0.001 from the last alerted
value. The pipeline is triggered externally every 15 minutes and runs on
GitHub Actions.

**Important: all user-facing messages (Telegram alerts, commit messages if
asked, CLI output meant for the user, README content) must be written in
Russian. Code, comments, variable names, and this file stay in English.**

## Architecture / Pipeline

```
cron-job.org (every 15 min, Mon–Fri, 09:00–20:00)
        │  HTTP POST
        ▼
GitHub Actions (repository_dispatch or workflow_dispatch trigger)
        │
        ▼
Python script: fetch rate → compare to last alert → log → maybe notify
        │
        ├─ append entry to rates_log.json (always)
        ├─ if |new_rate - last_alert_rate| >= 0.001:
        │     ├─ send Telegram message
        │     └─ update last_alert_rate in rates_log.json
        └─ else: no notification, last_alert_rate unchanged
```

### Trigger mechanism (why not GitHub's native cron)

GitHub Actions' built-in `schedule:` cron can be delayed by 10+ minutes
during high load, which is unacceptable for 15-minute polling. Instead:

- cron-job.org holds the schedule and calls the GitHub REST API
  (`POST /repos/{owner}/{repo}/dispatches`, event_type e.g. `check-rate`)
  using a GitHub Personal Access Token.
- The workflow listens on `repository_dispatch` (type: `check-rate`) and
  optionally also exposes `workflow_dispatch` for manual/manual-test runs.

### Data source

- Target: https://finance.yahoo.com/quote/EURUSD=X
- Prefer a stable extraction method over brittle HTML scraping if a viable
  option exists (e.g. Yahoo Finance's public quote endpoint). Fall back to
  HTML parsing only if no stable endpoint is available. Document whichever
  method is chosen and why.

## Data Formats

- Rate: 4 decimal places, e.g. `1.1446`
- Timestamp in logs/messages: `DD.MM.YYYY HH:MM:SS`, e.g. `17.07.2026 20:29:22`
- Comparison threshold: `0.001` (absolute difference)

## Telegram Alert Format (message text is in Russian)

```
EUR/USD UP: изменение 0.0038
Было: 1.1400 (22:40 UTC)
Стало: 1.1438 (22:43 UTC)
Источник: <actual source used, e.g. Yahoo Finance>
```

- Direction: `UP` if new rate > old alert rate, `DOWN` if lower.
- Change value: absolute difference, 4 decimals.
- Source line must reflect the real data source used in code, not a
  placeholder.

## Schedule Constraints

- Every 15 minutes
- Only 09:00–20:00
- Only Monday–Friday (no Saturday/Sunday)
- Timezone: **must be confirmed with the user** before hardcoding into
  cron-job.org schedule or into timestamp formatting logic. Do not assume
  UTC vs local silently — ask or flag clearly if ambiguous.

## Storage: rates_log.json

Single JSON file in the repo acting as both history log and alert-state
store. Suggested shape (adjust as needed, but keep both a full history and
a distinct "last alert" pointer — the comparison must always be against the
last *alerted* value, not the last *logged* value):

```json
{
  "last_alert": {
    "rate": 1.1400,
    "timestamp": "17.07.2026 22:40:00"
  },
  "history": [
    {"rate": 1.1400, "timestamp": "17.07.2026 22:40:00", "alert_sent": true},
    {"rate": 1.1412, "timestamp": "17.07.2026 22:55:00", "alert_sent": false}
  ]
}
```

The workflow must commit the updated `rates_log.json` back to the repo after
each run (only when it actually changed).

## Required Secrets / Config

Stored as GitHub Actions repository secrets, never hardcoded:

- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `TELEGRAM_CHAT_ID` — target chat/channel for alerts
- GitHub PAT used *by cron-job.org* to call the dispatch API (not stored in
  repo — lives in cron-job.org's own request config, but document its
  required scope, e.g. `repo` / `contents:write` + `actions:write`
  depending on token type).

## Manual Testing Notes

- On weekends the market is closed and the rate does not move, so the
  "rate changed >= 0.001" branch cannot be exercised naturally.
- To test alert logic manually: edit `rates_log.json`'s `last_alert.rate`
  by hand to create an artificial difference, then trigger the workflow
  (`workflow_dispatch`) and confirm a Telegram message is sent and the file
  updates correctly. Revert/document test edits clearly so they aren't
  confused with real data.

## Deliverables Expected From This Repo

1. Python script(s) for: fetching the rate, comparing against last alert,
   updating `rates_log.json`, sending the Telegram message.
2. `.github/workflows/*.yml` workflow triggered by `repository_dispatch`
   (type `check-rate`) and `workflow_dispatch`, running the script and
   committing changes to `rates_log.json`.
3. `rates_log.json` — initial/seed file.
4. `README.md` (in Russian) — setup instructions:
   - creating the Telegram bot via @BotFather and obtaining token/chat id
   - adding repo secrets
   - configuring cron-job.org (schedule, HTTP method, headers, endpoint,
     auth token, days/hours restriction)
   - how to run a manual test

## Open Questions to Resolve Before/During Implementation

- Timezone for the 09:00–20:00 window and for timestamps.
- Exact Yahoo Finance data-access method (scraping vs endpoint).
- Target Telegram chat: personal DM vs dedicated channel/group.
- GitHub token type/scope to give cron-job.org (classic PAT vs
  fine-grained PAT) — pick the least-privileged option that works.
