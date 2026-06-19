#!/usr/bin/env python3
"""
Samsara HOS Audit Tool
======================
Fleet Regulators — Daily compliance audit for Samsara ELD clients.

Checks active drivers for:
  1. HOS violations flagged by Samsara
  2. Certified logs with missing shipping document IDs
  3. Drivers approaching 70-hour weekly limit (configurable threshold)

Usage:
  python3 audit.py                        # Uses config/settings.ini
  python3 audit.py --token YOUR_TOKEN     # Override token via CLI
  python3 audit.py --days 2               # Override active driver window
  python3 audit.py --client "ABC Trucking" # Label reports by client name
"""

import requests
import csv
import argparse
import configparser
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT        = Path(__file__).parent
CONFIG_FILE = ROOT / "config" / "settings.ini"
REPORTS_DIR = ROOT / "reports"

# ── Defaults (overridden by config file or CLI args) ─────────────────────────
DEFAULTS = {
    "active_days":     3,
    "hours_warning":   60,
    "client_name":     "Client",
}

BASE_URL = "https://api.samsara.com"


# ── Config loader ─────────────────────────────────────────────────────────────
def load_config():
    cfg = DEFAULTS.copy()
    if CONFIG_FILE.exists():
        parser = configparser.ConfigParser()
        parser.read(CONFIG_FILE)
        s = parser.get("settings", "active_days",   fallback=None)
        if s: cfg["active_days"]   = int(s)
        s = parser.get("settings", "hours_warning", fallback=None)
        if s: cfg["hours_warning"] = int(s)
        s = parser.get("settings", "client_name",   fallback=None)
        if s: cfg["client_name"]   = s
        token = parser.get("settings", "api_token", fallback=None)
        if token and token != "YOUR_API_TOKEN_HERE":
            cfg["api_token"] = token
    return cfg


# ── Samsara API helpers ───────────────────────────────────────────────────────
def get_headers(token):
    return {"Authorization": f"Bearer {token}"}


def paginated_get(url, headers, params=None):
    """Handles Samsara cursor-based pagination."""
    params = params or {}
    params["limit"] = 512
    results = []
    while True:
        resp = requests.get(url, headers=headers, params=params)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("data", []))
        pagination = data.get("pagination", {})
        if not pagination.get("hasNextPage"):
            break
        params["after"] = pagination["endCursor"]
    return results


def get_all_drivers(headers):
    return paginated_get(f"{BASE_URL}/fleet/drivers", headers)


def get_hos_violations(headers, driver_id, start_ms, end_ms):
    try:
        resp = requests.get(
            f"{BASE_URL}/fleet/hos/violations",
            headers=headers,
            params={"driverIds": driver_id, "startMs": start_ms, "endMs": end_ms}
        )
        if resp.status_code != 200:
            return []
        return resp.json().get("data", [])
    except Exception:
        return []


def get_daily_logs(headers, driver_id, start_ms, end_ms):
    try:
        resp = requests.get(
            f"{BASE_URL}/fleet/hos/daily-logs",
            headers=headers,
            params={"driverIds": driver_id, "startMs": start_ms, "endMs": end_ms}
        )
        if resp.status_code != 200:
            return []
        return resp.json().get("data", [])
    except Exception:
        return []


def get_hos_summary(headers, driver_id):
    try:
        resp = requests.get(
            f"{BASE_URL}/fleet/hos/summary",
            headers=headers,
            params={"driverIds": driver_id}
        )
        if resp.status_code != 200:
            return None
        summaries = resp.json().get("data", [])
        return summaries[0] if summaries else None
    except Exception:
        return None


# ── Helpers ───────────────────────────────────────────────────────────────────
def ms_to_str(ms):
    if not ms:
        return "N/A"
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d %I:%M %p")


def get_hos_logs_raw(headers, driver_id, start_ms, end_ms):
    """Fetch raw HOS duty status logs for a driver in a time range."""
    try:
        resp = requests.get(
            f"{BASE_URL}/fleet/hos/logs",
            headers=headers,
            params={"driverIds": driver_id, "startMs": start_ms, "endMs": end_ms}
        )
        if resp.status_code != 200:
            return []
        return resp.json().get("data", [])
    except Exception:
        return []


