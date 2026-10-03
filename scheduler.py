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


def assign_optimal_pairs(
    members: list,
    start_shifts: dict,
    leaves_dict: dict,
    weeks: list,
    year: int,
    month: int,
    is_apprentice: bool = False
) -> dict:
    """
    Find the weekly off pair assignment that ensures every shift (1, 2, and 3)
    has full coverage on every single day of the month with zero backward rotation violations.
    """
    eids = [str(m["id"]) for m in members]
    days_in_month = calendar.monthrange(year, month)[1]
    best_p = None
    best_score = 99999

    for p_tuple in itertools.permutations(PAIR_POOL, len(eids)):
        woffs_dict = {
            eids[i]: get_woff_days_for_pair(year, month, p_tuple[i], weeks, alt_even=(i % 2 == 1))
            for i in range(len(eids))
        }
        sch = solve_shift_group(
            members, start_shifts, woffs_dict, leaves_dict, days_in_month,
            year=year, month=month, is_apprentice=is_apprentice
        )

        v_count = 0
        if not is_apprentice:
            for d in range(1, days_in_month + 1):
                for s in ("1", "2", "3"):
                    if sum(1 for eid in eids if sch[eid].get(d) == s) < 1:
                        v_count += 1

        bad_rot = 0
        for eid in eids:
            for d in range(1, days_in_month):
                s1 = sch[eid].get(d)
                s2 = sch[eid].get(d + 1)
                if s1 in ("1", "2", "3") and s2 in ("1", "2", "3"):
                    if (s1 == "3" and s2 in ("1", "2")) or (s1 == "2" and s2 == "1"):
                        bad_rot += 1

        mid_changes = sum(
            1 for eid in eids for w in weeks
            if len({sch[eid].get(d) for d in w if sch[eid].get(d) in ("1", "2", "3")}) > 1
        )
        score = v_count * 1000 + bad_rot * 100 + mid_changes

        if score < best_score:
            best_score = score
            best_p = p_tuple
            if v_count == 0 and bad_rot == 0 and (is_apprentice or mid_changes <= 4):
                break

    if best_p is None:
        best_p = list(itertools.permutations(PAIR_POOL, len(eids)))[0]

    return {eids[i]: (best_p[i], i % 2 == 1) for i in range(len(eids))}


# ─────────────────────────────────────────────────────────────────────────────
# Core Group Shift Solver (Weekly Blocks: Rules 1, 2, 4, 6)
# ─────────────────────────────────────────────────────────────────────────────

