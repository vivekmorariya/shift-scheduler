"""
Shift Scheduler - Core scheduling logic
Handles all scheduling rules, constraints and generation

Rules enforced:
  1. Shift rotation: 1→2→3→1→... (after 3rd shift, only 2nd or 1st)
  2. At least 1 W/off mandatory between consecutive shift changes
  3. Weekly offs are consecutive 2-day pairs (e.g. Sat+Sun, Mon+Tue)
     and the first day of the pair alternates (1st week off, 2nd week work, etc.)
  4. Shift changes affect whole weeks (Mon–Sun), no mid-week shift changes
  5. Manual overrides are stored and respected in future regeneration
"""

import json
import calendar
import copy
from datetime import date, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
EMPLOYEES_FILE     = DATA_DIR / "employees.json"
LEAVE_HISTORY_FILE = DATA_DIR / "leave_history.json"
SCHEDULES_FILE     = DATA_DIR / "schedules.json"
OVERRIDES_FILE     = DATA_DIR / "manual_overrides.json"

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


def load_manual_overrides() -> dict:
    """Returns {month_key: {emp_id: {day_str: assignment}}}"""
    if not OVERRIDES_FILE.exists():
        return {}
    with open(OVERRIDES_FILE) as f:
        return json.load(f)


def save_manual_overrides(data: dict):
    if not DATA_DIR.exists():
        DATA_DIR.mkdir(parents=True)
    with open(OVERRIDES_FILE, "w") as f:
        json.dump(data, f, indent=2)


def record_manual_override(year: int, month: int, emp_id: str, day: int, assignment: str):
    """Store a single manual cell edit so future generation can respect it."""
    overrides = load_manual_overrides()
    key = f"{year}-{month:02d}"
    if key not in overrides:
        overrides[key] = {}
    eid = str(emp_id)
    if eid not in overrides[key]:
        overrides[key][eid] = {}
    overrides[key][eid][str(day)] = assignment
    save_manual_overrides(overrides)


def get_manual_overrides_for_month(year: int, month: int) -> dict:
    """Returns {emp_id: {day_str: assignment}} for the given month."""
    overrides = load_manual_overrides()
    key = f"{year}-{month:02d}"
    return overrides.get(key, {})


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
    history["schedules"] = [s for s in history["schedules"]
                             if s["month_start"] != month_start]
    history["schedules"].append({
        "month_start": month_start,
        "employee_schedules": employee_schedules,
    })
    save_leave_history(history)


# ─────────────────────────────────────────────────────────────────────────────
# Shift & rotation constants
# ─────────────────────────────────────────────────────────────────────────────

SHIFTS = ["1", "2", "3"]
SHIFT_NAMES = {"1": "Shift 1 (Morning)", "2": "Shift 2 (Afternoon)", "3": "Shift 3 (Night)"}

# Valid next shifts after a given shift (Rule 1)
VALID_NEXT_SHIFTS = {
    "1": ["2", "3"],   # after 1st, can go to 2nd or 3rd
    "2": ["3"],        # after 2nd, must go to 3rd
    "3": ["1", "2"],   # after 3rd, can go to 1st or 2nd
}

# The standard forward-rotation order
SHIFT_SEQUENCE = ["1", "2", "3"]


def next_shift_in_rotation(current_shift: str) -> str:
    """Return the next shift in standard 1→2→3→1 rotation."""
    idx = SHIFT_SEQUENCE.index(current_shift)
    return SHIFT_SEQUENCE[(idx + 1) % 3]


# ─────────────────────────────────────────────────────────────────────────────
# Weekly-off helpers  (Rule 3: consecutive pair, alternating first day)
# ─────────────────────────────────────────────────────────────────────────────

def get_weeks_in_month(year: int, month: int) -> list[list[int]]:
    """
    Return a list of weeks. Each week is a list of day-numbers (1-based)
    that fall within the month for that Mon–Sun calendar week.
    """
    days_in_month = calendar.monthrange(year, month)[1]
    weeks = []
    week = []
    for day in range(1, days_in_month + 1):
        wd = date(year, month, day).weekday()   # 0=Mon … 6=Sun
        week.append(day)
        if wd == 6:   # end of week (Sunday)
            weeks.append(week)
            week = []
    if week:
        weeks.append(week)
    return weeks


