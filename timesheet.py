#!/usr/bin/env python3
from __future__ import annotations

import argparse
import calendar
import json
import os
import random
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

def load_env_file(file_path: str = ".env") -> None:
    try:
        with open(file_path, encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        return


load_env_file()


def parse_positive_int_env(var_name: str, fallback: int) -> int:
    raw_value = (os.getenv(var_name) or "").strip()
    if not raw_value:
        return fallback
    try:
        parsed = int(raw_value)
    except ValueError:
        print(f"[WARN] Invalid {var_name}={raw_value!r}; using {fallback}.")
        return fallback
    if parsed < 1:
        print(f"[WARN] {var_name} must be >= 1; using {fallback}.")
        return fallback
    return parsed


DEFAULT_BASE_URL = os.getenv("DEFAULT_BASE_URL", "https://jira.prosv.ru")
DEFAULT_TOKEN = os.getenv("DEFAULT_TOKEN", "")
DEFAULT_HOURS = parse_positive_int_env("DEFAULT_HOURS", 4)
DEFAULT_MAX_DURATION = parse_positive_int_env("DEFAULT_MAX_DURATION", 2)
DEFAULT_TASK_DAYS_RANGE = parse_positive_int_env("DEFAULT_TASK_DAYS_RANGE", 60)
INSECURE_SSL_CONTEXT = ssl._create_unverified_context()
DATE_INPUT_FORMAT = "%d.%m.%Y"


@dataclass
class Issue:
    key: str
    summary: str
    weight: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Jira worklogs by weighted random distribution."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not create worklogs, only print planned payloads.",
    )
    parser.add_argument(
        "--token",
        default=DEFAULT_TOKEN,
        help="Jira Bearer token. If omitted, DEFAULT_TOKEN from .env is used.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Jira base URL.",
    )
    return parser.parse_args()


