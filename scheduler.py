"""
Shift Scheduler - Core scheduling logic
Handles all scheduling rules, constraints and generation
"""

import json
import calendar
import copy
from datetime import date, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
EMPLOYEES_FILE = DATA_DIR / "employees.json"
LEAVE_HISTORY_FILE = DATA_DIR / "leave_history.json"
SCHEDULES_FILE = DATA_DIR / "schedules.json"

ROLE_ORDER = ["Engineers", "Operators", "Technicians", "Apprentices"]


# ─────────────────────────────────────────────────────────────────────────────
# Data helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_employees() -> dict:
    with open(EMPLOYEES_FILE) as f:
        return json.load(f)


def save_employees(data: dict):
    with open(EMPLOYEES_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_leave_history() -> dict:
    if not LEAVE_HISTORY_FILE.exists():
        return {"schedules": []}
    with open(LEAVE_HISTORY_FILE) as f:
        return json.load(f)


def save_leave_history(data: dict):
    with open(LEAVE_HISTORY_FILE, "w") as f:
        json.dump(data, f, indent=2)


def load_schedules() -> list:
    if not SCHEDULES_FILE.exists():
        return []
    with open(SCHEDULES_FILE) as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def save_schedules(data: list):
    with open(SCHEDULES_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_all_employees_flat(employees: dict) -> list:
    """Return flat list of all employees with role attached."""
    result = []
    for role, members in employees.items():
        for m in members:
            result.append({**m, "role": role})
    return result


def find_employee(emp_id: str, employees: dict) -> dict | None:
    for role, members in employees.items():
        for m in members:
            if str(m["id"]) == str(emp_id):
                return {**m, "role": role}
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Leave history helpers
# ─────────────────────────────────────────────────────────────────────────────

def count_leaves_last_n_months(emp_id: str, n: int = 2) -> int:
    """Count total leaves taken by an employee in the last n months."""
    history = load_leave_history()
    today = date.today()
    # Calculate the start of the window (beginning of month, n months ago)
    month = today.month - n
    year = today.year
    while month <= 0:
        month += 12
        year -= 1
    cutoff = date(year, month, 1)

    total = 0
    for schedule_record in history["schedules"]:
        sch_date = date.fromisoformat(schedule_record["month_start"])
        if sch_date >= cutoff:
            emp_schedule = schedule_record["employee_schedules"].get(str(emp_id), {})
            total += sum(1 for v in emp_schedule.values() if v == "L")
    return total


def append_schedule_to_history(year: int, month: int, employee_schedules: dict):
    """Persist a finalised schedule into leave_history."""
    history = load_leave_history()
    month_start = date(year, month, 1).isoformat()
    # Remove existing record for this month if any
    history["schedules"] = [s for s in history["schedules"]
                             if s["month_start"] != month_start]
    history["schedules"].append({
        "month_start": month_start,
        "employee_schedules": employee_schedules,
    })
    save_leave_history(history)


# ─────────────────────────────────────────────────────────────────────────────
# Weekly-off pattern helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_staggered_offs(year: int, month: int, offset: int) -> set[int]:
    """
    Return a set of W/off days for a specific offset (0-6).
    offset=0 represents Sun + alt Sat. 
    Other offsets shift this pattern across the week.
    """
    offs = set()
    target1 = (6 + offset) % 7 # 6 = Sunday
    target2 = (5 + offset) % 7 # 5 = Saturday
    
    target2_count = 0
    days_in_month = calendar.monthrange(year, month)[1]
    for day in range(1, days_in_month + 1):
        wd = calendar.weekday(year, month, day)
        if wd == target1:
            offs.add(day)
        elif wd == target2:
            target2_count += 1
            if target2_count % 2 == 1:
                offs.add(day)
    return offs


# ─────────────────────────────────────────────────────────────────────────────
# Shift rotation helpers
# ─────────────────────────────────────────────────────────────────────────────

SHIFTS = ["1", "2", "3"]   # 1=Morning, 2=Afternoon, 3=Night
SHIFT_NAMES = {"1": "Shift 1 (Morning)", "2": "Shift 2 (Afternoon)", "3": "Shift 3 (Night)"}
SHIFT_ROTATION = {"1": "2", "2": "3", "3": "1"}   # standard rotation


def get_previous_month_last_shift(emp_id: str) -> str | None:
    """Find what shift an employee was on at the end of the last schedule."""
    schedules = load_schedules()
    if not schedules:
        return None
    last = schedules[-1]
    emp_sch = last.get("employee_schedules", {}).get(str(emp_id), {})
    if not emp_sch:
        return None
    # Find last working day
    for day in sorted(emp_sch.keys(), key=int, reverse=True):
        if emp_sch[day] in SHIFTS:
            return emp_sch[day]
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Core schedule generator
# ─────────────────────────────────────────────────────────────────────────────

def generate_schedule(
    year: int,
    month: int,
    requested_leaves: dict,   # {emp_id: [day_int, ...]}
    new_employees: list,      # [{id, name, role}]
    leaving_emp_ids: list,    # [emp_id_str, ...]
) -> dict:
    """
    Generate a shift schedule for the given month.

    Returns a dict:
    {
      "year": int,
      "month": int,
      "employee_schedules": {emp_id: {day: "1"/"2"/"3"/"W"/"L"}, ...},
      "cancelled_leaves": [{emp_id, day, reason}],
      "warnings": [str],
      "meta": {emp_id: {name, role}, ...}
    }
    """
    employees = load_employees()

    # Apply departures
    for eid in leaving_emp_ids:
        for role, members in employees.items():
            employees[role] = [m for m in members if str(m["id"]) != str(eid)]

    # Apply new joiners
    for ne in new_employees:
        role = ne["role"]
        if role not in employees:
            employees[role] = []
        employees[role].append({"id": str(ne["id"]), "name": ne["name"]})

    all_emps = get_all_employees_flat(employees)
    days_in_month = calendar.monthrange(year, month)[1]
    days = list(range(1, days_in_month + 1))

    # ── Step 1: Assign initial shifts based on rotation ──────────────────────
    employee_schedules = {}   # {emp_id: {day: assignment}}
    meta = {}

    def get_rotation_sequence(role: str, num_people: int) -> list:
        if role in ("Engineers", "Apprentices") or num_people < 2:
            return ["1"] * max(1, num_people)
        # Exactly one '2' and one '3', the rest '1'
        return ["1"] * (num_people - 2) + ["2", "3"]

    def assign_starting_indices(emps, seq):
        assigned = {}
        available_indices = list(range(len(seq)))
        for emp in emps:
            eid = str(emp["id"])
            last_shift = get_previous_month_last_shift(eid)
            if last_shift:
                expected_next = SHIFT_ROTATION[last_shift]
                for idx in available_indices:
                    if seq[idx] == expected_next:
                        assigned[eid] = idx
                        available_indices.remove(idx)
                        break
        for emp in emps:
            eid = str(emp["id"])
            if eid not in assigned:
                idx = available_indices.pop(0)
                assigned[eid] = idx
        return assigned

    # Group employees by role and assign starting indices
    role_emps = {}
    for emp in all_emps:
        role_emps.setdefault(emp["role"], []).append(emp)

    emp_sequence_info = {} # eid -> (sequence, current_idx, woff_offset)
    for role, emps in role_emps.items():
        seq = get_rotation_sequence(role, len(emps))
        assigned_indices = assign_starting_indices(emps, seq)
        for i, emp in enumerate(emps):
            eid = str(emp["id"])
            meta[eid] = {"name": emp["name"], "role": emp["role"]}
            # We skip offsets 2 and 3 because they land on Tuesday (weekday 1).
            # This ensures almost NO ONE has a scheduled W/Off on Tuesday,
            # maximizing attendance for the Shift 1 weekly meeting!
            allowed_offsets = [0, 1, 4, 5, 6]
            woff_offset = allowed_offsets[i % len(allowed_offsets)]
            emp_sequence_info[eid] = (seq, assigned_indices[eid], woff_offset)

    for emp in all_emps:
        eid = str(emp["id"])
        seq, current_idx, woff_offset = emp_sequence_info[eid]
        personal_offs = get_staggered_offs(year, month, woff_offset)

        emp_sch = {}
        consecutive_work = 0

        for day in days:
            if day in personal_offs:
                emp_sch[day] = "W"
                consecutive_work = 0
            else:
                emp_sch[day] = seq[current_idx]
                consecutive_work += 1
                # Rotate sequence every 7 working days
                if consecutive_work >= 7:
                    consecutive_work = 0
                    current_idx = (current_idx + 1) % len(seq)

        employee_schedules[eid] = emp_sch

    # ── Step 2: Engineers prefer Shift 1 ─────────────────────────────────────
    for emp in all_emps:
        if emp["role"] == "Engineers":
            eid = str(emp["id"])
            for day in days:
                if employee_schedules[eid].get(day) not in ("W", "L"):
                    employee_schedules[eid][day] = "1"

    # ── Step 3: Apply requested leaves ───────────────────────────────────────
    cancelled_leaves = []
    applied_leaves = {}   # {emp_id: [days]}

    for eid, leave_days in requested_leaves.items():
        eid = str(eid)
        applied_leaves[eid] = []
        for day in leave_days:
            if day in days and employee_schedules.get(eid, {}).get(day) != "W":
                employee_schedules[eid][day] = "L"
                applied_leaves[eid].append(day)

    # ── Step 4: Ensure 1 WO between shift changes ────────────────────────────
    _enforce_shift_change_wos(employee_schedules, days, all_emps)

    # ── Step 5: Enforce constraints & fix violations ──────────────────────────
    warnings = []
    for day in days:
        _fix_day_constraints(
            day, employee_schedules, all_emps, applied_leaves,
            cancelled_leaves, warnings, days
        )

    return {
        "year": year,
        "month": month,
        "employee_schedules": {
            eid: {str(d): v for d, v in sch.items()}
            for eid, sch in employee_schedules.items()
        },
        "cancelled_leaves": cancelled_leaves,
        "warnings": warnings,
        "meta": meta,
        "days_in_month": days_in_month,
    }


def _fix_day_constraints(
    day: int,
    schedules: dict,
    all_emps: list,
    applied_leaves: dict,
    cancelled_leaves: list,
    warnings: list,
    days_list: list,
):
    """Ensure each shift on `day` has ≥1 Operator, ≥1 non-Apprentice support, and Shift 1 has ≥1 Engineer."""

    for shift in SHIFTS:
        # Who is assigned this shift today?
        on_shift = [
            emp for emp in all_emps
            if schedules.get(str(emp["id"]), {}).get(day) == shift
        ]

        operators = [e for e in on_shift if e["role"] == "Operators"]
        support = [e for e in on_shift if e["role"] not in ("Operators", "Apprentices")]
        engineers = [e for e in on_shift if e["role"] == "Engineers"]

        need_operator = len(operators) == 0
        need_support = len(support) == 0
        need_engineer = len(engineers) == 0 if shift == "1" else False

        if not need_operator and not need_support and not need_engineer:
            continue  # constraints satisfied

        # Try to find someone to cover
        if need_operator:
            _try_reassign(
                day, shift, "Operators", schedules, all_emps,
                applied_leaves, cancelled_leaves, warnings, days_list
            )
        if need_support:
            _try_reassign(
                day, shift, ["Technicians", "Engineers"], schedules, all_emps,
                applied_leaves, cancelled_leaves, warnings, days_list
            )
        if need_engineer:
            _try_reassign(
                day, shift, "Engineers", schedules, all_emps,
                applied_leaves, cancelled_leaves, warnings, days_list
            )


def compensate_woff(emp_id, original_day, schedules, days_list):
    sch = schedules[str(emp_id)]
    for offset in range(1, 15):
        d = original_day + offset
        if d in days_list and sch.get(d) in SHIFTS:
            sch[d] = "W"
            return

def _try_reassign(
    day: int,
    shift: str,
    role_filter,
    schedules: dict,
    all_emps: list,
    applied_leaves: dict,
    cancelled_leaves: list,
    warnings: list,
    days_list: list,
):
    """Attempt to reassign someone to cover a shortage. Cancel leaves if needed."""
    if isinstance(role_filter, str):
        role_filter = [role_filter]

    # Look for W or L
    candidates = [
        emp for emp in all_emps
        if emp["role"] in role_filter
        and schedules.get(str(emp["id"]), {}).get(day) in ("L", "W")
    ]

    if not candidates:
        warnings.append(
            f"Day {day}: Could not satisfy {role_filter} constraint for Shift {shift}. "
            f"No available staff to reassign."
        )
        return

    def is_normally_on_shift(emp):
        sch = schedules[str(emp["id"])]
        for d in (day-1, day+1, day-2, day+2):
            if d in sch and sch[d] == shift:
                return 1
        return 0

    w_candidates = [c for c in candidates if schedules[str(c["id"])][day] == "W"]
    w_candidates.sort(key=lambda c: is_normally_on_shift(c))
    
    l_candidates = [c for c in candidates if schedules[str(c["id"])][day] == "L"]
    l_candidates.sort(key=lambda c: (count_leaves_last_n_months(str(c["id"]), 2), -is_normally_on_shift(c)), reverse=True)

    if w_candidates:
        if shift != "1" and "Technicians" in role_filter:
            w_candidates.sort(key=lambda c: 0 if c["role"] == "Technicians" else 1)
        chosen = w_candidates[0]
        schedules[str(chosen["id"])][day] = shift
        compensate_woff(chosen["id"], day, schedules, days_list)
        return

    if l_candidates:
        if shift != "1" and "Technicians" in role_filter:
            l_candidates.sort(key=lambda c: 0 if c["role"] == "Technicians" else 1)
            
        # Sort by leave count desc; cancel the one with most leaves
        l_candidates.sort(
            key=lambda e: count_leaves_last_n_months(str(e["id"]), 2), reverse=True
        )
        chosen = l_candidates[0]
        leave_count = count_leaves_last_n_months(str(chosen["id"]), 2)
        reason = (
            f"{chosen['name']} had {leave_count} leave(s) in the last 2 months, "
            f"which is the highest among available candidates for this role."
            if leave_count > 0
            else f"No other coverage available for {role_filter} on Day {day} – "
                 f"this is the only person available in that role."
        )
        schedules[str(chosen["id"])][day] = shift
        compensate_woff(chosen["id"], day, schedules, days_list)
        cancelled_leaves.append({
            "emp_id": str(chosen["id"]),
            "emp_name": chosen["name"],
            "day": day,
            "shift": shift,
            "reason": reason,
        })


def _enforce_shift_change_wos(schedules: dict, days: list, all_emps: list):
    """Ensure at least 1 weekly off exist between consecutive shift changes."""
    for emp in all_emps:
        eid = str(emp["id"])
        sch = schedules.get(eid, {})
        # Find shift transitions and ensure 1 WO before the new shift starts
        work_days = [d for d in days if sch.get(d) in SHIFTS]
        if len(work_days) < 2:
            continue

        i = 0
        while i < len(work_days) - 1:
            curr_day = work_days[i]
            next_day = work_days[i + 1]
            curr_shift = sch[curr_day]
            next_shift = sch[next_day]

            if curr_shift != next_shift:
                # Count WOs between these two working days
                between = [d for d in days if curr_day < d < next_day and sch.get(d) == "W"]
                if len(between) < 1:
                    # We need to insert WOs. Convert the next_day to W
                    j = next_day
                    wos_added = len(between)
                    while wos_added < 1 and j in days:
                        if sch.get(j) in SHIFTS:
                            sch[j] = "W"
                            wos_added += 1
                        j += 1
                    # Recalculate work_days after modification
                    work_days = [d for d in days if sch.get(d) in SHIFTS]
                    i = 0
                    continue
            i += 1


# ─────────────────────────────────────────────────────────────────────────────
# Schedule persistence
# ─────────────────────────────────────────────────────────────────────────────

def save_schedule(schedule_result: dict):
    """Append or update schedule in schedules.json and update leave history."""
    schedules = load_schedules()
    key = f"{schedule_result['year']}-{schedule_result['month']:02d}"
    schedules = [s for s in schedules if s.get("key") != key]
    schedule_result["key"] = key
    schedules.append(schedule_result)
    save_schedules(schedules)
    # Update leave history
    append_schedule_to_history(
        schedule_result["year"],
        schedule_result["month"],
        schedule_result["employee_schedules"],
    )


def get_schedule(year: int, month: int) -> dict | None:
    key = f"{year}-{month:02d}"
    for s in load_schedules():
        if s.get("key") == key:
            return s
    return None


def day_name(year: int, month: int, day: int) -> str:
    """Return short weekday name for a given date."""
    return date(year, month, day).strftime("%a")


def month_name(month: int) -> str:
    return calendar.month_name[month]
