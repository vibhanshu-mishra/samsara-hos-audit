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
import zoneinfo
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


def get_hos_violations(headers, driver_id, start_iso, end_iso):
    """
    Fetch HOS violations for a driver. This endpoint requires ISO 8601
    startTime/endTime parameters, NOT startMs/endMs like other HOS endpoints.
    Response structure is {"data": [{"violations": [...]}]} — violations
    are nested one level deeper than other endpoints.
    """
    try:
        resp = requests.get(
            f"{BASE_URL}/fleet/hos/violations",
            headers=headers,
            params={"driverIds": driver_id, "startTime": start_iso, "endTime": end_iso}
        )
        if resp.status_code != 200:
            return []
        results = []
        for entry in resp.json().get("data", []):
            results.extend(entry.get("violations", []))
        return results
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


def get_dvirs(headers, start_ms, end_ms):
    """
    Fetch all DVIRs for yesterday's window.
    No inspection_type filter — some drivers submit as "Unspecified"
    instead of "pretrip", so we fetch all types and accept any submission.
    """
    try:
        resp = requests.get(
            f"{BASE_URL}/v1/fleet/maintenance/dvirs",
            headers=headers,
            params={"start_ms": start_ms, "end_ms": end_ms}
        )
        if resp.status_code != 200:
            return []
        return resp.json().get("dvirs", [])
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
    ca_tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    dt = datetime.fromtimestamp(ms / 1000, tz=ca_tz)
    return dt.strftime("%Y-%m-%d %I:%M %p PT")


def ms_to_str_iso(iso_str):
    """Convert an ISO 8601 timestamp string to readable PT date/time."""
    if not iso_str:
        return "N/A"
    try:
        ca_tz = zoneinfo.ZoneInfo("America/Los_Angeles")
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00")).astimezone(ca_tz)
        return dt.strftime("%Y-%m-%d %I:%M %p PT")
    except Exception:
        return "N/A"


def get_all_hos_clocks(headers):
    """
    Fetch current HOS clocks for ALL drivers in one API call.
    Returns a dict of driver_id -> clock data.
    The cycleStartedAtTime field tells us when the driver last started
    a real cycle. Inactive drivers show today's auto-reset time.
    """
    try:
        resp = requests.get(f"{BASE_URL}/fleet/hos/clocks", headers=headers)
        if resp.status_code != 200:
            return {}
        clocks = {}
        for entry in resp.json().get("data", []):
            driver_id = entry.get("driver", {}).get("id")
            if driver_id:
                clocks[driver_id] = entry
        return clocks
    except Exception:
        return {}


def is_active_from_clocks(clock_entry, auto_reset_time):
    """
    A driver is inactive if their cycleStartedAtTime matches Samsara's
    auto-reset timestamp (meaning they have done nothing real recently).
    A driver is active if their cycleStartedAtTime is a real past date
    different from the auto-reset time.
    """
    if not clock_entry:
        return False
    cycle_started = clock_entry.get("clocks", {}).get("cycle", {}).get("cycleStartedAtTime", "")
    if not cycle_started:
        return False
    # If cycle started matches auto-reset time exactly — inactive
    if cycle_started == auto_reset_time:
        return False
    return True