def is_active(headers, driver_id, seven_days_ago_ms, now_ms):
    """
    A driver is active if ANY status change occurred in the last 7 days.
    A driver is inactive only if there have been zero status changes
    for 7 or more days — meaning they are stuck on the same status
    the entire time with no activity whatsoever.
    """
    logs = get_hos_logs_raw(headers, driver_id, seven_days_ago_ms, now_ms)
    if not logs:
        return False
    for entry in logs:
        # If there are 2 or more hosLogs entries, a status change happened
        hos_logs = entry.get("hosLogs", [])
        if len(hos_logs) >= 2:
            return True
        # Even 1 log that started within the 7-day window means recent activity
        for log in hos_logs:
            start_time = log.get("logStartTime", "")
            if start_time:
                from datetime import datetime, timezone
                try:
                    start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                    start_ms = int(start_dt.timestamp() * 1000)
                    if start_ms >= seven_days_ago_ms:
                        return True
                except Exception:
                    pass
    return False


# ── Core audit logic ──────────────────────────────────────────────────────────
def audit_driver(headers, driver, yesterday_start_ms, yesterday_end_ms, hours_warning):
    driver_id = driver.get("id")
    issues = []

    # 1. HOS Violations
    violations = get_hos_violations(headers, driver_id, yesterday_start_ms, yesterday_end_ms)
    for v in violations:
        vtype         = v.get("violationType", "Unknown violation")
        vtime         = ms_to_str(v.get("startMs"))
        duration_mins = round(v.get("durationMs", 0) / 60000)
        issues.append({
            "category": "HOS VIOLATION",
            "detail":   f"{vtype} — started {vtime}, lasted {duration_mins} min"
        })

    # 2. Certified logs with missing shipping document IDs
    daily_logs = get_daily_logs(headers, driver_id, yesterday_start_ms, yesterday_end_ms)
    for log in daily_logs:
        if not log.get("certified", False):
            continue
        log_date     = ms_to_str(log.get("startMs"))
        shipping_docs = log.get("shippingDocs", [])
        if not shipping_docs:
            issues.append({
                "category": "MISSING SHIPPING ID",
                "detail":   f"Log certified on {log_date} — no shipping document ID recorded"
            })
        else:
            for doc in shipping_docs:
                if not doc.get("documentNumber", "").strip():
                    issues.append({
                        "category": "MISSING SHIPPING ID",
                        "detail":   f"Log certified on {log_date} — shipping doc entry is blank"
                    })

    # 3. 70-hour weekly limit warning
    summary = get_hos_summary(headers, driver_id)
    if summary:
        on_duty_ms    = summary.get("onDutyMs", 0)
        on_duty_hours = round(on_duty_ms / 3600000, 1)
        if on_duty_hours >= hours_warning:
            remaining = max(round(70 - on_duty_hours, 1), 0)
            issues.append({
                "category": "70-HOUR WARNING",
                "detail":   f"{on_duty_hours} hrs used in last 8 days — {remaining} hrs remaining"
            })

    return issues


# ── Report writers ────────────────────────────────────────────────────────────
def print_report(flagged, clean, total_active, run_time, client_name):
    print("\n" + "=" * 62)
    print(f"  {client_name.upper()} — SAMSARA HOS AUDIT")
    print(f"  {run_time.strftime('%Y-%m-%d %I:%M %p')}")
    print("=" * 62)

    print(f"\n🚨 FLAGGED DRIVERS ({len(flagged)})")
    print("-" * 62)
    if not flagged:
        print("  None — all active drivers are clean.")
    for d in flagged:
        print(f"\n  {d['name']}  (ID: {d['id']})")
        for issue in d["issues"]:
            print(f"    [{issue['category']}] {issue['detail']}")

    print(f"\n✅ CLEAN DRIVERS ({len(clean)})")
    print("-" * 62)
    for name in clean:
        print(f"  {name}")

    print(f"\n{'=' * 62}")
    print(f"  Active drivers audited : {total_active}")
    print(f"  Flagged                : {len(flagged)}")
    print(f"  Clean                  : {len(clean)}")
    print("=" * 62 + "\n")


