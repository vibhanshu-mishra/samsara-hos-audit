# Samsara HOS Compliance Audit Tool

A daily automated compliance audit script for Samsara ELD fleets. Built for internal use by trucking safety consultants and compliance teams.

The script runs every morning, pulls the previous day's data from Samsara's API, filters out inactive drivers automatically, and produces a prioritised compliance report grouped into Critical, Warning, Pending, and Clean categories so safety teams can focus on the issues that require action first.

---

## Features

- Automatically audits yesterday's Samsara HOS activity
- Uses Samsara HOS cycle clocks to automatically exclude inactive drivers
- Categorises findings into Critical, Warning, Pending, and Clean
- Detects HOS violations, uncertified logs, missing shipping IDs, missing DVIRs, missing trailer DVIRs, and 70-hour warnings
- Generates human-readable terminal and CSV reports
- Read-only access — never modifies Samsara data

---

## What it checks

### 1. HOS Violations
Pulls any violation that Samsara's system flagged for the previous day. This includes:
- **11-hour driving limit** — driver exceeded maximum daily drive time
- **14-hour on-duty window** — driver was on duty beyond the 14-hour window from first going on duty
- **30-minute break violation** — driver drove 8+ cumulative hours without a 30-minute break
- **Cycle and shift violations** — any weekly or shift-level hour limit breach

### 2. HOS - Missing Driver Certification
Checks whether the driver actually certified (signed off on) their daily log. An uncertified log is a compliance gap even if the hours themselves are fine — it means the driver never confirmed the log was accurate. This check catches the issue from two sources — Samsara's violations endpoint (which surfaces it as `unsubmittedLogs`) and the daily-logs endpoint (which exposes the `isCertified` field directly). If both flag the same driver, it only appears once in the report.

