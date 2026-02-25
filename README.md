# Timesheets automation

![Timesheets banner](assets/banner.webp)

A Python 3.11 CLI utility for generating and sending Jira `worklog` entries using weighted task distribution.

## What It Does

1. Fetches open issues from the last 60 days:
   - `assignee=currentUser()`
   - `statusCategory!=Done`
   - `created>=-60d`
2. Asks for weight `1..5` for each issue (`key + summary`).
3. Asks for a date range (`dd.mm.yyyy`), defaults:
   - start: one month ago
   - end: today
4. Asks for:
   - hours per day (default `4`)
   - max hours per single entry (default `2`)
5. Prints a summary and asks for confirmation.
6. For each working day (Mon-Fri):
   - checks already logged time via JQL:
     - `worklogAuthor=currentUser() AND worklogDate="YYYY-MM-DD"`
   - fills only the remaining time to reach the daily target
   - generates entries in `1 hour` chunks
   - distributes tasks via weighted random using configured weights

## Files

- Script: `timesheet.py`

## Requirements

- Python `3.11+`
- No external dependencies required

Install:

```bash
python3 -m pip install -r requirements.txt
```

## Token and URL Configuration

In `.env`:

- `DEFAULT_BASE_URL = "https://jira.domain.example.com"`
- `DEFAULT_TOKEN = ""`

You can:

1. Set values in `.env`, or
2. Pass `--token` / `--base-url` flags (they override `.env`).

## Run

Dry-run (does not send anything, only prints payload):

```bash
python3 timesheet.py --dry-run --token "<JIRA_TOKEN>"
```

Real posting:

```bash
python3 timesheet.py --token "<JIRA_TOKEN>"
```

With custom URL:

```bash
python3 timesheet.py --token "<JIRA_TOKEN>" --base-url "https://jira.domain.example.com"
```

## Payload Format

POST to:

- `/rest/api/2/issue/{ISSUE_KEY}/worklog`

Example:

```json
{
  "comment": "Work on task TASK-123",
  "started": "2026-02-23T10:00:00.000+0300",
  "timeSpentSeconds": 7200
}
```

## Important Logic Details

- Generation runs only on weekdays.
- If remaining time for a day is less than 1 hour, that day is skipped.
- If `max_task_hours > daily_hours`, the script exits with a validation error.
- Time zone in `started`: `+0300` (MSK).

## Interactive Session Example

```text
$ python3 timesheet.py --dry-run --token "***"
Step 1/5: loading open issues for the last 60 days...
Weight for TASK-123 (Fix parser bug) [1-5]: 5
Weight for TASK-456 (Refactor importer) [1-5]: 2

Step 2/5: period dates.
Start date [25.01.2026]:
End date [25.02.2026]:

Step 3/5: daily hours.
Hours to fill per day [4]:

Step 4/5: maximum duration per entry.
Maximum hours per entry [2]:

Step 5/5: confirmation.
... summary ...
Confirm run? [y/n]: y
[DAY] 2026-02-23 logged=1.00h to_add=3.00h
[DRY-RUN] TASK-123 {'comment': 'Work on task TASK-123', ...}
...
```
