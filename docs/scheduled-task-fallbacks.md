# Scheduled-task fallbacks

The app has no configured Anvil Scheduled Tasks (`scheduled_tasks: []`).
Existing background-task entry points remain available for a future paid plan
or trusted external worker; ordinary app navigation does not launch them.

| Previous job | Fallback | When nobody is using the page |
| --- | --- | --- |
| `refresh_profile_rankings` hourly | Profile visits/summary refresh advance a bounded, leased ranking snapshot; previous completed results remain available. | Rankings keep their last completed timestamp. |
| `process_insider_notifications` every five minutes | Open/refresh Notifications, then its visible-page timer checks the current account's watches in bounded batches. Shared cooldowns and event keys prevent duplicate requests/alerts. | No new inbox checks; catch up on the next visit, subject to provider history coverage. |
| `check_hedge_fund_filings` hourly | Selected-fund comparison requests check for filings and resume a persisted import/model update one step at a time. Old complete models remain readable. | Incomplete work pauses and can resume on a later visit. |
| Email/SMS digest delivery inside the insider job | Preferences remain saved, but the in-app fallback does not send outbound messages. | Exact-time/unattended delivery requires Anvil Scheduled Tasks or an independently running trusted scheduler. |

A browser Timer is only a trigger while its page is open. It does not replace
an always-running server scheduler, and tab suspension or provider outages can
delay checks. The UI shows this limitation instead of promising timed delivery.

No public maintenance endpoint or unauthenticated global update callable was
added. Tests stub SEC, pricing and message providers and clean temporary data.

## Restore scheduled execution later

In the Anvil Editor, add the following jobs in Scheduled Tasks after upgrading.
These are the original UTC schedules; enable them only in the intended published
environment. Do not rely on clocks in an open browser for offline delivery.

| Job ID | Background function | Schedule |
| --- | --- | --- |
| `folio-profile-rankings` | `refresh_profile_rankings` | Hourly at minute 40 UTC |
| `folio-insider-notifications` | `process_insider_notifications` | Every 5 minutes |
| `portfoliora-13f-check` | `check_hedge_fund_filings` | Hourly at minute 20 UTC |

Official Anvil documentation:

- https://anvil.works/docs/server/scheduled-tasks
- https://anvil.works/docs/server/background-tasks.html

Free-plan Background Tasks have a 30-second runtime limit; long filing/model
updates must continue using bounded steps or run in a separately managed worker.
