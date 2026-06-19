# Samsara HOS Audit Tool

Daily compliance audit script for Samsara ELD clients. Built for the Safety team's internal use.

## What it checks

Runs every morning and audits all active drivers from the previous day.

| Check | What it flags |
|---|---|
| HOS Violations | Any violation Samsara flagged — 11-hour driving limit, 14-hour window, 30-min break |
| Missing Shipping ID | Certified logs where shipping document ID is blank or missing entirely |
| Missing DVIR | Driver did not submit any pre-trip inspection for yesterday |
| Missing Trailer DVIR | Driver submitted a vehicle DVIR but no trailer DVIR |
| 70-Hour Warning | Drivers at 60+ hours of their weekly 70-hour limit |

**Active driver filter:** Drivers who have had zero real activity (stuck on the same status with no cycle activity) are automatically skipped. Only drivers with genuine recent activity are audited.

**What still needs human eyes:**
- Sleeper berth split validity
- Personal Conveyance distance abuse
- Yard Move distance abuse
- Log edit history (driver changed a log after the fact)
- Edge cases and carrier workarounds

## Setup

**1. Install Python**
If you don't have Python installed: https://www.python.org/downloads/

**2. Install dependencies**
```
pip3 install requests
```

**3. Create your config file**

In Terminal, run this command (replace the values with your actual token and client name):
```bash
cat > /path/to/samsara-hos-audit/config/settings.ini << 'CONF'
[settings]
api_token = YOUR_ACTUAL_TOKEN_HERE
client_name = Client Name Here
hours_warning = 60
CONF
```

Or copy the template and fill it in manually:
```
cp config/settings.ini.template config/settings.ini
```

The token needs **Global Read** access. Generate it at: Samsara → Settings → API Tokens.

## Running the audit

```bash
python3 /path/to/samsara-hos-audit/audit.py
```

The script will:
1. Fetch all drivers and filter to active ones only
2. Fetch all DVIRs for yesterday in one call
3. Check each active driver against all 5 checks
4. Print a flagged/clean summary to the terminal
5. Save a timestamped CSV report to the `reports/` folder

## Options

Override settings without editing the config file:

```bash
python3 audit.py --token YOUR_TOKEN
python3 audit.py --client "ABC Trucking"
python3 audit.py --client "ABC Trucking" --token YOUR_TOKEN
```

## Output

**Terminal:**
```
ABC TRUCKING — SAMSARA HOS AUDIT
2026-06-17 08:30 AM PT

🚨 FLAGGED DRIVERS (3)

  RKNO ONO  (ID: 5xxxxxx7)
    [HOS VIOLATION] 14HourDriving — started 2026-06-17 06:00 AM PT, lasted 45 min
    [MISSING SHIPPING ID] Log certified on 2026-06-17 — no shipping document ID recorded

  OINOI ONOIN  (ID: 5xxxxxx3)
    [MISSING DVIR] No pretrip DVIR submitted for yesterday

  AOINON ONOINO  (ID: 5xxxxxx0)
    [70-HOUR WARNING] 63.5 hrs used in last 8 days — 6.5 hrs remaining
    [MISSING TRAILER DVIR] Vehicle DVIR submitted but no trailer DVIR found

✅ CLEAN DRIVERS (11)
  VNSLN KOSNO
  ABCD EFGH
  ...
```

**CSV report** saved to `reports/Abc_Trucking_2026-06-17_0830.csv`

## Security

- `config/settings.ini` is in `.gitignore` — your API token will never be committed to GitHub
- The script only reads data from Samsara — it never writes or modifies anything
- Never share your `settings.ini` file with anyone

## Adding a new client

Each client needs their own Samsara API token. Run for a different client without changing your config:

```bash
python3 audit.py --token CLIENT_B_TOKEN --client "Client B Name"
```

---

Built by Fleet Regulators