def save_csv(flagged, clean, run_time, client_name):
    REPORTS_DIR.mkdir(exist_ok=True)
    filename = REPORTS_DIR / f"{client_name.replace(' ', '_')}_{run_time.strftime('%Y-%m-%d_%H%M')}.csv"

    rows = []
    for d in flagged:
        for issue in d["issues"]:
            rows.append({
                "date":       run_time.strftime("%Y-%m-%d"),
                "client":     client_name,
                "driver":     d["name"],
                "driver_id":  d["id"],
                "status":     "FLAGGED",
                "category":   issue["category"],
                "detail":     issue["detail"],
            })
    for name in clean:
        rows.append({
            "date":      run_time.strftime("%Y-%m-%d"),
            "client":    client_name,
            "driver":    name,
            "driver_id": "",
            "status":    "CLEAN",
            "category":  "",
            "detail":    "",
        })

    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "client", "driver", "driver_id", "status", "category", "detail"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"  📄 Report saved: {filename.name}\n")
    return filename


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    # CLI args
    parser = argparse.ArgumentParser(description="Samsara HOS Audit Tool")
    parser.add_argument("--token",  help="Samsara API token")
    parser.add_argument("--days",   type=int, help="Active driver window in days")
    parser.add_argument("--client", help="Client name for report labeling")
    args = parser.parse_args()

    # Merge: defaults → config file → CLI
    cfg = load_config()
    if args.token:  cfg["api_token"]    = args.token
    if args.days:   cfg["active_days"]  = args.days
    if args.client: cfg["client_name"]  = args.client

    if not cfg.get("api_token"):
        print("\n❌ No API token found.")
        print("   Add it to config/settings.ini or pass --token YOUR_TOKEN\n")
        sys.exit(1)

    token        = cfg["api_token"]
    active_days  = cfg["active_days"]
    hours_warn   = cfg["hours_warning"]
    client_name  = cfg["client_name"]
    headers      = get_headers(token)

    # Time windows
    now                = datetime.now(tz=timezone.utc)
    seven_days_ago_ms  = int((now - timedelta(days=7)).timestamp() * 1000)
    now_ms             = int(now.timestamp() * 1000)
    yesterday_start    = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_end      = yesterday_start + timedelta(days=1)
    yesterday_start_ms = int(yesterday_start.timestamp() * 1000)
    yesterday_end_ms   = int(yesterday_end.timestamp() * 1000)

    # Fetch & filter drivers
    print(f"\nConnecting to Samsara ({client_name})...")
    try:
        all_drivers = get_all_drivers(headers)
    except requests.exceptions.HTTPError as e:
        print(f"\n❌ API error — check your token.\nDetails: {e}\n")
        sys.exit(1)

    print(f"Total drivers in account: {len(all_drivers)}")
    print("Checking activity (last 7 days)...")

    active_drivers = []
    for d in all_drivers:
        if is_active(headers, d.get("id"), seven_days_ago_ms, now_ms):
            active_drivers.append(d)

    print(f"Active drivers (had ON/Driving in last 7 days): {len(active_drivers)}")
    print(f"Skipped inactive drivers: {len(all_drivers) - len(active_drivers)}")

    if not active_drivers:
        print("\n✅ No active drivers found. Nothing to audit.\n")
        sys.exit(0)

    # Audit each active driver
    flagged = []
    clean   = []

    for i, driver in enumerate(active_drivers, 1):
        name = driver.get("name", "Unknown Driver")
        print(f"  Auditing {i}/{len(active_drivers)}: {name}...", end="\r")
        issues = audit_driver(headers, driver, yesterday_start_ms, yesterday_end_ms, hours_warn)
        if issues:
            flagged.append({"name": name, "id": driver.get("id"), "issues": issues})
        else:
            clean.append(name)

    print(" " * 60, end="\r")  # clear progress line

    # Output
    print_report(flagged, clean, len(active_drivers), now, client_name)
    save_csv(flagged, clean, now, client_name)


if __name__ == "__main__":
    main()