# ── Core audit logic ──────────────────────────────────────────────────────────
def audit_driver(headers, driver, yesterday_start_ms, yesterday_end_ms,
                  yesterday_start_iso, yesterday_end_iso, hours_warning, dvirs_by_driver):
    driver_id = driver.get("id")
    issues = []

    # 1. HOS Violations (requires ISO startTime/endTime, not ms)
    violations = get_hos_violations(headers, driver_id, yesterday_start_iso, yesterday_end_iso)
    has_unsubmitted_logs_violation = False
    for v in violations:
        vtype = v.get("type", v.get("violationType", "Unknown violation"))
        # unsubmittedLogs is handled separately as MISSING DRIVER CERTIFICATION
        if vtype == "unsubmittedLogs":
            has_unsubmitted_logs_violation = True
            continue
        start_ms      = v.get("startMs")
        duration_mins = round(v.get("durationMs", 0) / 60000)
        if start_ms:
            detail = f"{vtype} — started {ms_to_str(start_ms)}, lasted {duration_mins} min"
        else:
            detail = f"{vtype} — lasted {duration_mins} min"
        issues.append({
            "category": "HOS VIOLATION",
            "detail":   detail
        })

    # 2. Daily log certification + shipping document ID
    # Real fields: isCertified (bool), logMetaData.shippingDocs (string, not array)
    daily_logs = get_daily_logs(headers, driver_id, yesterday_start_ms, yesterday_end_ms)

    # If unsubmittedLogs violation came through but daily-logs returned nothing, still flag it
    if has_unsubmitted_logs_violation and not daily_logs:
        if not any(i["category"] == "HOS - MISSING DRIVER CERTIFICATION" for i in issues):
            issues.append({
                "category": "HOS - MISSING DRIVER CERTIFICATION",
                "detail":   "Driver has unsubmitted logs for yesterday"
            })

    for log in daily_logs:
        log_date = ms_to_str(log.get("startMs")) if log.get("startMs") else ms_to_str_iso(log.get("startTime"))
        meta = log.get("logMetaData", {})
        is_certified = meta.get("isCertified", log.get("isCertified", False))

        if not is_certified or has_unsubmitted_logs_violation:
            # Only add once — unsubmittedLogs violation and isCertified=false are the same issue
            if not any(i["category"] == "HOS - MISSING DRIVER CERTIFICATION" for i in issues):
                issues.append({
                    "category": "HOS - MISSING DRIVER CERTIFICATION",
                    "detail":   f"Log for {log_date} was not certified by driver"
                })
            continue  # don't double-flag shipping ID if not even certified

        shipping_docs = meta.get("shippingDocs", log.get("shippingDocs", ""))
        if not shipping_docs or not str(shipping_docs).strip():
            issues.append({
                "category": "MISSING SHIPPING ID",
                "detail":   f"Log certified on {log_date} — no shipping document ID recorded"
            })

    # 3. DVIR check — did driver submit pretrip DVIRs yesterday?
    driver_dvirs = dvirs_by_driver.get(str(driver_id), [])
    if not driver_dvirs:
        issues.append({
            "category": "MISSING DVIR",
            "detail":   "No pretrip DVIR submitted for yesterday"
        })
    else:
        # Check if driver submitted a trailer DVIR (one with trailerName populated)
        # Each driver should submit two DVIRs: one for vehicle, one for trailer
        has_trailer_dvir = any(
            dvir.get("trailerName", "").strip() or
            str(dvir.get("trailerId", "0")) not in ("0", "")
            for dvir in driver_dvirs
        )
        if not has_trailer_dvir:
            issues.append({
                "category": "MISSING TRAILER DVIR",
                "detail":   "Vehicle DVIR submitted but no trailer DVIR found"
            })

    # 4. 70-hour weekly limit warning
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
    # All times calculated in California time (client's Samsara timezone)
    ca_tz              = zoneinfo.ZoneInfo("America/Los_Angeles")
    now                = datetime.now(tz=ca_tz)
    yesterday_start    = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_end      = yesterday_start + timedelta(days=1)
    yesterday_start_ms  = int(yesterday_start.timestamp() * 1000)
    yesterday_end_ms    = int(yesterday_end.timestamp() * 1000)
    yesterday_start_iso = yesterday_start.isoformat()
    yesterday_end_iso   = yesterday_end.isoformat()

    # Fetch & filter drivers
    print(f"\nConnecting to Samsara ({client_name})...")
    try:
        all_drivers = get_all_drivers(headers)
    except requests.exceptions.HTTPError as e:
        print(f"\n❌ API error — check your token.\nDetails: {e}\n")
        sys.exit(1)

    print(f"Total drivers in account: {len(all_drivers)}")
    print("Fetching HOS clocks...")

    all_clocks = get_all_hos_clocks(headers)

    # Detect Samsara's auto-reset timestamp (used for inactive drivers)
    # It's the most common cycleStartedAtTime across all drivers
    from collections import Counter
    cycle_times = [
        e.get("clocks", {}).get("cycle", {}).get("cycleStartedAtTime", "")
        for e in all_clocks.values()
    ]
    auto_reset_time = Counter(cycle_times).most_common(1)[0][0] if cycle_times else ""

    active_drivers = []
    for d in all_drivers:
        driver_id = d.get("id")
        clock_entry = all_clocks.get(driver_id)
        if is_active_from_clocks(clock_entry, auto_reset_time):
            active_drivers.append(d)

    print(f"Active drivers (real cycle activity): {len(active_drivers)}")
    print(f"Skipped inactive drivers: {len(all_drivers) - len(active_drivers)}")

    if not active_drivers:
        print("\n✅ No active drivers found. Nothing to audit.\n")
        sys.exit(0)

    # Fetch all DVIRs for yesterday in one call, index by driverId (as string)
    print("Fetching DVIRs...")
    raw_dvirs = get_dvirs(headers, yesterday_start_ms, yesterday_end_ms)
    dvirs_by_driver = {}
    for dvir in raw_dvirs:
        # driverId comes back as integer — convert to string to match driver IDs
        did = str(dvir.get("authorSignature", {}).get("driverId", ""))
        if did and did != "0":
            dvirs_by_driver.setdefault(did, []).append(dvir)

    # Audit each active driver
    flagged = []
    clean   = []

    for i, driver in enumerate(active_drivers, 1):
        name = driver.get("name", "Unknown Driver")
        print(f"  Auditing {i}/{len(active_drivers)}: {name}...", end="\r")
        issues = audit_driver(headers, driver, yesterday_start_ms, yesterday_end_ms,
                               yesterday_start_iso, yesterday_end_iso, hours_warn, dvirs_by_driver)
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
