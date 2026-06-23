#!/usr/bin/env python3
"""
Samsara HOS Audit Tool
======================
Fleet Regulators — Daily compliance audit for Samsara ELD clients.

Checks active drivers for:
  1. HOS violations flagged by Samsara
  2. Missing driver log certification
  3. Certified logs with missing shipping document IDs
  4. Missing vehicle/trailer DVIRs
  5. Drivers approaching 70-hour weekly limit (configurable threshold)

Uses a 3-tier report system:
  CRITICAL = actual HOS violations
  WARNING  = issues requiring safety team follow-up
  PENDING  = likely driver action later, such as certification while still off duty/sleeper

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


def find_first_key(obj, possible_keys):
    """
    Recursively search a Samsara response for the first matching key.
    This makes the script more tolerant if Samsara nests duty status slightly
    differently across accounts or API versions.
    """
    if isinstance(obj, dict):
        for key in possible_keys:
            if key in obj and obj[key] not in (None, ""):
                return obj[key]
        for value in obj.values():
            found = find_first_key(value, possible_keys)
            if found not in (None, ""):
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = find_first_key(item, possible_keys)
            if found not in (None, ""):
                return found
    return None


def get_current_duty_status(clock_entry):
    """
    Pull the driver's current duty status from the HOS clock response.
    Common Samsara values include offDuty, sleeperBerth, driving, and onDuty.
    """
    status = find_first_key(clock_entry, [
        "currentDutyStatus",
        "dutyStatus",
        "hosStatusType",
        "status",
        "type",
    ])
    return str(status or "unknown")


def normalize_duty_status(status):
    cleaned = str(status or "unknown").replace("_", "").replace("-", "").replace(" ", "").lower()

    if cleaned in ("offduty", "off"):
        return "offDuty"
    if cleaned in ("sleeperberth", "sleeper"):
        return "sleeperBerth"
    if cleaned in ("driving", "drive"):
        return "driving"
    if cleaned in ("onduty", "ondutynotdriving", "notdriving"):
        return "onDuty"
    return status or "unknown"


def is_pending_certification_status(status):
    """
    If the driver is still Off Duty or Sleeper Berth at audit time, missing
    certification is treated as PENDING instead of WARNING.
    """
    normalized = normalize_duty_status(status)
    return normalized in ("offDuty", "sleeperBerth")


def make_issue(severity, category, detail):
    return {
        "severity": severity,
        "category": category,
        "detail": detail,
    }


# ── Core audit logic ──────────────────────────────────────────────────────────
def audit_driver(headers, driver, yesterday_start_ms, yesterday_end_ms,
                  yesterday_start_iso, yesterday_end_iso, hours_warning,
                  dvirs_by_driver, clock_entry):
    driver_id = driver.get("id")
    current_duty_status = normalize_duty_status(get_current_duty_status(clock_entry))
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
        issues.append(make_issue("CRITICAL", "HOS VIOLATION", detail))

    # 2. Daily log certification + shipping document ID
    # Real fields: isCertified (bool), logMetaData.shippingDocs (string, not array)
    daily_logs = get_daily_logs(headers, driver_id, yesterday_start_ms, yesterday_end_ms)

    # If unsubmittedLogs violation came through but daily-logs returned nothing, still flag it
    if has_unsubmitted_logs_violation and not daily_logs:
        if not any(i["category"] == "HOS - MISSING DRIVER CERTIFICATION" for i in issues):
            severity = "PENDING" if is_pending_certification_status(current_duty_status) else "WARNING"
            detail = f"Driver has unsubmitted logs for yesterday — current status: {current_duty_status}"
            issues.append(make_issue(severity, "HOS - MISSING DRIVER CERTIFICATION", detail))

    for log in daily_logs:
        log_date = ms_to_str(log.get("startMs")) if log.get("startMs") else ms_to_str_iso(log.get("startTime"))
        meta = log.get("logMetaData", {})
        is_certified = meta.get("isCertified", log.get("isCertified", False))

        if not is_certified or has_unsubmitted_logs_violation:
            # Only add once — unsubmittedLogs violation and isCertified=false are the same issue
            if not any(i["category"] == "HOS - MISSING DRIVER CERTIFICATION" for i in issues):
                severity = "PENDING" if is_pending_certification_status(current_duty_status) else "WARNING"
                detail = f"Log for {log_date} was not certified by driver — current status: {current_duty_status}"
                issues.append(make_issue(severity, "HOS - MISSING DRIVER CERTIFICATION", detail))
            continue  # don't double-flag shipping ID if not even certified

        shipping_docs = meta.get("shippingDocs", log.get("shippingDocs", ""))
        if not shipping_docs or not str(shipping_docs).strip():
            issues.append(make_issue(
                "WARNING",
                "MISSING SHIPPING ID",
                f"Log certified on {log_date} — no shipping document ID recorded"
            ))

    # 3. DVIR check — did driver submit pretrip DVIRs yesterday?
    driver_dvirs = dvirs_by_driver.get(str(driver_id), [])
    if not driver_dvirs:
        issues.append(make_issue("WARNING", "MISSING DVIR", "No pretrip DVIR submitted for yesterday"))
    else:
        # Check if driver submitted a trailer DVIR (one with trailerName populated)
        # Each driver should submit two DVIRs: one for vehicle, one for trailer
        has_trailer_dvir = any(
            dvir.get("trailerName", "").strip() or
            str(dvir.get("trailerId", "0")) not in ("0", "")
            for dvir in driver_dvirs
        )
        if not has_trailer_dvir:
            issues.append(make_issue(
                "WARNING",
                "MISSING TRAILER DVIR",
                "Vehicle DVIR submitted but no trailer DVIR found"
            ))

    # 4. 70-hour weekly limit warning
    summary = get_hos_summary(headers, driver_id)
    if summary:
        on_duty_ms    = summary.get("onDutyMs", 0)
        on_duty_hours = round(on_duty_ms / 3600000, 1)
        if on_duty_hours >= hours_warning:
            remaining = max(round(70 - on_duty_hours, 1), 0)
            issues.append(make_issue(
                "WARNING",
                "70-HOUR WARNING",
                f"{on_duty_hours} hrs used in last 8 days — {remaining} hrs remaining"
            ))

    return issues


# ── Report writers ────────────────────────────────────────────────────────────
def drivers_with_severity(flagged, severity):
    """Return drivers who have at least one issue at the selected severity."""
    selected = []
    for driver in flagged:
        issues = [i for i in driver["issues"] if i.get("severity") == severity]
        if issues:
            selected.append({**driver, "issues": issues})
    return selected


def print_issue_section(title, drivers):
    print(f"\n{title} ({len(drivers)})")
    print("-" * 62)
    if not drivers:
        print("  None")
        return
    for d in drivers:
        print(f"\n  {d['name']}  (ID: {d['id']})")
        for issue in d["issues"]:
            print(f"    [{issue['category']}] {issue['detail']}")


def print_report(flagged, clean, total_active, run_time, client_name):
    critical = drivers_with_severity(flagged, "CRITICAL")
    warning = drivers_with_severity(flagged, "WARNING")
    pending = drivers_with_severity(flagged, "PENDING")

    print("\n" + "=" * 62)
    print(f"  {client_name.upper()} — SAMSARA HOS AUDIT")
    print(f"  {run_time.strftime('%Y-%m-%d %I:%M %p')}")
    print("=" * 62)

    print_issue_section("🚨 CRITICAL — ACTION REQUIRED", critical)
    print_issue_section("⚠️  WARNING — SAFETY FOLLOW-UP", warning)
    print_issue_section("🟡 PENDING — CHECK LATER", pending)

    print(f"\n✅ CLEAN DRIVERS ({len(clean)})")
    print("-" * 62)
    for name in clean:
        print(f"  {name}")

    print(f"\n{'=' * 62}")
    print(f"  Active drivers audited : {total_active}")
    print(f"  Critical drivers       : {len(critical)}")
    print(f"  Warning drivers        : {len(warning)}")
    print(f"  Pending drivers        : {len(pending)}")
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
                "status":     issue.get("severity", "FLAGGED"),
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
        clock_entry = all_clocks.get(driver.get("id"))
        issues = audit_driver(headers, driver, yesterday_start_ms, yesterday_end_ms,
                               yesterday_start_iso, yesterday_end_iso, hours_warn,
                               dvirs_by_driver, clock_entry)
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