def make_headers(token: str) -> dict[str, str]:
    if not token:
        raise ValueError(
            "Jira token is empty. Pass --token or set DEFAULT_TOKEN in .env."
        )
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def request_json(
    method: str,
    url: str,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if params:
        query = urllib.parse.urlencode(params)
        url = f"{url}?{query}"

    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    request = urllib.request.Request(url=url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(
            request, timeout=30, context=INSECURE_SSL_CONTEXT
        ) as response:
            raw_body = response.read()
    except urllib.error.HTTPError as exc:
        response_text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP {exc.code} for {method} {url}: {response_text[:500]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed: {method} {url}: {exc}") from exc
    try:
        if not raw_body:
            return {}
        return json.loads(raw_body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Non-JSON response for {method} {url}") from exc


def fetch_open_issues(
    headers: dict[str, str], base_url: str, task_days_range: int
) -> list[dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/rest/api/2/search"
    params = {
        "jql": (
            "assignee=currentUser() AND statusCategory!=Done "
            f"AND created>=-{task_days_range}d ORDER BY created DESC"
        ),
        "fields": "key,summary,description,creator,created,status",
        "startAt": 0,
        "maxResults": 100,
    }
    issues: list[dict[str, Any]] = []
    while True:
        payload = request_json("GET", url, headers, params=params)
        page_items = payload.get("issues", [])
        issues.extend(page_items)
        start_at = payload.get("startAt", 0)
        max_results = payload.get("maxResults", len(page_items))
        total = payload.get("total", len(page_items))
        if start_at + max_results >= total:
            break
        params["startAt"] = start_at + max_results
    return issues


def fetch_day_issue_keys(
    headers: dict[str, str], base_url: str, day_iso: str
) -> list[str]:
    url = f"{base_url.rstrip('/')}/rest/api/2/search"
    params = {
        "jql": f'worklogAuthor=currentUser() AND worklogDate="{day_iso}"',
        "expand": "worklog",
        "fields": "key",
        "startAt": 0,
        "maxResults": 100,
    }
    keys: list[str] = []
    while True:
        payload = request_json("GET", url, headers, params=params)
        for issue in payload.get("issues", []):
            key = issue.get("key")
            if key:
                keys.append(key)
        start_at = payload.get("startAt", 0)
        max_results = payload.get("maxResults", 0)
        total = payload.get("total", len(keys))
        if start_at + max_results >= total:
            break
        params["startAt"] = start_at + max_results
    return keys


def fetch_issue_worklogs(
    headers: dict[str, str], base_url: str, issue_key: str
) -> list[dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/rest/api/2/issue/{issue_key}/worklog"
    start_at = 0
    max_results = 100
    all_logs: list[dict[str, Any]] = []
    while True:
        payload = request_json(
            "GET", url, headers, params={"startAt": start_at, "maxResults": max_results}
        )
        logs = payload.get("worklogs", [])
        all_logs.extend(logs)
        total = payload.get("total", len(all_logs))
        returned = payload.get("maxResults", len(logs))
        if start_at + returned >= total:
            break
        start_at += returned
    return all_logs


def post_worklog(
    headers: dict[str, str],
    base_url: str,
    issue_key: str,
    payload: dict[str, Any],
) -> None:
    url = f"{base_url.rstrip('/')}/rest/api/2/issue/{issue_key}/worklog"
    request_json("POST", url, headers, body=payload)


def subtract_one_month(today: date) -> date:
    year = today.year
    month = today.month - 1
    if month == 0:
        month = 12
        year -= 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def ask_date(prompt: str, default: date) -> date:
    while True:
        value = input(f"{prompt} [{default.strftime(DATE_INPUT_FORMAT)}]: ").strip()
        if not value:
            return default
        try:
            return datetime.strptime(value, DATE_INPUT_FORMAT).date()
        except ValueError:
            print("Invalid format. Use dd.mm.yyyy")


def ask_int(prompt: str, default: int, min_value: int = 1) -> int:
    while True:
        value = input(f"{prompt} [{default}]: ").strip()
        if not value:
            return default
        if value.isdigit() and int(value) >= min_value:
            return int(value)
        print(f"Enter an integer >= {min_value}")


def ask_weight(issue_key: str, summary: str) -> int:
    while True:
        value = input(f"Weight for {issue_key} ({summary}) [1-5]: ").strip()
        if value in {"1", "2", "3", "4", "5"}:
            return int(value)
        print("Weight must be an integer from 1 to 5.")


def format_token_for_display(token: str) -> str:
    if not token:
        return "<empty>"
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]}"


def ask_use_defaults() -> bool:
    print("\nDefault values from .env:")
    print(f"  - Jira base URL [DEFAULT_BASE_URL]: {DEFAULT_BASE_URL}")
    print(
        "  - Jira API token [DEFAULT_TOKEN]: "
        f"{format_token_for_display(DEFAULT_TOKEN)}"
    )
    print(f"  - Daily hours [DEFAULT_HOURS]: {DEFAULT_HOURS}")
    print(f"  - Max duration per entry [DEFAULT_MAX_DURATION]: {DEFAULT_MAX_DURATION}")
    print(
        "  - Task lookback in days [DEFAULT_TASK_DAYS_RANGE]: "
        f"{DEFAULT_TASK_DAYS_RANGE}"
    )
    while True:
        answer = input("Use these defaults? [Y/n]: ").strip().lower()
        if answer in {"", "y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Enter Y or N.")


def print_issues_table(issues: list[tuple[str, str]]) -> None:
    if not issues:
        return
    index_width = max(len("#"), len(str(len(issues))))
    key_width = max(len("Issue Key"), max(len(key) for key, _ in issues))
    summary_width = 90
    separator = f"+-{'-' * index_width}-+-{'-' * key_width}-+-{'-' * summary_width}-+"
    print("\nOpen issues:")
    print(separator)
    print(
        f"| {'#'.ljust(index_width)} | {'Issue Key'.ljust(key_width)} | "
        f"{'Summary'.ljust(summary_width)} |"
    )
    print(separator)
    for index, (key, summary) in enumerate(issues, start=1):
        summary_value = (
            summary if len(summary) <= summary_width else f"{summary[:summary_width - 3]}..."
        )
        print(
            f"| {str(index).ljust(index_width)} | {key.ljust(key_width)} | "
            f"{summary_value.ljust(summary_width)} |"
        )
    print(separator)


def parse_started_datetime(started: str) -> datetime:
    # Jira value example: 2026-02-23T10:00:00.000+0300
    return datetime.strptime(started, "%Y-%m-%dT%H:%M:%S.%f%z")


def calculate_logged_seconds_for_day(
    headers: dict[str, str], base_url: str, day: date
) -> int:
    day_iso = day.isoformat()
    keys = fetch_day_issue_keys(headers, base_url, day_iso)
    if not keys:
        return 0

    total_seconds = 0
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        try:
            worklogs = fetch_issue_worklogs(headers, base_url, key)
        except RuntimeError as exc:
            print(f"[WARN] Failed to fetch worklog for {key}: {exc}")
            continue
        for worklog in worklogs:
            started = worklog.get("started")
            seconds = int(worklog.get("timeSpentSeconds", 0) or 0)
            if not started or seconds <= 0:
                continue
            try:
                started_dt = parse_started_datetime(started)
            except ValueError:
                continue
            if started_dt.date() == day:
                total_seconds += seconds
    return total_seconds


def working_days(start_date: date, end_date: date) -> list[date]:
    days: list[date] = []
    current = start_date
    while current <= end_date:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def build_day_payloads(
    day: date,
    issues: list[Issue],
    remaining_seconds: int,
    max_task_hours: int,
) -> list[tuple[str, dict[str, Any]]]:
    hour_seconds = 3600
    max_chunk_seconds = max_task_hours * hour_seconds
    issue_keys = [x.key for x in issues]
    issue_weights = [x.weight for x in issues]

    payloads: list[tuple[str, dict[str, Any]]] = []
    current_dt = datetime.combine(day, time(hour=10, minute=0, second=0)).replace(
        tzinfo=timezone(timedelta(hours=3))
    )

    while remaining_seconds >= hour_seconds:
        max_allowed = min(max_chunk_seconds, remaining_seconds)
        max_hours = max_allowed // hour_seconds
        chunk_hours = random.randint(1, max_hours)
        chunk_seconds = chunk_hours * hour_seconds
        issue_key = random.choices(issue_keys, weights=issue_weights, k=1)[0]

        payload = {
            "comment": f"Work on task {issue_key}",
            "started": current_dt.strftime("%Y-%m-%dT%H:%M:%S.000%z"),
            "timeSpentSeconds": chunk_seconds,
        }
        payloads.append((issue_key, payload))

        current_dt += timedelta(seconds=chunk_seconds)
        remaining_seconds -= chunk_seconds

    return payloads


def print_summary(
    weighted_issues: list[Issue],
    start_date: date,
    end_date: date,
    daily_hours: int,
    max_task_hours: int,
    dry_run: bool,
) -> None:
    print("\n=== Summary ===")
    print(f"Period: {start_date.strftime(DATE_INPUT_FORMAT)} - {end_date.strftime(DATE_INPUT_FORMAT)}")
    print(f"Hours per day: {daily_hours}")
    print(f"Max single-task duration: {max_task_hours} h")
    print(f"Mode: {'DRY-RUN' if dry_run else 'REAL POST'}")
    print("Weights:")
    for issue in weighted_issues:
        print(f"  - {issue.key}: {issue.weight} ({issue.summary})")
    print("==============\n")


def ask_confirmation() -> bool:
    while True:
        answer = input("Confirm run? [y/n]: ").strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Enter y or n.")


def main() -> int:
    args = parse_args()
    try:
        headers = make_headers(args.token)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        return 1

    base_url = args.base_url.rstrip("/")
    use_defaults = ask_use_defaults()

    print(f"\nStep 1/5: loading open issues for the last {DEFAULT_TASK_DAYS_RANGE} days...")
    try:
        raw_issues = fetch_open_issues(headers, base_url, DEFAULT_TASK_DAYS_RANGE)
    except RuntimeError as exc:
        print(f"[ERROR] Failed to fetch issues: {exc}")
        return 1

    if not raw_issues:
        print("[ERROR] No open issues found for the configured JQL.")
        return 1

    issue_rows: list[tuple[str, str]] = []
    for item in raw_issues:
        key = item.get("key", "")
        fields = item.get("fields", {}) or {}
        summary = (fields.get("summary") or "").strip() or "no summary"
        if key:
            issue_rows.append((key, summary))

    if not issue_rows:
        print("[ERROR] No issues available for generation.")
        return 1

    print_issues_table(issue_rows)

    weighted_issues: list[Issue] = []
    for key, summary in issue_rows:
        weight = ask_weight(key, summary)
        weighted_issues.append(Issue(key=key, summary=summary, weight=weight))

    today = datetime.now().date()
    default_start = subtract_one_month(today)

    print("\nStep 2/5: period dates.")
    start_date = ask_date("Start date", default_start)
    end_date = ask_date("End date", today)
    if start_date > end_date:
        print("[ERROR] Start date is later than end date.")
        return 1

    print("\nStep 3/5: daily workload settings.")
    if use_defaults:
        daily_hours = DEFAULT_HOURS
        max_task_hours = DEFAULT_MAX_DURATION
        print(f"Using daily hours [DEFAULT_HOURS]: {daily_hours}")
        print(f"Using max duration per entry [DEFAULT_MAX_DURATION]: {max_task_hours}")
    else:
        daily_hours = ask_int("Hours to fill per day", default=DEFAULT_HOURS, min_value=1)
        max_task_hours = ask_int(
            "Maximum hours per entry", default=DEFAULT_MAX_DURATION, min_value=1
        )
    if max_task_hours > daily_hours:
        print("[ERROR] Max task duration cannot exceed daily hours.")
        return 1

    print("\nStep 4/5: confirmation.")
    print_summary(
        weighted_issues, start_date, end_date, daily_hours, max_task_hours, args.dry_run
    )
    if not ask_confirmation():
        print("Cancelled by user.")
        return 0

    days = working_days(start_date, end_date)
    if not days:
        print("No working days (Mon-Fri) in the selected range.")
        return 0

    created = 0
    skipped_days = 0
    errors = 0

    for day in days:
        try:
            logged_seconds = calculate_logged_seconds_for_day(headers, base_url, day)
        except RuntimeError as exc:
            print(f"[WARN] Failed to calculate logged time for {day.isoformat()}: {exc}")
            errors += 1
            continue

        target_seconds = daily_hours * 3600
        remaining_seconds = target_seconds - logged_seconds
        if remaining_seconds < 3600:
            print(
                f"[SKIP] {day.isoformat()}: already logged {logged_seconds / 3600:.2f}h, "
                "remaining < 1h."
            )
            skipped_days += 1
            continue

        payloads = build_day_payloads(day, weighted_issues, remaining_seconds, max_task_hours)
        if not payloads:
            print(f"[SKIP] {day.isoformat()}: nothing to add.")
            skipped_days += 1
            continue

        print(
            f"[DAY] {day.isoformat()} logged={logged_seconds/3600:.2f}h "
            f"to_add={sum(p['timeSpentSeconds'] for _, p in payloads)/3600:.2f}h"
        )
        for issue_key, payload in payloads:
            if args.dry_run:
                print(f"[DRY-RUN] {issue_key} {payload}")
                created += 1
                continue
            try:
                post_worklog(headers, base_url, issue_key, payload)
                print(
                    f"[OK] {issue_key} +{payload['timeSpentSeconds'] / 3600:.0f}h "
                    f"started={payload['started']}"
                )
                created += 1
            except RuntimeError as exc:
                print(f"[ERR] {issue_key}: {exc}")
                errors += 1

    print("\n=== Result ===")
    print(f"Working days: {len(days)}")
    print(f"Created entries: {created}")
    print(f"Skipped days: {skipped_days}")
    print(f"Errors: {errors}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