def build_woff_pattern(year: int, month: int, woff_pair: tuple[int, int]) -> set[int]:
    """
    Build a set of W/off day numbers for one employee.

    woff_pair: (day1_wd, day2_wd) where 0=Mon … 6=Sun.
    The pair represents the TWO consecutive days off per week.
    The first day of the pair alternates: off in week 1, work in week 2,
    off in week 3, … (alternating-Saturday rule generalised).
    """
    days_in_month = calendar.monthrange(year, month)[1]
    offs = set()
    pair_week_count = 0   # count how many times we've seen the first day of the pair

    # We track week number to decide alternation
    # week_no: 1-indexed, increments each Monday
    current_week = 1
    prev_wd = None

    for day in range(1, days_in_month + 1):
        wd = date(year, month, day).weekday()
        # Detect week rollover
        if prev_wd is not None and wd < prev_wd:
            current_week += 1
        prev_wd = wd

        d1, d2 = woff_pair
        if wd == d1:
            pair_week_count += 1
            # Odd occurrences: this pair is OFF
            if pair_week_count % 2 == 1:
                offs.add(day)
                # Also mark the next calendar day if it falls in month and is d2
                if day + 1 <= days_in_month:
                    next_wd = date(year, month, day + 1).weekday()
                    if next_wd == d2:
                        offs.add(day + 1)
        elif wd == d2:
            # Only add if the previous day (d1) was already added
            if (day - 1) in offs:
                offs.add(day)

    return offs


# Stagger W/off pairs across employees so not everyone is off on the same days.
# Each pair is (first_day_wd, second_day_wd), consecutive.
WOFF_PAIR_POOL = [
    (5, 6),   # Sat + Sun
    (6, 0),   # Sun + Mon
    (0, 1),   # Mon + Tue
    (3, 4),   # Thu + Fri
    (4, 5),   # Fri + Sat
]

# Skip pairs that include Tuesday (weekday 1) to maximise Tuesday meeting attendance
TUESDAY_SAFE_PAIRS = [(d1, d2) for d1, d2 in WOFF_PAIR_POOL if 1 not in (d1, d2)]


# ─────────────────────────────────────────────────────────────────────────────
# Previous month continuity
# ─────────────────────────────────────────────────────────────────────────────

def get_previous_month_last_shift(emp_id: str) -> str | None:
    """Find what shift an employee was on at the end of the last schedule."""
    schedules = load_schedules()
    if not schedules:
        return None
    last = schedules[-1]
    emp_sch = last.get("employee_schedules", {}).get(str(emp_id), {})
    if not emp_sch:
        return None
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
      "year": int, "month": int,
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
    weeks = get_weeks_in_month(year, month)

    # Load any stored manual overrides for this month
    manual_overrides = get_manual_overrides_for_month(year, month)

    # ── Step 1: Assign weekly-block shifts (Rule 4: whole week same shift) ────
    #
    # Each employee stays on ONE shift for an entire ISO week.
    # Shift changes only happen at week boundaries.
    # At a shift change boundary we insert a W/off day (Rule 2).
    # Engineers always stay on Shift 1.
    # The rotation follows 1→2→3→1 (Rule 1).

    employee_schedules: dict[str, dict[int, str]] = {}
    meta: dict[str, dict] = {}

    # Pool of W/off pairs to stagger across employees
    pair_pool_size = len(TUESDAY_SAFE_PAIRS)

    for i_emp, emp in enumerate(all_emps):
        eid = str(emp["id"])
        meta[eid] = {"name": emp["name"], "role": emp["role"]}

        # Determine starting shift
        last_shift = get_previous_month_last_shift(eid)
        if last_shift:
            start_shift = next_shift_in_rotation(last_shift)
        else:
            start_shift = "1"   # default

        # Engineers always Shift 1
        if emp["role"] == "Engineers":
            start_shift = "1"

        # Assign W/off pair
        woff_pair = TUESDAY_SAFE_PAIRS[i_emp % pair_pool_size]
        woff_days = build_woff_pattern(year, month, woff_pair)

        emp_sch: dict[int, str] = {}
        current_shift = start_shift

        for w_idx, week in enumerate(weeks):
            # Determine shift for this week
            if emp["role"] == "Engineers":
                week_shift = "1"
            else:
                week_shift = current_shift

            # Check if this week has a shift change (not first week)
            if w_idx > 0:
                # What was last week's shift?
                last_week = weeks[w_idx - 1]
                last_working_days = [d for d in last_week if emp_sch.get(d) in SHIFTS]
                prev_shift = emp_sch.get(last_working_days[-1]) if last_working_days else None

                if prev_shift and prev_shift != week_shift:
                    # Rule 2: insert mandatory W/off between shift changes.
                    # Mark the FIRST day of this new week as W/off (rest day).
                    # The actual shift starts from the second day.
                    first_day = week[0]
                    emp_sch[first_day] = "W"
                    # Remaining days of the week get the new shift or W/off
                    for day in week[1:]:
                        if day in woff_days:
                            emp_sch[day] = "W"
                        else:
                            emp_sch[day] = week_shift
                    # Advance shift for next rotation
                    if emp["role"] != "Engineers":
                        current_shift = next_shift_in_rotation(current_shift)
                    continue

            # Normal week (no shift change this week)
            for day in week:
                if day in woff_days:
                    emp_sch[day] = "W"
                else:
                    emp_sch[day] = week_shift

            # After 7 working days worth of weeks, rotate shift
            if emp["role"] != "Engineers":
                current_shift = next_shift_in_rotation(current_shift)

        employee_schedules[eid] = emp_sch

    # ── Step 2: Apply requested leaves ───────────────────────────────────────
    cancelled_leaves = []
    applied_leaves: dict[str, list] = {}

    for eid, leave_days in requested_leaves.items():
        eid = str(eid)
        applied_leaves[eid] = []
        for day in leave_days:
            if day in days and employee_schedules.get(eid, {}).get(day) not in ("W",):
                employee_schedules[eid][day] = "L"
                applied_leaves[eid].append(day)

    # ── Step 3: Apply manual overrides (Rule 5) ───────────────────────────────
    for eid, day_map in manual_overrides.items():
        if eid in employee_schedules:
            for day_str, assignment in day_map.items():
                try:
                    day = int(day_str)
                    if day in days:
                        employee_schedules[eid][day] = assignment
                except (ValueError, KeyError):
                    pass

    # ── Step 4: Enforce constraints & fix violations ──────────────────────────
    warnings: list[str] = []
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


