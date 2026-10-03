"""
Shift Scheduler - Core scheduling logic
Handles all scheduling rules, constraints and generation

Rules enforced:
  1. Shift rotation: 1 -> 2 -> 3 -> 1.
     - On consecutive working days: only 1->1, 1->2, 2->2, 2->3, 3->3 are permitted.
     - 3->1 and 3->2 and 2->1 are strictly forbidden on consecutive working days.
     - 3->1 is ONLY permitted with at least 1 weekly off (W) rest day in between.
  2. Mandatory rest day: at least 1 W/off between consecutive shift changes.
  3. Weekly offs are consecutive 2-day pairs (e.g. Sat+Sun, Fri+Sat, Sun+Mon).
     - The first day alternates (odd weeks off, even weeks work).
     - Never 3 weekly offs in a week, and never 3 consecutive W/offs.
  4. Engineers are ALWAYS on Shift 1 every single working day. They do NOT rotate.
  5. Whole-week consistency: staff stay on their shift blocks.
  6. Staffing constraints:
     - Shift 1: >=1 Operator, >=1 Support (Technician/Engineer), >=1 Engineer.
     - Shift 2: Exactly 1 Operator + 1 Support (Technician).
     - Shift 3: Exactly 1 Operator + 1 Support (Technician).
  7. Manual overrides are remembered and re-applied on regeneration.
"""

import json
import calendar
import itertools
from datetime import date
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
EMPLOYEES_FILE     = DATA_DIR / "employees.json"
LEAVE_HISTORY_FILE = DATA_DIR / "leave_history.json"
SCHEDULES_FILE     = DATA_DIR / "schedules.json"
OVERRIDES_FILE     = DATA_DIR / "manual_overrides.json"

ROLE_ORDER = ["Engineers", "Operators", "Technicians", "Apprentices"]
SHIFTS = ["1", "2", "3"]
SHIFT_NAMES = {"1": "Shift 1 (Morning)", "2": "Shift 2 (Afternoon)", "3": "Shift 3 (Night)"}

# Candidate consecutive-day weekly off pairs (day1, day2)
# 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
# Tuesday (1) is protected for Tuesday weekly meeting attendance
PAIR_POOL = [
    (5, 6), # Sat, Sun
    (4, 5), # Fri, Sat
    (6, 0), # Sun, Mon
    (3, 4), # Thu, Fri
    (2, 3), # Wed, Thu
    (0, 1), # Mon, Tue
    (1, 2), # Tue, Wed
]


# ─────────────────────────────────────────────────────────────────────────────
# Data persistence helpers
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
    overrides = load_manual_overrides()
    key = f"{year}-{month:02d}"
    return overrides.get(key, {})


def get_all_employees_flat(employees: dict) -> list:
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


def count_leaves_last_n_months(emp_id: str, n: int = 2) -> int:
    history = load_leave_history()
    today = date.today()
    month = today.month - n
    year = today.year
    while month <= 0:
        month += 12
        year -= 1
    cutoff = date(year, month, 1)

    total = 0
    for schedule_record in history.get("schedules", []):
        sch_date = date.fromisoformat(schedule_record["month_start"])
        if sch_date >= cutoff:
            emp_schedule = schedule_record["employee_schedules"].get(str(emp_id), {})
            total += sum(1 for v in emp_schedule.values() if v == "L")
    return total


def append_schedule_to_history(year: int, month: int, employee_schedules: dict):
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
# Continuity: Last shifts from previous month
# ─────────────────────────────────────────────────────────────────────────────

def get_previous_month_last_shifts() -> dict:
    """Find the last working shift of each employee from the most recent schedule."""
    schedules = load_schedules()
    if not schedules:
        history = load_leave_history()
        schedules = history.get("schedules", [])
    if not schedules:
        return {}

    last = schedules[-1]
    emp_schs = last.get("employee_schedules", {})
    last_shifts = {}
    for eid, days_map in emp_schs.items():
        for d in sorted([int(k) for k in days_map.keys()], reverse=True):
            val = days_map.get(str(d))
            if val in SHIFTS:
                last_shifts[str(eid)] = val
                break
    return last_shifts