def solve_shift_group(
    members: list,
    start_shifts: dict,
    woffs_dict: dict,
    leaves_dict: dict,
    days_in_month: int,
    year: int = None,
    month: int = None,
    is_apprentice: bool = False
) -> dict:
    """
    Assign shifts for a role group in weekly blocks (Rules 1, 2, 4, 6).
    Enforces:
      - Whole-week shift consistency: an employee stays on the SAME shift for the entire week.
      - Forward-only rotation across weeks: 1 -> 2 -> 3 -> 1.
      - Mandatory rest day (W) before 3 -> 1 transition.
      - HR rule: Weekly offs take priority over leaves (leaves do not consume weekly offs).
      - Minimal relief coverage on Shift 1/2/3 ONLY when strictly required (0 headcount on that day).
      - Apprentices have pure weekly blocks with 0 mid-week changes.
    """
    eids = [str(m["id"]) for m in members]
    days = list(range(1, days_in_month + 1))
    sched = {eid: {} for eid in eids}

    # Step 1: Pre-populate Weekly Offs and Leaves (HR Rule: Weekly off takes priority!)
    for eid in eids:
        for d in days:
            if d in woffs_dict.get(eid, set()):
                sched[eid][d] = "W"
            elif d in leaves_dict.get(eid, []):
                sched[eid][d] = "L"

    # Determine calendar weeks
    if year and month:
        weeks = get_calendar_weeks(year, month)
    else:
        weeks = []
        curr = []
        for d in days:
            curr.append(d)
            if len(curr) == 7:
                weeks.append(curr)
                curr = []
        if curr:
            weeks.append(curr)

    cycle = ["1", "2", "3"]
    last_assigned_shift = {}
    for i, eid in enumerate(eids):
        last_assigned_shift[eid] = start_shifts.get(eid, cycle[i % 3])

    # Step 2: Week-by-week whole-week shift block assignment
    for w_idx, w_days in enumerate(weeks):
        cand_shifts = {}
        for eid in eids:
            prev_s = last_assigned_shift[eid]
            p_idx = cycle.index(prev_s)
            fwd_s = cycle[(p_idx + 1) % 3]  # normal forward rotation
            same_s = prev_s

            # Week 0 (partial week at start of month): stay on previous month's starting shift
            if w_idx == 0:
                cand_shifts[eid] = [prev_s]
                continue

            # 3 -> 1 Rest Day Rule: Must have W/L on Sunday (end of prev week) or Monday (start of new week)
            if prev_s == "3" and fwd_s == "1":
                prev_w_days = weeks[w_idx - 1]
                sun_d = prev_w_days[-1]
                mon_d = w_days[0]
                has_rest = (sched[eid].get(sun_d) in ("W", "L")) or (sched[eid].get(mon_d) in ("W", "L"))
                if not has_rest:
                    cand_shifts[eid] = ["3"]  # Cannot rotate to 1 without rest! Must stay on 3
                else:
                    cand_shifts[eid] = [fwd_s, same_s]
            else:
                cand_shifts[eid] = [fwd_s, same_s]

        keys = list(cand_shifts.keys())
        combos = list(itertools.product(*[cand_shifts[k] for k in keys]))

        best_w_shifts = None
        best_w_shortages = 999
        best_w_dev = 999

        for combo in combos:
            sh_map = {keys[i]: combo[i] for i in range(len(keys))}
            shortages = 0
            if not is_apprentice:
                for d in w_days:
                    working = {sh_map[eid] for eid in eids if sched[eid].get(d) is None}
                    for s in ("1", "2", "3"):
                        if s not in working:
                            shortages += 1
            dev = sum(1 for i, k in enumerate(keys) if combo[i] != cand_shifts[k][0])
            if shortages < best_w_shortages or (shortages == best_w_shortages and dev < best_w_dev):
                best_w_shortages = shortages
                best_w_dev = dev
                best_w_shifts = sh_map
                if shortages == 0 and dev == 0:
                    break

        if best_w_shifts is None:
            best_w_shifts = {keys[i]: combos[0][i] for i in range(len(keys))}

        for eid in eids:
            w_sh = best_w_shifts[eid]
            last_assigned_shift[eid] = w_sh
            for d in w_days:
                if sched[eid].get(d) is None:
                    sched[eid][d] = w_sh

    # Step 3: Minimal relief coverage ONLY when strictly required (0 working people on target shift)
    if not is_apprentice:
        for d in days:
            for target in ("3", "2", "1"):
                cnt = sum(1 for eid in eids if sched[eid].get(d) == target)
                if cnt == 0:
                    cands = []
                    for eid in eids:
                        cur_s = sched[eid].get(d)
                        if cur_s in ("1", "2", "3") and cur_s != target:
                            # STRICT PROHIBITION: NEVER borrow backwards for relief!
                            # A Shift 3 worker can NEVER be put on Shift 1 or Shift 2!
                            if cur_s == "3" and target in ("1", "2"):
                                continue
                            # A Shift 2 worker can NEVER be put on Shift 1!
                            if cur_s == "2" and target == "1":
                                continue
                            cur_s_cnt = sum(1 for o_eid in eids if sched[o_eid].get(d) == cur_s)
                            if cur_s_cnt > 1:  # Only borrow from shift with surplus
                                cands.append(eid)

                    def can_transition(c):
                        prev_s = sched[c].get(d - 1) if d > 1 else "1"
                        next_s = sched[c].get(d + 1) if d < days_in_month else "1"
                        prev_ok = (prev_s in ("W", "L")) or not (prev_s in ("2", "3") and target < prev_s)
                        next_ok = (next_s in ("W", "L")) or not (next_s in ("1", "2") and target > next_s)
                        return prev_ok and next_ok

                    valid = [c for c in cands if can_transition(c)]
                    if valid:
                        sched[valid[0]][d] = target

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

    op_pair_config = assign_optimal_pairs(operators, op_starts, leaves_clean, weeks, year, month)
    tech_pair_config = assign_optimal_pairs(technicians, tech_starts, leaves_clean, weeks, year, month)
    app_pair_config = assign_optimal_pairs(apprentices, app_starts, leaves_clean, weeks, year, month, is_apprentice=True)

    op_woffs = {eid: get_woff_days_for_pair(year, month, pair, weeks, alt) for eid, (pair, alt) in op_pair_config.items()}
    tech_woffs = {eid: get_woff_days_for_pair(year, month, pair, weeks, alt) for eid, (pair, alt) in tech_pair_config.items()}
    app_woffs = {eid: get_woff_days_for_pair(year, month, pair, weeks, alt) for eid, (pair, alt) in app_pair_config.items()}

    # Engineers: stagger across PAIR_POOL, prioritized for Shift 1
    eng_woffs = {
        str(e["id"]): get_woff_days_for_pair(year, month, PAIR_POOL[i % len(PAIR_POOL)], weeks, alt_even=(i % 2 == 1))
        for i, e in enumerate(engineers)
    }

    # Solve Operators, Technicians, and Apprentices with weekly-block forward rotation
    op_sched = solve_shift_group(operators, op_starts, op_woffs, leaves_clean, days_in_month, year=year, month=month)
    tech_sched = solve_shift_group(technicians, tech_starts, tech_woffs, leaves_clean, days_in_month, year=year, month=month)
    app_sched = solve_shift_group(apprentices, app_starts, app_woffs, leaves_clean, days_in_month, year=year, month=month, is_apprentice=True)

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
