"""
Shift Scheduler – Flask Web Application
Accessible from any device on the same local network.
"""

import json
import calendar
from datetime import date
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, session, send_file, jsonify
)
import io

from scheduler import (
    load_employees, save_employees, get_schedule, save_schedule,
    generate_schedule, find_employee, get_all_employees_flat,
    month_name, day_name, ROLE_ORDER, load_schedules,
    record_manual_override, get_manual_overrides_for_month
)
from excel_export import export_to_excel

app = Flask(__name__)
app.secret_key = "shift_scheduler_secret_2026"   # change for production
app.jinja_env.add_extension("jinja2.ext.do")

# Add enumerate to Jinja2 globals
app.jinja_env.globals["enumerate"] = enumerate

import hashlib
from pathlib import Path

USERS_FILE = Path(__file__).parent / "data" / "users.json"

def load_users() -> dict:
    if not USERS_FILE.exists():
        default = {
            "admin":   {"password": "admin123", "role": "admin"},
            "manager": {"password": "mgr456",   "role": "manager"},
        }
        USERS_FILE.write_text(json.dumps(default, indent=2))
        return default
    with open(USERS_FILE) as f:
        return json.load(f)

def save_users(data: dict):
    with open(USERS_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ─────────────────────────────────────────────────────────────────────────────
# Auth helpers
# ─────────────────────────────────────────────────────────────────────────────

def current_user():
    return session.get("user")


def require_login(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user():
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def require_admin(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user():
            return redirect(url_for("login"))
        users = load_users()
        if users.get(current_user(), {}).get("role") != "admin":
            flash("Admin access required.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────────────────────────────────────
# Auth routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        users = load_users()
        user = users.get(username)
        if user and user["password"] == password:
            session["user"] = username
            session["role"] = user["role"]
            flash(f"Welcome, {username}!", "success")
            return redirect(url_for("index"))
        flash("Invalid username or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.", "info")
    return redirect(url_for("login"))


# ─────────────────────────────────────────────────────────────────────────────
# Main dashboard
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/")
@require_login
def index():
    schedules = load_schedules()
    # Build summary list
    schedule_list = []
    for s in schedules:
        y, m = s["year"], s["month"]
        schedule_list.append({
            "key": s["key"],
            "label": f"{month_name(m)} {y}",
            "year": y,
            "month": m,
            "warnings": len(s.get("warnings", [])),
            "cancelled": len(s.get("cancelled_leaves", [])),
        })
    schedule_list.sort(key=lambda x: (x["year"], x["month"]), reverse=True)
    return render_template(
        "index.html",
        schedule_list=schedule_list,
        user=current_user(),
        role=session.get("role"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# New schedule wizard  (multi-step via session)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/new-schedule", methods=["GET", "POST"])
@require_admin
def new_schedule():
    if request.method == "POST":
        year = int(request.form["year"])
        month = int(request.form["month"])
        session["wizard"] = {
            "year": year,
            "month": month,
            "new_employees": [],
            "leaving_ids": [],
            "leaves": {},   # {emp_id: [day, ...]}
        }
        return redirect(url_for("wizard_step", step=1))

    today = date.today()
    # Suggest next month
    next_month = today.month % 12 + 1
    next_year = today.year + (1 if today.month == 12 else 0)
    years = list(range(today.year, today.year + 3))
    return render_template("new_schedule.html", years=years,
                           default_year=next_year, default_month=next_month,
                           user=current_user())


@app.route("/wizard/<int:step>", methods=["GET", "POST"])
@require_admin
def wizard_step(step):
    if "wizard" not in session:
        return redirect(url_for("new_schedule"))

    wizard = session["wizard"]
    employees = load_employees()
    all_emps = get_all_employees_flat(employees)

    if step == 1:
        # New joiners
        if request.method == "POST":
            action = request.form.get("action")
            if action == "add":
                new_id = request.form.get("emp_id", "").strip()
                new_name = request.form.get("emp_name", "").strip()
                new_role = request.form.get("emp_role", "").strip()
                if new_id and new_name and new_role:
                    wizard["new_employees"].append({
                        "id": new_id, "name": new_name, "role": new_role
                    })
                    session.modified = True
                    flash(f"Added new joiner: {new_name}", "success")
            elif action == "remove":
                idx = int(request.form.get("idx", -1))
                if 0 <= idx < len(wizard["new_employees"]):
                    wizard["new_employees"].pop(idx)
                    session.modified = True
            elif action == "next":
                return redirect(url_for("wizard_step", step=2))
        return render_template("wizard_step1.html", wizard=wizard,
                               roles=["Engineers", "Operators", "Technicians", "Apprentices"],
                               user=current_user())

    elif step == 2:
        # Leaving employees
        if request.method == "POST":
            action = request.form.get("action")
            if action == "add":
                leaving_id = request.form.get("leaving_id", "").strip()
                if leaving_id:
                    emp = find_employee(leaving_id, employees)
                    if emp:
                        if leaving_id not in wizard["leaving_ids"]:
                            wizard["leaving_ids"].append(leaving_id)
                            session.modified = True
                            flash(f"Marked {emp['name']} as leaving.", "success")
                    else:
                        flash(f"Employee ID {leaving_id} not found.", "warning")
            elif action == "remove":
                idx = int(request.form.get("idx", -1))
                if 0 <= idx < len(wizard["leaving_ids"]):
                    wizard["leaving_ids"].pop(idx)
                    session.modified = True
            elif action == "next":
                return redirect(url_for("wizard_step", step=3))
            elif action == "back":
                return redirect(url_for("wizard_step", step=1))
        leaving_details = []
        for eid in wizard["leaving_ids"]:
            emp = find_employee(eid, employees)
            if emp:
                leaving_details.append(emp)
        return render_template("wizard_step2.html", wizard=wizard,
                               leaving_details=leaving_details,
                               user=current_user())

    elif step == 3:
        # Leave requests
        year = wizard["year"]
        month = wizard["month"]
        days_in_month = calendar.monthrange(year, month)[1]
        if request.method == "POST":
            action = request.form.get("action")
            if action == "add":
                leave_id = request.form.get("leave_emp_id", "").strip()
                leave_days_raw = request.form.get("leave_days", "").strip()
                emp = find_employee(leave_id, employees)
                # Also check new employees
                if not emp:
                    for ne in wizard["new_employees"]:
                        if str(ne["id"]) == str(leave_id):
                            emp = ne
                            break
                if emp and leave_days_raw:
                    try:
                        leave_days = [int(d.strip()) for d in leave_days_raw.split(",") if d.strip()]
                        leave_days = [d for d in leave_days if 1 <= d <= days_in_month]
                        if leave_id not in wizard["leaves"]:
                            wizard["leaves"][leave_id] = []
                        for d in leave_days:
                            if d not in wizard["leaves"][leave_id]:
                                wizard["leaves"][leave_id].append(d)
                        session.modified = True
                        flash(f"Leave added for {emp['name']} on days: {leave_days}", "success")
                    except ValueError:
                        flash("Invalid day format. Use comma-separated numbers.", "danger")
                else:
                    flash("Employee not found or no days specified.", "warning")
            elif action == "remove":
                emp_id = request.form.get("rm_emp_id", "").strip()
                day = int(request.form.get("rm_day", 0))
                if emp_id in wizard["leaves"] and day in wizard["leaves"][emp_id]:
                    wizard["leaves"][emp_id].remove(day)
                    if not wizard["leaves"][emp_id]:
                        del wizard["leaves"][emp_id]
                    session.modified = True
            elif action == "generate":
                return redirect(url_for("wizard_step", step=4))
            elif action == "back":
                return redirect(url_for("wizard_step", step=2))

        # Build leave display
        leave_display = []
        for eid, dlist in wizard["leaves"].items():
            emp = find_employee(eid, employees)
            if not emp:
                for ne in wizard["new_employees"]:
                    if str(ne["id"]) == str(eid):
                        emp = ne; break
            name = emp["name"] if emp else eid
            for d in sorted(dlist):
                leave_display.append({"emp_id": eid, "name": name, "day": d,
                                      "day_name": day_name(year, month, d)})
        return render_template(
            "wizard_step3.html", wizard=wizard,
            leave_display=leave_display,
            all_emps=all_emps, days_in_month=days_in_month,
            month_name=month_name(month), year=year,
            user=current_user()
        )

    elif step == 4:
        # Preview / Generate
        if request.method == "POST":
            action = request.form.get("action")
            if action == "confirm":
                result = generate_schedule(
                    wizard["year"], wizard["month"],
                    wizard["leaves"], wizard["new_employees"],
                    wizard["leaving_ids"]
                )
                save_schedule(result)
                # Persist employee changes (new joiners & leavers)
                employees = load_employees()
                for leaving_id in wizard["leaving_ids"]:
                    for role in employees:
                        employees[role] = [e for e in employees[role]
                                           if str(e["id"]) != str(leaving_id)]
                for ne in wizard["new_employees"]:
                    role = ne["role"]
                    if role not in employees:
                        employees[role] = []
                    exists = any(str(e["id"]) == str(ne["id"]) for e in employees[role])
                    if not exists:
                        employees[role].append({"id": str(ne["id"]), "name": ne["name"]})
                save_employees(employees)
                session.pop("wizard", None)
                flash(f"Schedule for {month_name(result['month'])} {result['year']} generated!", "success")
                return redirect(url_for("view_schedule",
                                        year=result["year"], month=result["month"]))
            elif action == "back":
                return redirect(url_for("wizard_step", step=3))

        # Generate preview
        result = generate_schedule(
            wizard["year"], wizard["month"],
            wizard["leaves"], wizard["new_employees"],
            wizard["leaving_ids"]
        )
        overrides = get_manual_overrides_for_month(wizard["year"], wizard["month"])
        return render_template(
            "wizard_step4.html", wizard=wizard,
            result=result,
            month_name=month_name(wizard["month"]),
            role_order=["Engineers", "Operators", "Technicians", "Apprentices"],
            day_name_fn=day_name,
            user=current_user(),
            role=session.get("role"),
            overrides=overrides,
        )

    return redirect(url_for("index"))


@app.route("/wizard/override", methods=["POST"])
@require_admin
def wizard_override():
    wizard = session.get("wizard")
    if not wizard:
        flash("No active wizard session.", "warning")
        return redirect(url_for("new_schedule"))

    emp_id = request.form.get("emp_id", "").strip()
    day = request.form.get("day", "").strip()
    new_val = request.form.get("new_val", "").strip().upper()

    valid_vals = {"1", "2", "3", "W", "L"}
    if not emp_id or not day.isdigit() or new_val not in valid_vals:
        flash("Invalid override data.", "danger")
        return redirect(url_for("wizard_step", step=4))

    record_manual_override(wizard["year"], wizard["month"], emp_id, int(day), new_val)
    flash(f"Manual edit saved: Employee {emp_id}, Day {day} → {new_val}", "success")
    return redirect(url_for("wizard_step", step=4))


# ─────────────────────────────────────────────────────────────────────────────
# View schedule
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/schedule/<int:year>/<int:month>")
@require_login
def view_schedule(year, month):
    result = get_schedule(year, month)
    if not result:
        flash("Schedule not found.", "warning")
        return redirect(url_for("index"))
    overrides = get_manual_overrides_for_month(year, month)
    return render_template(
        "view_schedule.html",
        result=result,
        month_name=month_name(month),
        role_order=["Engineers", "Operators", "Technicians", "Apprentices"],
        user=current_user(),
        role=session.get("role"),
        day_name_fn=day_name,
        overrides=overrides,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Manual override – Admin can click individual cells to change assignment
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/schedule/<int:year>/<int:month>/override", methods=["POST"])
@require_admin
def manual_override(year, month):
    """Save a manual cell edit, update the stored schedule, and redirect back."""
    emp_id   = request.form.get("emp_id", "").strip()
    day      = request.form.get("day", "").strip()
    new_val  = request.form.get("new_val", "").strip().upper()

    valid_vals = {"1", "2", "3", "W", "L"}
    if not emp_id or not day.isdigit() or new_val not in valid_vals:
        flash("Invalid override data.", "danger")
        return redirect(url_for("view_schedule", year=year, month=month))

    day = int(day)

    # Persist the override so it survives regeneration (Rule 5 – app learns)
    record_manual_override(year, month, emp_id, day, new_val)

    # Also patch the live schedule JSON immediately
    from scheduler import load_schedules, save_schedules
    schedules = load_schedules()
    key = f"{year}-{month:02d}"
    for s in schedules:
        if s.get("key") == key:
            emp_sch = s["employee_schedules"].setdefault(emp_id, {})
            emp_sch[str(day)] = new_val
            break
    save_schedules(schedules)

    flash(f"Cell updated: Employee {emp_id}, Day {day} → {new_val}", "success")
    return redirect(url_for("view_schedule", year=year, month=month))


# ─────────────────────────────────────────────────────────────────────────────
# Download Excel
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/schedule/<int:year>/<int:month>/download")
@require_login
def download_schedule(year, month):
    result = get_schedule(year, month)
    if not result:
        flash("Schedule not found.", "warning")
        return redirect(url_for("index"))
    xlsx_bytes = export_to_excel(result)
    filename = f"Shift_Schedule_{calendar.month_abbr[month]}_{year}.xlsx"
    return send_file(
        io.BytesIO(xlsx_bytes),
        download_name=filename,
        as_attachment=True,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Employee management
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/employees")
@require_login
def employees():
    emps = load_employees()
    return render_template("employees.html", employees=emps,
                           role_order=["Engineers", "Operators", "Technicians", "Apprentices"],
                           user=current_user(), role=session.get("role"))

@app.route("/change-role", methods=["POST"])
@require_admin
def change_role():
    emp_id = request.form.get("emp_id")
    current_role = request.form.get("current_role")
    new_role = request.form.get("new_role")
    
    if not all([emp_id, current_role, new_role]) or current_role == new_role:
        return redirect(url_for("employees"))
        
    emps = load_employees()
    
    # Find employee
    target_emp = None
    if current_role in emps:
        for i, e in enumerate(emps[current_role]):
            if str(e["id"]) == str(emp_id):
                target_emp = emps[current_role].pop(i)
                break
                
    if target_emp:
        if new_role not in emps:
            emps[new_role] = []
        emps[new_role].append(target_emp)
        save_employees(emps)
        flash(f"Role updated successfully for {target_emp['name']}.", "success")
    else:
        flash("Employee not found.", "danger")
        
    return redirect(url_for("employees"))


# ─────────────────────────────────────────────────────────────────────────────
# User management (Admin only)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/users")
@require_admin
def manage_users():
    users = load_users()
    return render_template("manage_users.html",
                           users=users,
                           user=current_user(),
                           role=session.get("role"))

@app.route("/users/create", methods=["POST"])
@require_admin
def create_user():
    username = request.form.get("username", "").strip().lower()
    password = request.form.get("password", "").strip()
    role     = request.form.get("role", "manager").strip()

    if not username or not password:
        flash("Username and password are required.", "danger")
        return redirect(url_for("manage_users"))

    if role not in ("admin", "manager"):
        flash("Invalid role. Must be admin or manager.", "danger")
        return redirect(url_for("manage_users"))

    users = load_users()
    if username in users:
        flash(f"Username '{username}' already exists.", "warning")
        return redirect(url_for("manage_users"))

    users[username] = {"password": password, "role": role}
    save_users(users)
    flash(f"User '{username}' created successfully as {role}.", "success")
    return redirect(url_for("manage_users"))

@app.route("/users/delete", methods=["POST"])
@require_admin
def delete_user():
    username = request.form.get("username", "").strip()
    if username == current_user():
        flash("You cannot delete your own account!", "danger")
        return redirect(url_for("manage_users"))

    users = load_users()
    if username not in users:
        flash(f"User '{username}' not found.", "warning")
        return redirect(url_for("manage_users"))

    del users[username]
    save_users(users)
    flash(f"User '{username}' deleted.", "success")
    return redirect(url_for("manage_users"))

@app.route("/users/change-password", methods=["POST"])
@require_admin
def change_user_password():
    username   = request.form.get("username", "").strip()
    new_password = request.form.get("new_password", "").strip()

    if not username or not new_password:
        flash("Username and new password are required.", "danger")
        return redirect(url_for("manage_users"))

    users = load_users()
    if username not in users:
        flash(f"User '{username}' not found.", "warning")
        return redirect(url_for("manage_users"))

    users[username]["password"] = new_password
    save_users(users)
    flash(f"Password updated for '{username}'.", "success")
    return redirect(url_for("manage_users"))


# ─────────────────────────────────────────────────────────────────────────────
# API endpoint for employee lookup (used in JS)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/api/employee/<emp_id>")
@require_login
def api_employee(emp_id):
    employees = load_employees()
    emp = find_employee(emp_id, employees)
    if emp:
        return jsonify({"found": True, "name": emp["name"], "role": emp["role"]})
    return jsonify({"found": False})


# ─────────────────────────────────────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # host="0.0.0.0" makes it accessible on local network (Android, manager)
    app.run(host="0.0.0.0", port=5000, debug=False)
