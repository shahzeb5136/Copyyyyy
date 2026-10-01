"""
Generate the synthetic demo datasets shipped with the app.

Everything here is fictional: the company ("Northstar Technologies"), the people,
the projects and the numbers. The generator is seeded, so running it again
produces byte-identical CSVs.

Usage:
    python scripts/generate_data.py
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
AS_OF = pd.Timestamp("2026-09-30")
rng = np.random.default_rng(42)


# --------------------------------------------------------------------------- #
# Shared reference data
# --------------------------------------------------------------------------- #
DEPARTMENTS = pd.DataFrame(
    [
        ("D01", "Sales", "Go-to-Market", "CC-1100"),
        ("D02", "Marketing", "Go-to-Market", "CC-1200"),
        ("D03", "Customer Success", "Go-to-Market", "CC-1300"),
        ("D04", "Engineering", "Product & Engineering", "CC-2100"),
        ("D05", "Product Management", "Product & Engineering", "CC-2200"),
        ("D06", "Finance", "General & Administrative", "CC-3100"),
        ("D07", "People & HR", "General & Administrative", "CC-3200"),
        ("D08", "IT & Operations", "General & Administrative", "CC-3300"),
    ],
    columns=["department_id", "department_name", "division", "cost_center"],
)
DEPARTMENT_HEADS = {
    "D01": "Maya Okafor",
    "D02": "Lucas Brennan",
    "D03": "Priya Raman",
    "D04": "Daniel Kowalski",
    "D05": "Sofia Marquez",
    "D06": "Omar Haddad",
    "D07": "Hannah Lindqvist",
    "D08": "Kenji Watanabe",
}

FIRST_NAMES = """Aaliyah Aarav Adam Adebayo Aisha Alejandro Alex Amara Amelia Ana Andrei Arjun Ava
Ben Bianca Carlos Chen Chloe Daniel Diego Dina Elena Eli Emma Ethan Fatima Felix Freya Gabriel
Grace Hamza Hana Hiro Ibrahim Imani Isabel Ivan Jack James Jana Javier Jin Julia Kai Karim Kate
Laila Leo Lina Lucas Luis Maria Mariam Mateo Mei Mia Mohammed Nadia Naomi Nikhil Noah Nora Olga
Omar Oscar Paula Rahul Rania Ravi Rosa Ryan Sami Sara Sebastian Sofia Tariq Tom Valentina Wei
Yara Yusuf Zara Zoe""".split()
LAST_NAMES = """Abbott Adeyemi Ahmed Alvarez Andersen Bakr Banerjee Becker Bianchi Brooks Carter Chen
Costa Das Dimitrov Dubois Edwards Eriksen Farouk Fernandes Fischer Garcia Ghosh Gupta Hansen
Hassan Hughes Ibrahim Ivanova Jensen Johnson Kaur Khan Kim Kowalczyk Kumar Larsen Lee Lopez
Martin Mehta Mensah Moreau Murphy Nakamura Nasser Nguyen Novak Okoye Olsen Patel Perez Petrov
Quinn Rahman Reyes Rossi Saleh Santos Schmidt Sharma Silva Singh Sokolov Suzuki Tanaka Torres
Usman Varga Vargas Walsh Wang Weber Wilson Yamamoto Yilmaz Young Zhang""".split()


def month_range(start: str, end: str) -> pd.DatetimeIndex:
    return pd.date_range(start, end, freq="MS")


def fiscal_quarter(ts: pd.Series) -> pd.Series:
    return "Q" + ts.dt.quarter.astype(str)


def money(x: np.ndarray | float) -> np.ndarray:
    return np.round(np.asarray(x, dtype=float), 2)


# --------------------------------------------------------------------------- #
# 1. Finance: budget vs actuals + revenue
# --------------------------------------------------------------------------- #
def build_finance() -> dict[str, pd.DataFrame]:
    months = month_range("2025-01-01", "2026-12-01")

    salaries = {"D01": 900e3, "D02": 350e3, "D03": 450e3, "D04": 1_450e3,
                "D05": 300e3, "D06": 220e3, "D07": 180e3, "D08": 250e3}
    contractors = {"D01": 10e3, "D02": 40e3, "D03": 12e3, "D04": 180e3,
                   "D05": 20e3, "D06": 15e3, "D07": 10e3, "D08": 60e3}
    software = {"D01": 45e3, "D02": 55e3, "D03": 30e3, "D04": 120e3,
                "D05": 18e3, "D06": 16e3, "D07": 14e3, "D08": 140e3}
    travel = {"D01": 110e3, "D02": 25e3, "D03": 35e3, "D04": 15e3,
              "D05": 12e3, "D06": 8e3, "D07": 9e3, "D08": 10e3}
    hardware = {"D01": 8e3, "D02": 6e3, "D03": 7e3, "D04": 40e3,
                "D05": 5e3, "D06": 4e3, "D07": 4e3, "D08": 70e3}
    programs = {"D01": 30e3, "D02": 420e3}

    rows = []
    for m in months:
        growth = 1.0 if m.year == 2025 else 1.08
        q = (m.month - 1) // 3 + 1
        for dept in DEPARTMENTS.department_id:
            cats = {
                "Salaries & Wages": salaries[dept] * growth,
                "Benefits": salaries[dept] * 0.22 * growth,
                "Contractors": contractors[dept] * growth,
                "Software & Subscriptions": software[dept] * growth,
                "Travel & Entertainment": travel[dept] * growth
                * (1.35 if m.month == 1 else 0.7 if m.month == 8 else 1.0),
                "Facilities": salaries[dept] * 0.06 * growth,
                "Training & Development": salaries[dept] * 0.015 * growth
                * (1.6 if q in (1, 3) else 0.6),
                "Hardware & Equipment": hardware[dept] * growth
                * (2.2 if m.month in (1, 2) else 0.75),
            }
            if dept in programs:
                cats["Marketing Programs"] = programs[dept] * growth * (
                    1.25 if q in (2, 4) else 0.8)

            for cat, budget in cats.items():
                budget = round(budget, -2)
                closed = m <= AS_OF
                actual = budget * (1 + rng.normal(0, 0.035))
                # --- storylines -------------------------------------------- #
                if dept == "D04" and cat == "Contractors" and m.year == 2026 and 4 <= m.month <= 9:
                    actual *= 1.38  # Engineering backfilled open roles with contractors
                if dept == "D08" and cat == "Software & Subscriptions" and m.year == 2026:
                    actual *= 1.65 if m.month == 3 else 1.14  # licence true-up in March
                if dept == "D02" and cat == "Marketing Programs" and m.year == 2026:
                    actual *= 0.82 if q == 2 else 1.09 if q == 3 else 1.0
                if dept == "D01" and cat == "Travel & Entertainment" and m.year == 2025 and q == 4:
                    actual *= 1.27  # year-end deal push
                if dept == "D01" and cat == "Salaries & Wages" and m.year == 2026:
                    actual *= 0.94  # unfilled AE seats
                if dept == "D04" and cat == "Salaries & Wages" and m.year == 2026:
                    actual *= 0.965
                rows.append({
                    "period_month": m,
                    "fiscal_year": m.year,
                    "fiscal_quarter": f"Q{q}",
                    "department_id": dept,
                    "expense_category": cat,
                    "budget_usd": budget,
                    "actual_usd": round(actual, 2) if closed else np.nan,
                    "period_status": "Closed" if closed else "Open",
                })
    budget_actuals = pd.DataFrame(rows)

    # Revenue ------------------------------------------------------------- #
    regions = {"North America": 1.0, "Europe": 0.62, "Middle East & Africa": 0.34, "Asia Pacific": 0.41}
    products = {"Cloud Platform": 1_150e3, "Analytics Suite": 520e3,
                "Professional Services": 310e3, "Support & Maintenance": 260e3}
    deal_size = {"Cloud Platform": 48e3, "Analytics Suite": 26e3,
                 "Professional Services": 35e3, "Support & Maintenance": 12e3}
    rev_rows = []
    for m in months:
        q = (m.month - 1) // 3 + 1
        t = (m.year - 2025) * 12 + m.month - 1  # months since Jan 2025
        season = {1: 0.9, 2: 0.98, 3: 1.04, 4: 1.12}[q] * (1.08 if m.month in (3, 6, 9, 12) else 0.96)
        for region, rw in regions.items():
            for product, base in products.items():
                trend = 1 + 0.012 * t
                if product == "Analytics Suite":
                    trend += 0.01 * t
                if region == "Middle East & Africa":
                    trend += 0.018 * t
                target = round(base * rw * trend * season, -3)
                closed = m <= AS_OF
                perf = 1 + rng.normal(0.01, 0.06)
                if region == "Middle East & Africa":
                    perf += 0.09
                if region == "Europe" and m.year == 2026 and q == 2:
                    perf -= 0.17
                if product == "Professional Services":
                    perf += rng.normal(0, 0.1)
                if region == "North America" and product == "Cloud Platform" and m.year == 2026 and q == 3:
                    perf += 0.06
                actual = target * perf
                deals = max(1, int(round(actual / deal_size[product] * rng.uniform(0.85, 1.15))))
                rev_rows.append({
                    "period_month": m,
                    "fiscal_year": m.year,
                    "fiscal_quarter": f"Q{q}",
                    "region": region,
                    "product_line": product,
                    "target_usd": target,
                    "actual_usd": round(actual, 2) if closed else np.nan,
                    "deals_closed": deals if closed else pd.NA,
                    "period_status": "Closed" if closed else "Open",
                })
    revenue = pd.DataFrame(rev_rows)
    revenue["deals_closed"] = revenue["deals_closed"].astype("Int64")

    departments = DEPARTMENTS.copy()
    departments["department_head"] = departments.department_id.map(DEPARTMENT_HEADS)
    departments = departments[["department_id", "department_name", "division", "department_head", "cost_center"]]
    return {"departments": departments, "budget_actuals": budget_actuals, "revenue": revenue}


# --------------------------------------------------------------------------- #
# 2. HR: employees + leave
# --------------------------------------------------------------------------- #
ROLE_NOUN = {
    "D01": "Account Executive", "D02": "Marketing Specialist", "D03": "Customer Success Manager",
    "D04": "Software Engineer", "D05": "Product Manager", "D06": "Financial Analyst",
    "D07": "People Partner", "D08": "Systems Engineer",
}
DEPT_SHORT = {
    "D01": "Sales", "D02": "Marketing", "D03": "Customer Success", "D04": "Engineering",
    "D05": "Product", "D06": "Finance", "D07": "People", "D08": "IT",
}
LEVEL_BASE_SALARY = {1: 52e3, 2: 72e3, 3: 96e3, 4: 124e3, 5: 150e3, 6: 190e3, 7: 255e3}
DEPT_PAY = {"D01": 1.0, "D02": 0.95, "D03": 0.92, "D04": 1.16, "D05": 1.12, "D06": 1.0, "D07": 0.94, "D08": 1.03}
LOCATION_PAY = {"Dubai": 1.0, "London": 1.06, "New York": 1.16, "Singapore": 1.02, "Remote": 0.93}
TARGET_ACTIVE = {"D01": 72, "D02": 31, "D03": 56, "D04": 132, "D05": 24, "D06": 19, "D07": 15, "D08": 27}
ANNUAL_ATTRITION = {"D01": 0.21, "D02": 0.14, "D03": 0.24, "D04": 0.11, "D05": 0.10, "D06": 0.08, "D07": 0.09, "D08": 0.12}


def job_title(dept: str, level: int) -> str:
    if level <= 4:
        prefix = {1: "Associate ", 2: "", 3: "Senior ", 4: "Lead "}[level]
        return f"{prefix}{ROLE_NOUN[dept]}"
    return {5: "Manager", 6: "Director", 7: "VP"}[level] + f", {DEPT_SHORT[dept]}"


def random_date(start: pd.Timestamp, end: pd.Timestamp) -> pd.Timestamp:
    span = max(1, (end - start).days)
    return (start + pd.Timedelta(days=int(rng.integers(0, span)))).normalize()


def build_hr() -> dict[str, pd.DataFrame]:
    people = []
    used_names = set()
    next_id = 1001
    start_of_company = pd.Timestamp("2017-01-01")

    def new_name():
        while True:
            name = (str(rng.choice(FIRST_NAMES)), str(rng.choice(LAST_NAMES)))
            if name not in used_names:
                used_names.add(name)
                return name

    for dept, target in TARGET_ACTIVE.items():
        # Leaders first (one VP, directors, managers) so reporting lines exist.
        n_total = int(target * 1.55)
        levels = [7] + [6] * max(1, target // 45) + [5] * max(1, target // 9)
        remaining = n_total - len(levels)
        levels += list(rng.choice([1, 2, 3, 4], size=remaining, p=[0.17, 0.38, 0.30, 0.15]))
        for lvl in levels:
            lvl = int(lvl)
            if lvl >= 6:
                hire = random_date(start_of_company, pd.Timestamp("2022-06-30"))
            else:
                # Hiring skewed towards recent years.
                year = int(rng.choice(range(2018, 2027), p=[.04, .05, .06, .10, .13, .14, .16, .18, .14]))
                hire = random_date(pd.Timestamp(f"{year}-01-01"), min(pd.Timestamp(f"{year}-12-31"), AS_OF))
            location = str(rng.choice(list(LOCATION_PAY), p=[0.38, 0.2, 0.17, 0.12, 0.13]))
            emp_type = "Full-time" if lvl >= 5 else str(rng.choice(["Full-time", "Part-time", "Contractor"], p=[0.88, 0.05, 0.07]))
            salary = LEVEL_BASE_SALARY[lvl] * DEPT_PAY[dept] * LOCATION_PAY[location] * rng.normal(1, 0.06)
            if emp_type == "Part-time":
                salary *= 0.6
            # Termination: VP and directors stay; others leave with dept-specific hazard.
            term_date, term_type, term_reason = pd.NaT, None, None
            if lvl < 6:
                years_employed = (AS_OF - hire).days / 365.25
                p_leave = 1 - (1 - ANNUAL_ATTRITION[dept]) ** years_employed
                if rng.random() < p_leave:
                    term_date = random_date(hire + pd.Timedelta(days=90), AS_OF)
                    if term_date > hire + pd.Timedelta(days=90):
                        voluntary = rng.random() < 0.78
                        term_type = "Voluntary" if voluntary else "Involuntary"
                        term_reason = str(rng.choice(
                            ["Better opportunity", "Compensation", "Relocation", "Career change", "Work-life balance"],
                            p=[0.36, 0.24, 0.14, 0.14, 0.12]) if voluntary else rng.choice(
                            ["Performance", "Restructuring", "Contract ended"], p=[0.45, 0.35, 0.20]))
                        if term_type == "Involuntary" and emp_type == "Contractor":
                            term_reason = "Contract ended"
                        elif term_reason == "Contract ended":
                            term_reason = "Restructuring"
                    else:
                        term_date = pd.NaT
            first, last = new_name()
            rating = int(rng.choice([1, 2, 3, 4, 5], p=[0.04, 0.13, 0.48, 0.26, 0.09]))
            if lvl >= 6:
                rating = int(rng.choice([3, 4, 5], p=[0.4, 0.45, 0.15]))
            if term_type == "Involuntary" and term_reason == "Performance":
                rating = int(rng.choice([1, 2]))
            recent_hire = hire > pd.Timestamp("2026-03-31")
            promo = pd.NaT
            if lvl >= 2 and (AS_OF - hire).days > 540 and rng.random() < 0.45:
                promo = random_date(hire + pd.Timedelta(days=365), min(AS_OF, term_date) if pd.notna(term_date) else AS_OF)
            people.append({
                "employee_id": f"E{next_id}",
                "first_name": first,
                "last_name": last,
                "department_id": dept,
                "job_title": job_title(dept, lvl),
                "job_level": f"L{lvl}",
                "is_people_manager": lvl >= 5,
                "employment_type": emp_type,
                "work_location": location,
                "hire_date": hire,
                "termination_date": term_date,
                "termination_type": term_type,
                "termination_reason": term_reason,
                "employment_status": "Terminated" if pd.notna(term_date) else "Active",
                "base_salary_usd": round(salary, -2),
                "performance_rating": None if recent_hire else rating,
                "last_promotion_date": promo,
                "_level": lvl,
            })
            next_id += 1

    emp = pd.DataFrame(people)

    # Reporting lines: VP -> CEO (none); directors -> VP; managers -> director/VP; ICs -> manager.
    manager_ids = []
    for _, r in emp.iterrows():
        same = emp[(emp.department_id == r.department_id)]
        if r._level == 7:
            manager_ids.append(None)
            continue
        pool = same[same._level > max(r._level, 4)] if r._level >= 5 else same[same._level == 5]
        if pool.empty:
            pool = same[same._level == 7]
        manager_ids.append(str(rng.choice(pool.employee_id.values)))
    emp["manager_id"] = manager_ids

    # Shuffle row order a little (by hire date) and sort ids for readability.
    emp = emp.sort_values("employee_id").reset_index(drop=True)
    emp["performance_rating"] = emp["performance_rating"].astype("Int64")

    # Leave records --------------------------------------------------------- #
    leave_rows = []
    leave_id = 1
    window_start, window_end = pd.Timestamp("2025-01-01"), AS_OF
    leave_profile = {
        "Annual leave": (4.0, (1, 10)),
        "Sick leave": (1.6, (1, 4)),
        "Parental leave": (0.03, (20, 90)),
        "Unpaid leave": (0.05, (2, 15)),
        "Bereavement": (0.06, (2, 5)),
    }
    for _, e in emp.iterrows():
        if e.employment_type == "Contractor":
            continue
        start = max(window_start, e.hire_date)
        end = min(window_end, e.termination_date) if pd.notna(e.termination_date) else window_end
        if end <= start:
            continue
        years = (end - start).days / 365.25
        sick_multiplier = 1.5 if e.department_id in ("D03", "D01") else 1.0
        for leave_type, (per_year, (lo, hi)) in leave_profile.items():
            lam = per_year * years * (sick_multiplier if leave_type == "Sick leave" else 1.0)
            for _ in range(rng.poisson(lam)):
                s = random_date(start, end)
                while s.weekday() >= 5:
                    s += pd.Timedelta(days=1)
                days = int(rng.integers(lo, hi + 1))
                e_date = pd.bdate_range(s, periods=days)[-1]
                leave_rows.append({
                    "leave_id": f"LV{leave_id:05d}",
                    "employee_id": e.employee_id,
                    "leave_type": leave_type,
                    "start_date": s,
                    "end_date": e_date,
                    "working_days": days,
                })
                leave_id += 1
    leave = pd.DataFrame(leave_rows).sort_values(["start_date", "employee_id"]).reset_index(drop=True)
    leave["leave_id"] = [f"LV{i:05d}" for i in range(1, len(leave) + 1)]

    active = emp[emp.employment_status == "Active"].groupby("department_id").size()
    plan_delta = {"D01": 9, "D02": 2, "D03": 6, "D04": 12, "D05": -1, "D06": 1, "D07": 0, "D08": 3}
    departments = DEPARTMENTS.copy()
    departments["department_head"] = departments.department_id.map(DEPARTMENT_HEADS)
    departments["approved_headcount_2026"] = [int(active.get(d, 0) + plan_delta[d]) for d in departments.department_id]
    departments = departments[["department_id", "department_name", "division", "department_head",
                               "cost_center", "approved_headcount_2026"]]

    emp = emp.drop(columns="_level")
    return {"departments": departments, "employees": emp, "leave_records": leave}


# --------------------------------------------------------------------------- #
# 3. PMO: projects, tasks, team members
# --------------------------------------------------------------------------- #
TEAM = [
    ("Project Manager", 95, 8), ("Business Analyst", 80, 6), ("Solution Architect", 125, 4),
    ("Developer", 90, 9), ("Data Engineer", 100, 4), ("QA Engineer", 70, 4),
    ("UX Designer", 85, 2), ("Change Manager", 88, 2),
]
PHASES = [
    ("Initiation", 0.06, ["Project charter", "Stakeholder kickoff"]),
    ("Planning", 0.10, ["Requirements gathering", "Plan & budget approval"]),
    ("Design", 0.16, ["Solution architecture", "Detailed design", "Design sign-off"]),
    ("Build", 0.34, ["Build sprint 1", "Build sprint 2", "Integrations", "Data migration"]),
    ("Testing", 0.17, ["System integration testing", "User acceptance testing"]),
    ("Deployment", 0.11, ["Production cutover", "Go-live", "Hypercare"]),
    ("Closure", 0.06, ["Lessons learned", "Project closure report"]),
]
MILESTONES = {"Plan & budget approval", "Design sign-off", "Go-live", "Project closure report"}
PHASE_ROLE = {
    "Initiation": "Project Manager", "Planning": "Business Analyst", "Design": "Solution Architect",
    "Build": "Developer", "Testing": "QA Engineer", "Deployment": "Developer", "Closure": "Project Manager",
}

PROJECTS = [
    # name, portfolio, sponsor dept, priority, start, planned end, scenario
    ("CRM Migration to Cloud", "Customer Experience", "Sales", "High", "2025-03-03", "2026-02-27", "completed_late"),
    ("Data Warehouse Modernization", "Data & Analytics", "Finance", "High", "2025-06-02", "2026-12-18", "off_track"),
    ("Customer Mobile App v3", "Customer Experience", "Product Management", "High", "2025-10-06", "2026-11-27", "on_track"),
    ("ERP Finance Module Upgrade", "Digital Transformation", "Finance", "High", "2025-04-07", "2026-01-30", "completed"),
    ("Zero Trust Network Rollout", "Infrastructure & Security", "IT & Operations", "High", "2025-09-01", "2026-10-30", "at_risk"),
    ("PDPL & GDPR Compliance Program", "Compliance", "Finance", "High", "2025-05-05", "2026-03-31", "completed"),
    ("Self-Service Analytics Portal", "Data & Analytics", "Product Management", "Medium", "2026-01-12", "2026-12-11", "on_track"),
    ("HR Information System Replacement", "Digital Transformation", "People & HR", "Medium", "2025-11-03", "2026-09-25", "off_track"),
    ("Customer Support Chatbot", "Customer Experience", "Customer Success", "Medium", "2026-02-02", "2026-11-13", "at_risk"),
    ("Data Center Exit", "Infrastructure & Security", "IT & Operations", "High", "2025-07-07", "2027-03-26", "on_track"),
    ("Procurement Automation", "Digital Transformation", "Finance", "Low", "2025-08-04", "2026-04-24", "completed_late"),
    ("Website Redesign", "Customer Experience", "Marketing", "Medium", "2026-03-02", "2026-10-23", "on_track"),
    ("Master Data Management", "Data & Analytics", "IT & Operations", "Medium", "2026-04-06", "2027-02-26", "on_hold"),
    ("SOC 2 Type II Certification", "Compliance", "IT & Operations", "High", "2025-09-15", "2026-07-17", "completed_late"),
    ("Field Service Scheduling", "Digital Transformation", "Customer Success", "Low", "2026-10-12", "2027-05-28", "not_started"),
    ("Endpoint Management Refresh", "Infrastructure & Security", "IT & Operations", "Low", "2026-11-02", "2027-03-12", "not_started"),
    ("Revenue Forecasting ML Model", "Data & Analytics", "Sales", "Medium", "2026-05-04", "2026-12-18", "at_risk"),
    ("E-Invoicing Compliance", "Compliance", "Finance", "High", "2026-10-05", "2027-06-25", "not_started"),
]
SCENARIO = {
    # slip (fraction of planned duration), status, health, hours overrun, vendor spend share
    "completed": (0.0, "Completed", "Closed", 0.03),
    "completed_late": (0.12, "Completed", "Closed", 0.16),
    "on_track": (0.02, "In Progress", "On Track", 0.04),
    "at_risk": (0.13, "In Progress", "At Risk", 0.14),
    "off_track": (0.28, "In Progress", "Off Track", 0.27),
    "on_hold": (0.0, "On Hold", "On Hold", 0.05),
    "not_started": (0.0, "Not Started", "Not Started", 0.0),
}


def build_pmo() -> dict[str, pd.DataFrame]:
    members = []
    mid = 1
    for role, rate, count in TEAM:
        for _ in range(count):
            first, last = str(rng.choice(FIRST_NAMES)), str(rng.choice(LAST_NAMES))
            members.append({
                "member_id": f"TM{mid:02d}",
                "full_name": f"{first} {last}",
                "role": role,
                "home_department": "IT & Operations" if role != "Change Manager" else "People & HR",
                "hourly_rate_usd": float(round(rate * rng.uniform(0.9, 1.12))),
                "weekly_capacity_hours": int(rng.choice([40, 40, 40, 32])),
            })
            mid += 1
    team = pd.DataFrame(members)
    by_role = {r: team[team.role == r].member_id.tolist() for r in team.role.unique()}
    rate = team.set_index("member_id").hourly_rate_usd

    projects, tasks = [], []
    tid = 1
    for i, (name, portfolio, sponsor, priority, start, end, scenario) in enumerate(PROJECTS, start=1):
        pid = f"PRJ-{i:03d}"
        start, planned_end = pd.Timestamp(start), pd.Timestamp(end)
        slip, status, health, overrun = SCENARIO[scenario]
        duration = (planned_end - start).days
        pm = str(rng.choice(by_role["Project Manager"]))
        hold_date = start + pd.Timedelta(days=int(duration * 0.22)) if scenario == "on_hold" else None

        cursor_planned = start
        cursor_actual = start
        project_tasks = []
        for phase, share, names in PHASES:
            phase_days = duration * share
            phase_days_actual = phase_days * (1 + slip * rng.uniform(0.6, 1.4))
            per = phase_days / len(names)
            per_actual = phase_days_actual / len(names)
            for tname in names:
                p_start = cursor_planned
                p_end = cursor_planned + pd.Timedelta(days=max(1, int(per)))
                a_start = cursor_actual
                a_end = cursor_actual + pd.Timedelta(days=max(1, int(per_actual)))
                cursor_planned, cursor_actual = p_end, a_end
                milestone = tname in MILESTONES
                if milestone:  # milestones are single-day events at the end of the window
                    p_start, a_start = p_end, a_end
                role = PHASE_ROLE[phase]
                if tname in ("Data migration", "Integrations") and portfolio == "Data & Analytics":
                    role = "Data Engineer"
                if tname == "Detailed design" and portfolio == "Customer Experience":
                    role = "UX Designer"
                if tname == "Stakeholder kickoff" and rng.random() < 0.5:
                    role = "Change Manager"
                assignee = pm if role == "Project Manager" else str(rng.choice(by_role[role]))
                workdays = max(1, len(pd.bdate_range(p_start, p_end)))
                # Hours cover the whole task team; the assignee is the task owner.
                est = 4.0 if milestone else float(round(workdays * 8 * rng.uniform(0.8, 2.2)))

                # Status as of AS_OF
                effective_now = min(AS_OF, hold_date) if hold_date is not None else AS_OF
                if scenario == "not_started" or a_start > effective_now:
                    t_status, pct, completed, actual_hours = "Not Started", 0, pd.NaT, 0.0
                elif a_end <= effective_now:
                    t_status, pct, completed = "Completed", 100, a_end
                    actual_hours = 4.0 if milestone else round(est * (1 + overrun + rng.normal(0, 0.08)), 1)
                else:
                    elapsed = (effective_now - a_start).days / max(1, (a_end - a_start).days)
                    pct = int(min(95, max(5, round(elapsed * 100 / 5) * 5)))
                    t_status = "In Progress"
                    if scenario == "on_hold":
                        t_status = "Blocked"
                    elif scenario == "off_track" and rng.random() < 0.45:
                        t_status = "Blocked"
                    completed = pd.NaT
                    actual_hours = round(est * pct / 100 * (1 + overrun + rng.normal(0, 0.08)), 1)
                project_tasks.append({
                    "task_id": f"T{tid:04d}",
                    "project_id": pid,
                    "task_name": tname,
                    "phase": phase,
                    "assignee_id": assignee,
                    "start_date": p_start,
                    "due_date": p_end,
                    "completed_date": completed,
                    "status": t_status,
                    "percent_complete": pct,
                    "estimated_hours": est,
                    "actual_hours": max(0.0, actual_hours),
                    "is_milestone": milestone,
                })
                tid += 1
        tasks.extend(project_tasks)

        pt = pd.DataFrame(project_tasks)
        forecast_end = max(planned_end, cursor_actual) if scenario != "on_hold" else planned_end
        if status == "Completed":
            assert (pt.status == "Completed").all() and forecast_end <= AS_OF, name
        labor_budget = float((pt.estimated_hours * pt.assignee_id.map(rate)).sum())
        vendor_budget = labor_budget * rng.uniform(0.15, 0.6)
        budget = round((labor_budget + vendor_budget) * 1.1, -3)
        labor_actual = float((pt.actual_hours * pt.assignee_id.map(rate)).sum())
        progress = float((pt.estimated_hours * pt.percent_complete / 100).sum() / pt.estimated_hours.sum())
        vendor_actual = vendor_budget * progress * (1 + overrun * 1.4 + rng.normal(0, 0.04))
        projects.append({
            "project_id": pid,
            "project_name": name,
            "portfolio": portfolio,
            "sponsor_department": sponsor,
            "project_manager_id": pm,
            "priority": priority,
            "status": status,
            "health": health,
            "start_date": start,
            "planned_end_date": planned_end,
            "forecast_end_date": forecast_end if status != "Completed" else pd.NaT,
            "actual_end_date": forecast_end if status == "Completed" else pd.NaT,
            "budget_usd": budget,
            "actual_cost_usd": round(labor_actual + max(0.0, vendor_actual), 2),
            "percent_complete": int(round(progress * 100)),
        })

    projects_df = pd.DataFrame(projects)
    tasks_df = pd.DataFrame(tasks)
    return {"projects": projects_df, "tasks": tasks_df, "team_members": team}


# --------------------------------------------------------------------------- #
# 4. Sample CSV for the "bring your own data" flow (no data dictionary on purpose)
# --------------------------------------------------------------------------- #
def build_sample_orders() -> pd.DataFrame:
    categories = {"Electronics": (40, 900), "Home & Kitchen": (12, 260), "Fashion": (15, 180),
                  "Sports & Outdoors": (18, 320), "Beauty": (8, 90), "Books": (6, 45)}
    regions = ["UAE", "Saudi Arabia", "Qatar", "Kuwait", "Oman", "Bahrain"]
    channels = ["Website", "Mobile App", "Marketplace"]
    rows = []
    for i in range(1, 2401):
        date = pd.Timestamp("2025-01-01") + pd.Timedelta(days=int(rng.integers(0, 365)))
        cat = str(rng.choice(list(categories), p=[0.24, 0.2, 0.2, 0.12, 0.14, 0.1]))
        lo, hi = categories[cat]
        price = round(float(rng.uniform(lo, hi)), 2)
        qty = int(rng.choice([1, 1, 1, 2, 2, 3, 4]))
        discount = int(rng.choice([0, 0, 0, 5, 10, 15, 20, 25]))
        if date.month == 11:  # White Friday / Black Friday promotions
            discount = max(discount, int(rng.choice([15, 20, 30])))
        net = round(price * qty * (1 - discount / 100), 2)
        rows.append({
            "Order ID": f"SO-{100000 + i}",
            "Order Date": date.strftime("%d/%m/%Y"),
            "Customer Country": str(rng.choice(regions, p=[0.42, 0.26, 0.1, 0.09, 0.07, 0.06])),
            "Sales Channel": str(rng.choice(channels, p=[0.45, 0.4, 0.15])),
            "Product Category": cat,
            "Qty": qty,
            "Unit Price (AED)": f"{price:,.2f}",
            "Discount %": discount,
            "Net Sales (AED)": f"{net:,.2f}",
            "Returned?": "Yes" if rng.random() < (0.11 if cat == "Fashion" else 0.04) else "No",
        })
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
def write(dataset: str, tables: dict[str, pd.DataFrame]) -> None:
    out = DATA_DIR / dataset
    out.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        df = df.copy()
        for col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                df[col] = df[col].dt.strftime("%Y-%m-%d")
        df.to_csv(out / f"{name}.csv", index=False)
        print(f"  {dataset}/{name}.csv  {len(df):>6,} rows × {df.shape[1]} cols")


if __name__ == "__main__":
    print("Generating demo data (as of", AS_OF.date(), ")")
    write("finance", build_finance())
    write("hr", build_hr())
    write("projects", build_pmo())
    sample = build_sample_orders()
    (DATA_DIR / "samples").mkdir(parents=True, exist_ok=True)
    sample.to_csv(DATA_DIR / "samples" / "online_store_orders.csv", index=False)
    print(f"  samples/online_store_orders.csv  {len(sample):>6,} rows × {sample.shape[1]} cols")
    print("Done.")