# ─────────────────────────────────────────────────────────────────────────────
# Constraint enforcement
# ─────────────────────────────────────────────────────────────────────────────

def _fix_day_constraints(
    day: int,
    schedules: dict,
    all_emps: list,
    applied_leaves: dict,
    cancelled_leaves: list,
    warnings: list,
    days_list: list,
):
    """Ensure each shift on `day` has ≥1 Operator, ≥1 non-Apprentice support,
    and Shift 1 has ≥1 Engineer."""

    for shift in SHIFTS:
        on_shift = [
            emp for emp in all_emps
            if schedules.get(str(emp["id"]), {}).get(day) == shift
        ]

        operators  = [e for e in on_shift if e["role"] == "Operators"]
        support    = [e for e in on_shift if e["role"] not in ("Operators", "Apprentices")]
        engineers  = [e for e in on_shift if e["role"] == "Engineers"]

        need_operator = len(operators) == 0
        need_support  = len(support) == 0
        need_engineer = (len(engineers) == 0) if shift == "1" else False

        if not need_operator and not need_support and not need_engineer:
            continue

        if need_operator:
            _try_reassign(day, shift, "Operators", schedules, all_emps,
                          applied_leaves, cancelled_leaves, warnings, days_list)
        if need_support:
            _try_reassign(day, shift, ["Technicians", "Engineers"], schedules, all_emps,
                          applied_leaves, cancelled_leaves, warnings, days_list)
        if need_engineer:
            _try_reassign(day, shift, "Engineers", schedules, all_emps,
                          applied_leaves, cancelled_leaves, warnings, days_list)


def compensate_woff(emp_id, original_day, schedules, days_list):
    """Give the employee a compensatory W/off on a nearby future working day."""
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
        for d in (day - 1, day + 1, day - 2, day + 2):
            if d in sch and sch[d] == shift:
                return 1
        return 0

    w_candidates = [c for c in candidates if schedules[str(c["id"])][day] == "W"]
    w_candidates.sort(key=lambda c: is_normally_on_shift(c))

    l_candidates = [c for c in candidates if schedules[str(c["id"])][day] == "L"]
    l_candidates.sort(
        key=lambda c: (count_leaves_last_n_months(str(c["id"]), 2), -is_normally_on_shift(c)),
        reverse=True,
    )

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
            "emp_id":    str(chosen["id"]),
            "emp_name":  chosen["name"],
            "day":       day,
            "shift":     shift,
            "reason":    reason,
        })


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
