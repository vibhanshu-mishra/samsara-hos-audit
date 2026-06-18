# Samsara HOS Audit Tool

Daily compliance audit script for Samsara ELD clients. 

## What it checks

For every driver active in the last 3 days:

| Check | What it flags |
|---|---|
| HOS Violations | Any violation Samsara flagged (11-hour, 14-hour, 30-min break, etc.) |
| Missing Shipping IDs | Certified logs where shipping document ID is blank |
| 70-Hour Warning | Drivers at 60+ hours of their weekly 70-hour limit |

Inactive drivers (no activity in last 3 days) are automatically skipped.

## Setup

**1. Install Python**
If you don't have Python installed: https://www.python.org/downloads/

**2. Install dependencies**
```
pip3 install -r requirements.txt
```

**3. Configure your client**

Copy the template config and fill it in:
```
cp config/settings.ini.template config/settings.ini
```

Open `config/settings.ini` and set:
- `api_token` — your Samsara API token (generate at Samsara → Settings → API Tokens, use read-only access)
- `client_name` — used to label your saved reports

## Running the audit

```bash
python3 audit.py
```

The script will:
1. Print a summary to the terminal
2. Save a timestamped CSV report to the `reports/` folder

## Options

You can override settings without editing the config file:

```bash
python3 audit.py --token YOUR_TOKEN
python3 audit.py --client "ABC Trucking"
python3 audit.py --days 2
python3 audit.py --client "ABC Trucking" --token YOUR_TOKEN --days 2
```

## Output

**Terminal:**
```
ABC TRUCKING — SAMSARA HOS AUDIT
2024-01-15 08:30 AM

🚨 FLAGGED DRIVERS (2)
  John Smith  (ID: 12345)
    [HOS VIOLATION] 14HourDriving — started 2024-01-14 06:00 AM, lasted 45 min
    [70-HOUR WARNING] 63.5 hrs used in last 8 days — 6.5 hrs remaining

  Maria Garcia  (ID: 67890)
    [MISSING SHIPPING ID] Log certified on 2024-01-14 — no shipping document ID recorded

✅ CLEAN DRIVERS (10)
  ...
```

**CSV report** saved to `reports/ABC_Trucking_2024-01-15_0830.csv`

## Security

- `config/settings.ini` is in `.gitignore` — your API token will never be committed to GitHub
- Use a **read-only** Samsara API token — the script never writes anything to Samsara
- Never share your `settings.ini` file

## Adding a new client

Each client needs their own Samsara API token. To run the audit for a different client without changing your config file:

```bash
python3 audit.py --token CLIENT_B_TOKEN --client "Client B Name"
```