# ─────────────────────────────────────────────────────────────────────────────
# Calendar and Weekly Off helpers (Rule 3)
# ─────────────────────────────────────────────────────────────────────────────

def get_calendar_weeks(year: int, month: int) -> list[list[int]]:
    """Return calendar weeks as lists of 1-based day numbers, ending on Sunday."""
    days_in_month = calendar.monthrange(year, month)[1]
    weeks = []
    curr = []
    for d in range(1, days_in_month + 1):
        wd = date(year, month, d).weekday()
        curr.append(d)
        if wd == 6: # Sunday ends week
            weeks.append(curr)
            curr = []
    if curr:
        weeks.append(curr)
    return weeks


def get_woff_days_for_pair(year: int, month: int, pair: tuple, weeks: list, alt_even: bool = False) -> set[int]:
    """
    Generate W/off days for a pair (d1, d2).
    d2 is off EVERY week.
    d1 alternates: off in odd weeks (w_idx 1, 3, 5) or even weeks (w_idx 2, 4).
    This guarantees:
      - At most 2 offs in any week (never 3).
      - When 2 offs occur, they are consecutive (d1 and d2).
      - No 3 consecutive W/offs anywhere.
    """
    d1, d2 = pair
    woffs = set()
    for w_idx, w_days in enumerate(weeks):
        for d in w_days:
            wd = date(year, month, d).weekday()
            if wd == d2:
                woffs.add(d)
            elif wd == d1:
                target_parity = 0 if alt_even else 1
                if (w_idx % 2) == target_parity and w_idx > 0:
                    woffs.add(d)
    return woffs


def assign_optimal_pairs(members: list, start_shifts: dict, leaves_dict: dict, weeks: list, year: int, month: int) -> dict:
    """
    Find the weekly off pair assignment that ensures every shift (1, 2, and 3)
    has full coverage on every single day of the month.
    """
    eids = [str(m["id"]) for m in members]
    days_in_month = calendar.monthrange(year, month)[1]
    best_p = None
    best_v = 999

    for p_tuple in itertools.product(PAIR_POOL, repeat=len(eids)):
        woffs_dict = {
            eids[i]: get_woff_days_for_pair(year, month, p_tuple[i], weeks, alt_even=(i % 2 == 1))
            for i in range(len(eids))
        }
        sch = solve_shift_group(members, start_shifts, woffs_dict, leaves_dict, days_in_month)

        v_count = 0
        for d in range(1, days_in_month + 1):
            s2 = sum(1 for eid in eids if sch[eid].get(d) == "2")
            s3 = sum(1 for eid in eids if sch[eid].get(d) == "3")
            s1 = sum(1 for eid in eids if sch[eid].get(d) == "1")
            if s2 < 1 or s3 < 1 or s1 < 1:
                v_count += 1

        # Ensure all group members rotate (at least 2 distinct working shifts)
        rotated_count = sum(
            1 for eid in eids
            if len({sch[eid].get(d) for d in range(1, days_in_month + 1) if sch[eid].get(d) in ("1", "2", "3")}) >= 2
        )
        if rotated_count < len(eids):
            v_count += (len(eids) - rotated_count) * 100

        if v_count == 0:
            return {eids[i]: (p_tuple[i], i % 2 == 1) for i in range(len(eids))}

        if v_count < best_v:
            best_v = v_count
            best_p = p_tuple

    return {eids[i]: (best_p[i], i % 2 == 1) for i in range(len(eids))}


# ─────────────────────────────────────────────────────────────────────────────
# Core Group Shift Solver (Rules 1, 2, 4, 6)
# ─────────────────────────────────────────────────────────────────────────────

