# 📅 Shift Scheduler

An intelligent, automated shift scheduling web application built with Python and Flask. Designed for factories and 24/7 continuous operations — generates monthly shift rotations automatically while enforcing strict staffing rules, fairness, and leave management.

> Developed with ❤️ by **Vivek Morariya**

---

## ✨ Features

### 🔄 Automatic Schedule Generation
- Generates complete monthly shift schedules with a 4-step wizard
- Handles new joiners and employees leaving the organisation
- Remembers the last shift of previous month for seamless continuity

### 📋 Shift Rotation Rules
- **Weekly-Block Rotation:** Each employee stays on the same shift for an entire calendar week (Mon–Sun). Employees work consistent shifts without sudden mid-week jumps.
- **Strict Forward-Only Rotation:** Shifts rotate forward week-by-week: **1 → 2 → 3 → (W) → 1**. Backward rotations ($3 \rightarrow 1$, $3 \rightarrow 2$, $2 \rightarrow 1$) on consecutive days without rest are strictly forbidden.
- **Mandatory Rest Day on Shift Change:** Transitioning from Shift 3 to Shift 1 across a week boundary strictly requires a weekly off ($\text{W}$) rest day. An employee never works Shift 3 on Sunday night and Shift 1 on Monday morning.
- **Apprentice Shift Rotation:** Apprentices rotate through shifts in weekly blocks just like Operators and Technicians, maintaining 0 mid-week changes.
- **Engineer Shift 1 Priority:** Engineers are prioritized for Shift 1 (morning shift) rather than permanently locked, maintaining morning presence while retaining flexibility to step in for emergency support if necessary.
- **"Unless Required" Single-Day Relief:** A single-day shift change is permitted only when strictly necessary (if a shift would otherwise drop to 0 headcount due to overlapping offs/leaves). Relief is borrowed only from shifts with a surplus ($\ge 2$ workers) and must comply with rest day rules.

### 📅 Weekly Off Rules
- Weekly offs are assigned as **consecutive 2-day pairs** (e.g., Sat+Sun, Sun+Mon, Mon+Tue, etc.).
- The first day of each pair **alternates** — off one week, working the next (e.g., 1st Saturday off, 2nd Saturday working).
- Strict weekly limit: Employees receive at most 2 weekly offs in any calendar week (never 3).
- Weekly off pairs are **staggered across staff** using optimal combinations so all shifts maintain full headcount every day.

### 🏭 Staffing Constraints
- Every shift (1, 2, and 3) must have **at least 1 Operator** every single day.
- Every shift must have **at least 1 support person** (Technician or Engineer).
- **Shift 1 is staffed with an Engineer** whenever available.
- Full 24/7 continuous operations with 0 staffing shortages.

### 🌿 Leave Management & HR Rules
- **HR Weekly Off Protection:** Mandated weekly offs take precedence over leave requests. If an employee's requested leave period overlaps with their scheduled weekly off, that day is marked as Weekly Off ($\text{W}$), preserving the employee's leave balance.
- **Occupying Leaves via Whole-Week Shifts:** When leaves occur, the system rebalances shift assignments as whole-week blocks to cover all shifts without fragmenting individual weeks.
- **Fairness in Leave Cancellation:** If a leave must ever be cancelled due to severe staffing shortages, the system prioritizes cancelling from the employee who took the most leaves in the preceding 2 months, accompanied by written justification.

### ✏️ Manual Override & Learning
- Admins can manually edit any shift or weekly off cell directly in **Step 4 (Preview)** before saving, or in the saved schedule view.
- Manual overrides are permanently saved in `data/manual_overrides.json` and remembered for future schedule generations.
- Manually overridden cells are clearly highlighted in the schedule view.

### 👥 Employee Management
- View all employees grouped by role (Engineers, Operators, Technicians, Apprentices)
- Change an employee's role instantly with a dropdown (promotion/demotion)
- Add new joiners and mark leavers through the New Schedule wizard

### 🔐 User Management (Admin)
- Create new application login accounts with a username, password, and role
- Two roles: **Admin** (full access) and **Manager** (view & download only)
- Change any user's password at any time
- Delete users (cannot delete your own account)
- All users stored securely in `data/users.json`

### 📊 Dashboard & Reports
- Dashboard lists all generated schedules
- View full schedule in a colour-coded table with daily headcount summary
- Download any schedule as a formatted **Excel file**
- Accessible from any device on the same network (Android, tablet, laptop)

---

## 🚀 How to Run Locally

1. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Start the Server:**
   ```bash
   python app.py
   ```
   Or use the included `Start Scheduler.bat` file which also prints your local network URL.

3. **Access the App:**
   - Open your browser and go to `http://localhost:5000`
   - Log in with your Admin or Manager credentials.

---

## ☁️ Live Deployment

This app is deployed on **PythonAnywhere** and accessible from anywhere in the world:

🌐 **[vivekmorariya.pythonanywhere.com](http://vivekmorariya.pythonanywhere.com)**

---

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10, Flask 3.1 |
| Frontend | HTML5, Bootstrap 5, Jinja2 |
| Data Storage | JSON files (no database required) |
| Export | OpenPyXL (Excel .xlsx) |
| Hosting | PythonAnywhere (Free tier) |

---

## 📁 Project Structure

```
shift-scheduler/
├── app.py                  # Flask routes and web application
├── scheduler.py            # Core scheduling engine and all rules
├── excel_export.py         # Excel file generation
├── requirements.txt        # Python dependencies
├── templates/              # HTML templates (Jinja2)
│   ├── base.html
│   ├── login.html
│   ├── index.html
│   ├── employees.html
│   ├── manage_users.html
│   ├── view_schedule.html
│   ├── wizard_step1–4.html
│   └── ...
└── data/                   # JSON data files (auto-created)
    ├── employees.json      # Employee roster
    ├── schedules.json      # Generated schedules
    ├── leave_history.json  # Leave history for fairness tracking
    ├── users.json          # Application login users
    └── manual_overrides.json # Saved manual cell edits
```
