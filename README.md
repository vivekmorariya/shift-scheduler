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
- **Weekly-block rotation:** Each employee stays on the same shift for an entire week (Mon–Sun). No mid-week shift changes
- **Forward-only rotation:** Shifts rotate 1→2→3→1. After Shift 3, only Shift 1 or 2 is allowed (never backwards)
- **Engineers always on Shift 1:** Engineers are prioritised for the morning shift at all times and are only moved if absolutely no other option exists
- **Mandatory rest day:** Whenever a shift changes at a week boundary, the first day of the new week is automatically blocked as a rest (W/off) day

### 📅 Weekly Off Rules
- Weekly offs are assigned as **consecutive 2-day pairs** (e.g. Sat+Sun, Mon+Tue, Thu+Fri)
- The first day of each pair **alternates** — off one week, working the next (e.g. 1st Saturday off, 2nd Saturday working)
- Weekly offs are **staggered** across the workforce so weekends are always adequately staffed
- Tuesday weekly offs are avoided to maximise attendance for the weekly Shift 1 meeting

### 🏭 Staffing Constraints
- Every shift must have **at least 1 Operator**
- Every shift must have **at least 1 support person** (Technician or Engineer)
- **Shift 1 must always have at least 1 Engineer**
- If a constraint is violated (e.g. due to leaves), the system automatically pulls in backup coverage

### 🌿 Leave Management
- Admin can input employee leave requests before generating a schedule
- The system respects leaves and avoids cancelling them unless absolutely necessary
- If a leave must be cancelled, the system prioritises cancelling from the employee who has taken the most leaves in the last 2 months
- All cancellations come with a written justification

### ✏️ Manual Override (Admin)
- Admins can click any individual cell in a saved schedule to change it (1, 2, 3, W, or L)
- Overrides are saved permanently in `data/manual_overrides.json`
- Future schedule regenerations **remember and re-apply** these manual overrides automatically
- Manually overridden cells are highlighted with a red border on the schedule view

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