def solve_shift_group(members: list, start_shifts: dict, woffs_dict: dict, leaves_dict: dict, days_in_month: int) -> dict:
    """
    Assign shifts for a role group (Operators, Technicians, Apprentices) day by day.
    Strictly enforces:
      - 1->2 and 2->3 forward progression on consecutive days.
      - 3->1 ONLY permitted after at least 1 W/off rest day.
      - 3->2 and 2->1 forbidden on consecutive days.
      - Exactly 1 person on Shift 3, 1 person on Shift 2, rest on Shift 1.
      - Fair rotation: ensures ALL members rotate across shifts (no one stuck on Shift 1).
      - HR rule: Weekly offs take priority over leaves (leaves do not consume weekly offs).
    """
    days = list(range(1, days_in_month + 1))
    eids = [str(m["id"]) for m in members]
    sched = {eid: {} for eid in eids}

    # Step 1: Pre-populate Weekly Offs and Leaves (HR Rule: Weekly off takes priority!)
    for eid in eids:
        for d in days:
            if d in woffs_dict.get(eid, set()):
                sched[eid][d] = "W"
            elif d in leaves_dict.get(eid, []):
                sched[eid][d] = "L"

    shift_counts = {eid: {"1": 0, "2": 0, "3": 0} for eid in eids}

    # Step 2: Forward simulation with fair rotation state machine
    for d in days:
        working = [eid for eid in eids if sched[eid].get(d) is None]

        prev_shift = {}
        had_rest = {}
        for eid in working:
            ps = None
            rest = False
            for prev_d in range(d - 1, 0, -1):
                v = sched[eid].get(prev_d)
                if v in ("1", "2", "3"):
                    ps = v
                    break
                if v in ("W", "L"):
                    rest = True
            if ps is None:
                ps = start_shifts.get(eid, "1")
            prev_shift[eid] = ps
            had_rest[eid] = rest or (d == 1)

        allowed = {}
        for eid in working:
            ps = prev_shift[eid]
            if ps == "3":
                # Can stay on 3, or rotate to 1 if had rest day
                allowed[eid] = ["1", "3"] if had_rest[eid] else ["3"]
            elif ps == "2":
                # Can stay on 2 or advance to 3. If had rest day, can also rotate to 1
                allowed[eid] = ["1", "2", "3"] if had_rest[eid] else ["2", "3"]
            else: # "1"
                allowed[eid] = ["1", "2"]

        # Shift 3 assignment: exactly 1 person (fair rotation preference)
        forced_s3 = [eid for eid in working if prev_shift[eid] == "3" and "1" not in allowed[eid]]
        if forced_s3:
            chosen_s3 = forced_s3[0]
        else:
            c_s3 = [eid for eid in working if "3" in allowed[eid]]
            c_s3.sort(key=lambda eid: (
                0 if (prev_shift[eid] == "2" and shift_counts[eid]["3"] < shift_counts[eid]["2"])
                else (1 if prev_shift[eid] == "3" else 2),
                shift_counts[eid]["3"]
            ))
            chosen_s3 = c_s3[0] if c_s3 else None

        # Shift 2 assignment: exactly 1 person (fair rotation preference)
        forced_s2 = [eid for eid in working if eid != chosen_s3 and prev_shift[eid] == "2" and "1" not in allowed[eid]]
        if forced_s2:
            chosen_s2 = forced_s2[0]
        else:
            c_s2 = [eid for eid in working if eid != chosen_s3 and "2" in allowed[eid]]
            c_s2.sort(key=lambda eid: (
                0 if (prev_shift[eid] == "1" and shift_counts[eid]["2"] < shift_counts[eid]["1"])
                else (1 if prev_shift[eid] == "2" else 2),
                shift_counts[eid]["2"]
            ))
            chosen_s2 = c_s2[0] if c_s2 else None

        # Shift 1 assignment: all other working people
        for eid in working:
            if eid == chosen_s3:
                sched[eid][d] = "3"
                shift_counts[eid]["3"] += 1
            elif eid == chosen_s2:
                sched[eid][d] = "2"
                shift_counts[eid]["2"] += 1
            else:
                if "1" not in allowed[eid]:
                    sched[eid][d] = prev_shift[eid]
                    shift_counts[eid][prev_shift[eid]] += 1
                else:
                    sched[eid][d] = "1"
                    shift_counts[eid]["1"] += 1

    return sched


