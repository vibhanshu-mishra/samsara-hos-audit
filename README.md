# Samsara HOS Audit Tool

A daily automated compliance audit script for Samsara ELD fleets. Built for internal use by trucking safety consultants and compliance teams.

The script can be run every morning to pull the previous day's data from Samsara's API, filter out inactive drivers automatically, and produce a clean flagged/clean report, so you only spend time investigating real issues instead of manually clicking through every driver's log.

---

## What it checks

### 1. HOS Violations
Pulls any violation that Samsara's system automatically flagged for the previous day. This includes:
- **11-hour driving limit** — driver exceeded maximum daily drive time
- **14-hour on-duty window** — driver was on duty beyond the 14-hour window from first going on duty
- **30-minute break violation** — driver drove 8+ cumulative hours without a 30-minute break
- **Cycle violations** — any weekly hour limit breach

### 2. Missing Shipping Document ID
Even when drivers certify their logs, they sometimes leave the shipping document ID blank. Samsara does not flag this as a violation on its own. This check catches:
- Certified logs with no shipping document entry at all
- Certified logs where a shipping document entry exists but the document number is blank

### 3. Missing DVIR
Checks whether the driver submitted a pre-trip Driver Vehicle Inspection Report for the previous day. Accepts all inspection types (pretrip, unspecified, etc.) since drivers sometimes submit under different type labels.

### 4. Missing Trailer DVIR
Drivers are expected to submit two DVIRs — one for the vehicle and one for the trailer. This check flags drivers who submitted a vehicle DVIR but forgot the trailer DVIR.

### 5. 70-Hour Weekly Limit Warning
Checks each driver's cumulative on-duty hours across the last 8 days. Flags anyone at or above the configured warning threshold (default: 60 hours) with how many hours they have remaining before hitting the 70-hour federal limit. This gives dispatchers time to act before a violation occurs.

---

## Active driver filter

Not all drivers in a Samsara account are active at any given time. Fleets often have drivers on leave, between assignments, or no longer active but still in the system. Auditing all of them wastes time and creates noise.

The script determines whether a driver is active by checking their HOS cycle data. Drivers whose cycle clock shows no real activity — meaning Samsara's system auto-resets their cycle rather than them starting a genuine new shift — are automatically skipped. Only drivers with confirmed real activity are included in the audit.

---

## What still requires human review

The script handles mechanical checks well, but some compliance issues require judgment that automation cannot reliably provide:

- **Sleeper berth split validity** — verifying the 7-hour + 2-hour split rule was followed correctly
- **Personal Conveyance abuse** — PC logged for unreasonably long distances
- **Yard Move abuse** — Yard Move covering distances inconsistent with on-site movement
- **Log edit history** — drivers editing logs after the fact (a major red flag in DOT audits)
- **Edge cases and carrier workarounds** — patterns that appear compliant on paper but aren't

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
5. Audit each active driver against all 5 checks
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
    [MISSING DVIR] No pre-trip DVIR submitted for yesterday
    [70-HOUR WARNING] 63.5 hrs used in last 8 days — 6.5 hrs remaining

  DRIVER NAME  (ID: XXXXXXX)
    [MISSING TRAILER DVIR] Vehicle DVIR submitted, but no trailer DVIR found

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
| category | Issue type (HOS VIOLATION, MISSING DVIR, etc.) |
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

Reports for each client are saved separately in the `reports/` folder, labelled by client name and date.

---

Built by Fleet Regulators
