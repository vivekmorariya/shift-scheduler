# Shift Scheduler

Automated shift scheduling system for your team — accessible from any device on your WiFi network.

## Quick Start

1. **Double-click** `Start Scheduler.bat` to launch the server
2. Open a browser and go to `http://localhost:5000` (this PC) or the IP shown in the window (for Android/Manager)
3. Login and start generating schedules

## Login Credentials

| User    | Username  | Password  | Access                                  |
|---------|-----------|-----------|------------------------------------------|
| You (Admin) | `admin` | `admin123` | Full access — generate & manage schedules |
| Manager | `manager` | `mgr456`  | View schedules + download Excel          |

> **Tip:** Change passwords in `app.py` (the `USERS` dict) after setup.

## Files in this Project

```
Shift Scheduler/
├── app.py                  Flask web application
├── scheduler.py            Core scheduling logic
├── excel_export.py         Excel (.xlsx) export
├── Start Scheduler.bat     Double-click to start
├── templates/              HTML pages
│   ├── base.html           Layout with nav bar
│   ├── login.html          Login page
│   ├── index.html          Dashboard
│   ├── new_schedule.html   Month picker
│   ├── wizard_step1.html   New joiners
│   ├── wizard_step2.html   Leavers
│   ├── wizard_step3.html   Leave requests
│   ├── wizard_step4.html   Preview & confirm
│   ├── view_schedule.html  View saved schedule
│   └── employees.html      Employee directory
└── data/
    ├── employees.json       Employee master list
    ├── schedules.json       All saved schedules
    └── leave_history.json   Leave tracking history
```

## How to Generate a Monthly Schedule

1. Click **"Generate New Schedule"** on the dashboard
2. Select the **year and month**
3. **Step 1 – New Joiners:** Add any new employees (ID, name, role)
4. **Step 2 – Leavers:** Enter IDs of employees leaving the organisation
5. **Step 3 – Leave Requests:** Enter Employee ID and days requested off
6. **Step 4 – Preview:** Review the generated schedule, check warnings, then **Confirm & Save**
7. **Download Excel** directly from the dashboard or schedule view

## Scheduling Rules Applied

| Rule | Description |
|------|-------------|
| Operator coverage | At least 1 Operator in every shift, every day |
| Support coverage | At least 1 Engineer or Technician per shift (not Apprentices) |
| Engineer preference | Engineers assigned to Shift 1 (Morning) only unless absolutely needed |
| Weekly offs | Sundays + alternating Saturdays (1st, 3rd…) off by default |
| Shift change buffer | Minimum 2 weekly offs between consecutive shift changes |
| Leave planning | Leaves are honoured; if cancellation needed, employee with most leaves in last 2 months is cancelled first with justification |

## Accessing from Android

1. Make sure your phone is on the **same WiFi** as this PC
2. Note the IP address shown in the terminal when you start the server (e.g. `10.20.161.68`)
3. Open Chrome/any browser on your phone and go to: `http://10.20.161.68:5000`

## Leave History

The system automatically tracks leave history across months. When generating a new schedule:
- If a leave needs to be cancelled to maintain shift coverage, the system checks the last **2 months** of leave history
- The employee who has taken the **most leaves** gets their leave cancelled first
- A **justification is shown** on the preview screen and in the saved schedule

## Employee Colours

| Code | Colour | Meaning |
|------|--------|---------|
| `1` | 🔵 Light Blue | Morning Shift |
| `2` | 🟡 Light Yellow | Afternoon Shift |
| `3` | 🟠 Light Orange | Night Shift |
| `W` | 🟢 Light Green | Weekly Off |
| `L` | 🔴 Light Red | Leave |