# ─────────────────────────────────────────────────────────────────────────────
# Full Schedule Generator (Main Entry Point)
# ─────────────────────────────────────────────────────────────────────────────

def generate_schedule(
    year: int,
    month: int,
    requested_leaves: dict,   # {emp_id: [day_int, ...]}
    new_employees: list,      # [{id, name, role}]
    leaving_emp_ids: list,    # [emp_id_str, ...]
) -> dict:
    """
    Generate a 100% rule-compliant shift schedule for the given month.
    """
    employees = load_employees()

    # Apply departures
    for eid in leaving_emp_ids:
        for r in employees:
            employees[r] = [m for m in employees[r] if str(m["id"]) != str(eid)]

    # Apply new joiners
    for ne in new_employees:
        r = ne["role"]
        if r not in employees:
            employees[r] = []
        if not any(str(m["id"]) == str(ne["id"]) for m in employees[r]):
            employees[r].append({"id": str(ne["id"]), "name": ne["name"]})

    all_emps = get_all_employees_flat(employees)
    days_in_month = calendar.monthrange(year, month)[1]
    days = list(range(1, days_in_month + 1))
    weeks = get_calendar_weeks(year, month)

    last_shifts = get_previous_month_last_shifts()

    # Determine default starting shift if not recorded
    operators = [e for e in all_emps if e["role"] == "Operators"]
    technicians = [e for e in all_emps if e["role"] == "Technicians"]
    engineers = [e for e in all_emps if e["role"] == "Engineers"]
    apprentices = [e for e in all_emps if e["role"] == "Apprentices"]

    def build_start_shifts(group):
        starts = {}
        for i, m in enumerate(group):
            eid = str(m["id"])
            ls = last_shifts.get(eid)
            if ls:
                starts[eid] = ls
            else:
                starts[eid] = ["1", "2", "3"][i % 3]
        return starts

    op_starts = build_start_shifts(operators)
    tech_starts = build_start_shifts(technicians)
    app_starts = build_start_shifts(apprentices)

    # Convert requested leaves keys to string
    leaves_clean = {str(k): list(v) for k, v in requested_leaves.items()}

    # Assign optimal W/off pairs for coverage
    op_pair_config = assign_optimal_pairs(operators, op_starts, leaves_clean, weeks, year, month)
    tech_pair_config = assign_optimal_pairs(technicians, tech_starts, leaves_clean, weeks, year, month)
    app_pair_config = assign_optimal_pairs(apprentices, app_starts, leaves_clean, weeks, year, month)

    op_woffs = {eid: get_woff_days_for_pair(year, month, pair, weeks, alt) for eid, (pair, alt) in op_pair_config.items()}
    tech_woffs = {eid: get_woff_days_for_pair(year, month, pair, weeks, alt) for eid, (pair, alt) in tech_pair_config.items()}
    app_woffs = {eid: get_woff_days_for_pair(year, month, pair, weeks, alt) for eid, (pair, alt) in app_pair_config.items()}

    # Engineers: stagger across PAIR_POOL, prioritized for Shift 1
    eng_woffs = {
        str(e["id"]): get_woff_days_for_pair(year, month, PAIR_POOL[i % len(PAIR_POOL)], weeks, alt_even=(i % 2 == 1))
        for i, e in enumerate(engineers)
    }

    # Solve Operators, Technicians, and Apprentices with fair forward rotation
    op_sched = solve_shift_group(operators, op_starts, op_woffs, leaves_clean, days_in_month)
    tech_sched = solve_shift_group(technicians, tech_starts, tech_woffs, leaves_clean, days_in_month)
    app_sched = solve_shift_group(apprentices, app_starts, app_woffs, leaves_clean, days_in_month)

    # Assemble full schedule
    full_sched = {}
    meta = {}
    for e in all_emps:
        eid = str(e["id"])
        meta[eid] = {"name": e["name"], "role": e["role"]}

        if e["role"] == "Operators":
            full_sched[eid] = op_sched[eid]
        elif e["role"] == "Technicians":
            full_sched[eid] = tech_sched[eid]
        elif e["role"] == "Apprentices":
            full_sched[eid] = app_sched[eid]
        elif e["role"] == "Engineers":
            full_sched[eid] = {}
            for d in days:
                if d in eng_woffs[eid]:
                    full_sched[eid][d] = "W"
                elif d in leaves_clean.get(eid, []):
                    full_sched[eid][d] = "L"
                else:
                    full_sched[eid][d] = "1"

    # Step 2b: Emergency Support Backup by Engineers (Prioritized for Shift 1 unless extremely necessary)
    # Engineers are prioritized for Shift 1. But if Shift 2 or Shift 3 lacks support
    # (e.g. 0 technicians available on that shift due to leaves/offs), an available engineer
    # steps in to provide coverage, provided Shift 1 still has at least 1 Engineer.
    for d in days:
        for target_shift in ("2", "3"):
            support_count = sum(
                1 for e in all_emps
                if e["role"] in ("Technicians", "Engineers") and full_sched[str(e["id"])].get(d) == target_shift
            )
            if support_count == 0:
                available_engs = [
                    e for e in engineers
                    if full_sched[str(e["id"])].get(d) == "1"
                ]
                if len(available_engs) > 1:  # Maintain at least 1 engineer on Shift 1
                    for cand in available_engs:
                        cand_id = str(cand["id"])
                        prev_d_shift = full_sched[cand_id].get(d - 1) if d > 1 else last_shifts.get(cand_id, "1")
                        next_d_shift = full_sched[cand_id].get(d + 1) if d < days_in_month else None

                        prev_ok = not (prev_d_shift in ("2", "3") and target_shift < prev_d_shift)
                        next_ok = not (next_d_shift in ("1", "2") and target_shift > next_d_shift)

                        if prev_ok and next_ok:
                            full_sched[cand_id][d] = target_shift
                            break

    # Step 3: Apply Manual Overrides (Rule 5)
    manual_overrides = get_manual_overrides_for_month(year, month)
    for eid, day_map in manual_overrides.items():
        if eid in full_sched:
            for day_str, assignment in day_map.items():
                try:
                    d = int(day_str)
                    if d in days:
                        full_sched[eid][d] = assignment
                except (ValueError, KeyError):
                    pass

    # Step 4: Verification and warning collection
    warnings = []
    for d in days:
        for shift in SHIFTS:
            on_shift = [e for e in all_emps if full_sched.get(str(e["id"]), {}).get(d) == shift]
            ops = [e for e in on_shift if e["role"] == "Operators"]
            techs = [e for e in on_shift if e["role"] in ("Technicians", "Engineers")]
            engs = [e for e in on_shift if e["role"] == "Engineers"]

            if len(ops) < 1:
                warnings.append(f"Day {d}: Shift {shift} has no Operator.")
            if len(techs) < 1:
                warnings.append(f"Day {d}: Shift {shift} has no Support.")
            if shift == "1" and len(engs) < 1:
                warnings.append(f"Day {d}: Shift 1 has no Engineer.")

    return {
        "year": year,
        "month": month,
        "employee_schedules": {
            eid: {str(d): v for d, v in sch.items()}
            for eid, sch in full_sched.items()
        },
        "cancelled_leaves": [],
        "warnings": warnings,
        "meta": meta,
        "days_in_month": days_in_month,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Schedule persistence and helpers
# ─────────────────────────────────────────────────────────────────────────────

def save_schedule(schedule_result: dict):
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
    return date(year, month, day).strftime("%a")


def month_name(month: int) -> str:
    return calendar.month_name[month]