### 3. Missing Shipping Document ID
Even when drivers certify their logs, they sometimes leave the shipping document ID blank. Samsara does not flag this as a violation on its own. This check only runs on logs that were certified (an uncertified log is already flagged separately above, so it isn't double-counted).

### 4. Missing DVIR
Checks whether the driver submitted any pretrip Driver Vehicle Inspection Report for the previous day. Accepts all inspection types (pretrip, unspecified, etc.) since drivers sometimes submit under different type labels.

### 5. Missing Trailer DVIR
Drivers are expected to submit two DVIRs — one for the vehicle and one for the trailer. This check flags drivers who submitted a vehicle DVIR but forgot the trailer DVIR.

### 6. 70-Hour Weekly Limit Warning
Checks each driver's cumulative on-duty hours across the last 8 days. Flags anyone at or above the configured warning threshold (default: 60 hours) with how many hours they have remaining before hitting the 70-hour federal limit. This gives dispatchers time to act before a violation occurs.

---

## Issue Severity Levels

The audit categorizes findings into three severity levels to help compliance teams prioritize their work.

### 🚨 Critical

Issues requiring immediate attention.

Examples:
- 11-hour driving limit violations
- 14-hour on-duty violations
- 30-minute break violations
- Cycle violations
- Any HOS violation returned by Samsara

### ⚠️ Warning

Compliance issues that should be addressed but are not immediate HOS violations.

Examples:
- Missing shipping document ID
- Missing DVIR
- Missing trailer DVIR
- Drivers approaching the 70-hour limit
- Uncertified logs where the driver has already started today's shift

### 🟡 Pending

Issues that may resolve naturally and typically do not require immediate intervention.

Examples:
- Uncertified logs where the driver is still Off Duty or in a sleeper berth and has not yet started their current workday.

These drivers should be monitored and rechecked later in the day before escalating.

---

## Driver Certification Logic

The audit distinguishes between drivers who have not certified a log because they have not started their day and drivers who are actively working with uncertified logs.

Examples:

Driver sleeping at 8:00 AM:
- Yesterday's log is uncertified
- Current status: Sleeper Berth

Result:
🟡 Pending

The driver starts driving at 9:30 AM:
- Yesterday's log is still uncertified
- Current status: Driving

Result:
⚠️ Warning

This reduces false positives and helps compliance teams focus on drivers who require intervention.

---

## Active driver filter

Not all drivers in a Samsara account are active at any given time. Fleets often have drivers on leave, between assignments, or no longer active but still in the system. Auditing all of them wastes time and creates noise.

The script determines whether a driver is active by analysing HOS cycle clock data rather than relying on driver lists or recent log activity. This approach automatically excludes inactive drivers while still including drivers who are currently resting but have an active cycle, significantly reducing false positives and unnecessary compliance reviews. Samsara auto-resets every driver's cycle clock daily, so a driver whose `cycleStartedAtTime` matches that day's common auto-reset timestamp has had no real activity and is skipped. Drivers with a distinct, real cycle start time — even if they're currently sitting on a sleeper berth or off duty — are included in the audit.

---

## Why Pending Certifications Are Not Immediately Flagged

Many drivers certify logs after completing their required rest period and before beginning their next shift.

Flagging every uncertified log early in the morning creates unnecessary noise and can result in compliance teams chasing issues that would have resolved naturally.

The Pending category exists to identify logs that require monitoring rather than immediate action.

---

## Compliance Checks Not Yet Automated

The audit automates mechanical compliance checks well, but some areas still require human review or additional development.

Current examples include:

- Sleeper berth split validation (7/3, 8/2, etc.)
- Personal Conveyance Abuse Detection
- Yard Move abuse detection
- Log edit history monitoring
- Detection of suspicious driver behavior patterns
- Trend analysis across multiple days
- Driver compliance scoring

These checks involve contextual judgment or historical analysis and are planned for future versions of the tool.

---

## Audit Workflow

Every run follows the same process:

1. Fetch all drivers
2. Fetch HOS clocks
3. Filter inactive drivers
4. Fetch DVIRs
5. Audit each active driver
6. Categorise findings by severity
7. Generate terminal output
8. Export CSV report

---

## Setup

### 1. Install Python
Check if Python is installed:
```bash
python3 --version
```
If not installed, download from: https://www.python.org/downloads/

### 2. Install dependencies
```bash
pip3 install requests
```

### 3. Create your config file
Run this in Terminal, replacing the values with your actual details:
```bash
cat > /path/to/samsara-hos-audit/config/settings.ini << 'CONF'
[settings]
api_token = YOUR_SAMSARA_API_TOKEN
client_name = Your Client Name
hours_warning = 60
CONF
```

Alternatively, copy the included template and edit it:
```bash
cp config/settings.ini.template config/settings.ini
```

Then open `config/settings.ini` in any text editor and fill in your values.

### 4. Generate a Samsara API token
In Samsara: **Settings → API Tokens → Create New Token**
- Set tag access to the relevant organization
- Enable **Global Read** scope
- Copy the token and paste it into your `settings.ini`

---

## Running the audit

```bash
python3 /path/to/samsara-hos-audit/audit.py
```

The script will:
1. Connect to Samsara and fetch all drivers in the account
2. Pull HOS clock data for all drivers in one API call
3. Filter out inactive drivers automatically
4. Fetch all DVIRs for the previous day in one API call
5. Audit each active driver against all 6 checks
6. Print a complete flagged/clean report to the terminal
7. Save a timestamped CSV report to the `reports/` folder

---

## CLI options

You can override config file settings from the command line without editing any files:

```bash
# Use a specific token
python3 audit.py --token YOUR_TOKEN

# Label the report with a client name
python3 audit.py --client "Fleet Name"

# Combine options
python3 audit.py --token YOUR_TOKEN --client "Fleet Name"
```

This is useful when running audits for multiple clients — keep one config file as your default and override per client as needed.

---

## Output

### Terminal output
```
================================================================
  FLEET NAME — SAMSARA HOS AUDIT
  2026-06-17 08:30 AM PT
================================================================

🚨 FLAGGED DRIVERS (3)
----------------------------------------------------------------

  DRIVER NAME  (ID: XXXXXXX)
    [HOS VIOLATION] 14HourDriving — started 2026-06-17 06:00 AM PT, lasted 45 min
    [MISSING SHIPPING ID] Log certified on 2026-06-17 — no shipping document ID recorded

  DRIVER NAME  (ID: XXXXXXX)
    [HOS - MISSING DRIVER CERTIFICATION] Log for 2026-06-17 was not certified by driver
    [MISSING DVIR] No pretrip DVIR submitted for yesterday

  DRIVER NAME  (ID: XXXXXXX)
    [MISSING TRAILER DVIR] Vehicle DVIR submitted but no trailer DVIR found
    [70-HOUR WARNING] 63.5 hrs used in last 8 days — 6.5 hrs remaining

✅ CLEAN DRIVERS (12)
----------------------------------------------------------------
  Driver Name
  Driver Name
  ...

================================================================
  Active drivers audited : 15
  Flagged                : 3
  Clean                  : 12
================================================================

  📄 Report saved: Fleet_Name_2026-06-17_0830.csv
```

### CSV report
A CSV file is saved to the `reports/` folder on every run, named with the client name and timestamp. Each issue gets its own row with the following columns:

| Column | Description |
|---|---|
| date | Date of the audit |
| client | Client name from config |
| driver | Driver full name |
| driver_id | Samsara driver ID |
| status | FLAGGED or CLEAN |
| category | Issue type (HOS VIOLATION, HOS - MISSING DRIVER CERTIFICATION, MISSING DVIR, etc.) |
| detail | Full description of the issue |

Clean drivers appear as a single row with blank category and detail columns.

---

## Timezone

All timestamps and the "previous day" calculation are based on the client's local timezone, which should be configured to match where the fleet operates. Update the timezone in `audit.py` if your client is not in the Pacific timezone:

```python
ca_tz = zoneinfo.ZoneInfo("America/Los_Angeles")  # Change as needed
```

Common options:
- `America/Los_Angeles` — Pacific
- `America/Denver` — Mountain
- `America/Chicago` — Central
- `America/New_York` — Eastern

---

## A note on Samsara's API quirks

A few non-obvious things were discovered while building this tool, worth knowing if extending it further:

- The `/fleet/hos/violations` endpoint requires `startTime`/`endTime` as ISO 8601 strings — not `startMs`/`endMs` like most other HOS endpoints. Using the wrong parameter names returns a 200 status with an empty result instead of an error, so this kind of bug can fail silently.
- The `/fleet/hos/daily-logs` endpoint uses `startMs`/`endMs` correctly, and certification status lives under `logMetaData.isCertified`, with the shipping document field at `logMetaData.shippingDocs` as a plain string — not an array.
- The `/fleet/hos/clocks` endpoint returns more entries than `/fleet/drivers` does, since it includes drivers no longer in the active roster. This is expected and not a bug.
- DVIR `driverId` comes back as an integer from the DVIR endpoint but as a string from the drivers endpoint — always cast to string before matching.
- Drivers submit two separate DVIRs per pretrip inspection: one for the vehicle, one for the trailer. Don't assume a single DVIR record covers both.

---

## Security

- `config/settings.ini` is listed in `.gitignore` and will never be committed to GitHub
- The script is read-only — it never writes, edits, or modifies any data in Samsara
- Never share your `settings.ini` file or paste your API token into any chat or document
- If a token is accidentally exposed, revoke it immediately in Samsara and generate a new one

---

## Adding a new client

Each Samsara account requires its own API token. To run an audit for a different client without changing your default config:

```bash
python3 audit.py --token CLIENT_TOKEN --client "Client Fleet Name"
```

Reports for each client are saved separately in the `reports/` folder, labeled by client name and date.

---

## Disclaimer

This tool assists compliance teams by automating routine mechanical checks against Samsara data.

It does not replace a qualified safety professional or DOT compliance review. Certain compliance determinations require operational context and human judgment.

