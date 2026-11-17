"""Synthetic history of a staffing agency modelled on BC.

The agency places workers at client plants. Workers follow weekly shift
rotations; they come to work, fall ill, take vacations, sometimes do not
show up, work overtime; buses bring them to the plants; the agency invoices
the clients for the hours worked. The generator writes three sources:

* the operational PostgreSQL database (OLTP) of the agency,
* monthly CSV exports of the accounting system (invoices to the clients),
* a JSON file served by the transport REST API (trips).

It also injects labelled incidents - a mass no-show, an urgent order with
overtime, road works delaying the buses, an outage of the time clock - and
writes their list to ground_truth/ for evaluating anomaly detection. The ETL
never reads ground_truth/.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import holidays
import numpy as np
import pandas as pd

from bikpi.config import settings, source_engine
from bikpi.db import copy_frame

PRAGUE = ZoneInfo("Europe/Prague")
START = date(2024, 1, 1)
FUTURE_DAYS = 28  # the schedule is known four weeks ahead


@dataclass(frozen=True)
class Plant:
    code: str
    name: str
    client: str
    city: str
    address: str
    headcount: int
    workdays: tuple[int, ...]  # ISO weekdays
    shifts: tuple[str, ...]
    agency_rate: float  # CZK per hour billed to the client
    worker_rate: float  # CZK per hour paid to the worker
    transport_share: float  # share of workers who ride the agency bus
    season: str  # demand pattern


PLANTS = (
    Plant("AUT-MB", "Automobilka Mladá Boleslav", "AutoMotive CZ a.s.", "Mladá Boleslav",
          "Průmyslová 12", 180, (1, 2, 3, 4, 5), ("morning", "afternoon", "night"), 265, 176, 0.65, "automotive"),
    Plant("LOG-BOR", "Logistické centrum Bor", "EuroLogistik s.r.o.", "Bor u Tachova",
          "Nová Hospoda 5", 140, (1, 2, 3, 4, 5, 6), ("morning", "afternoon", "night"), 242, 166, 0.55, "logistics"),
    Plant("ELE-JIH", "Elektrotechnika Jihlava", "VoltParts a.s.", "Jihlava",
          "Romana Havelky 4", 90, (1, 2, 3, 4, 5), ("morning", "afternoon"), 255, 171, 0.5, "flat"),
    Plant("ECO-DOB", "Sklad e-commerce Dobrovíz", "ShopFlow CZ s.r.o.", "Dobrovíz",
          "Logistický park 1", 120, (1, 2, 3, 4, 5, 6, 7), ("morning", "afternoon", "night"), 246, 165, 0.7, "ecommerce"),
    Plant("FOD-BRA", "Potravinářský závod Brandýs", "FreshFood a.s.", "Brandýs nad Labem",
          "Průmyslová zóna 8", 70, (1, 2, 3, 4, 5, 6), ("morning", "afternoon"), 231, 160, 0.45, "food"),
    Plant("PLA-PLZ", "Plastové díly Plzeň", "PolyForm s.r.o.", "Plzeň",
          "Borská pole 21", 60, (1, 2, 3, 4, 5), ("morning", "afternoon", "night"), 251, 170, 0.6, "flat"),
)

PLANT_CODES = [p.code for p in PLANTS]

SHIFT_STARTS = {"morning": time(6), "afternoon": time(14), "night": time(22)}
SHIFT_MINUTES = 480
BREAK_MINUTES = 30
SHIFT_NAMES = {"morning": "Ranní směna", "afternoon": "Odpolední směna", "night": "Noční směna"}

FIRST_NAMES = ["Anna", "Petr", "Eva", "Marek", "Olena", "Iryna", "Tomáš", "Jana", "Pavel", "Lucie",
               "Mykola", "Oksana", "Jan", "Kateřina", "Serhii", "Natalia", "Martin", "Tetiana",
               "Andrii", "Veronika", "Jakub", "Yulia", "Vasyl", "Petra", "Dmytro", "Marta"]
LAST_NAMES = ["Novák", "Dvořák", "Svoboda", "Kovalenko", "Shevchenko", "Bondarenko", "Černý",
              "Procházka", "Melnyk", "Tkachenko", "Horák", "Kučera", "Koval", "Marek", "Pospíšil",
              "Boiko", "Kravchenko", "Veselý", "Oliinyk", "Holub", "Moroz", "Král", "Lysenko"]


def demand(plant: Plant, day: date) -> float:
    """How many people the client wants compared with the base headcount."""
    years = (day - START).days / 365.25
    trend = 1 + 0.06 * years
    doy = day.timetuple().tm_yday
    if plant.season == "ecommerce":
        # Black Friday and Christmas
        peak = 0.38 * math.exp(-((doy - 335) / 22) ** 2) + 0.12 * math.exp(-((doy - 20) / 12) ** 2)
        return trend * (0.95 + peak)
    if plant.season == "automotive":
        # The summer shutdown is handled separately; spring and autumn are busy.
        return trend * (1 + 0.07 * math.sin(2 * math.pi * (doy - 60) / 365.25))
    if plant.season == "logistics":
        return trend * (1 + 0.1 * math.sin(2 * math.pi * (doy - 250) / 182.6))
    if plant.season == "food":
        # Summer and Christmas seasons
        return trend * (1 + 0.12 * math.exp(-((doy - 200) / 40) ** 2) + 0.15 * math.exp(-((doy - 350) / 15) ** 2))
    return trend


def flu(day: date) -> float:
    """Seasonal strength of illness, 0..1, highest in January and February."""
    doy = day.timetuple().tm_yday
    return max(math.exp(-((doy - 35) / 30) ** 2), math.exp(-((doy - 400) / 30) ** 2))


def summer_shutdown(plant: Plant, day: date) -> bool:
    """The automotive plant stops for two weeks every summer."""
    if plant.season != "automotive":
        return False
    first = date(day.year, 7, 22)
    return first <= day < first + timedelta(days=14)


@dataclass
class Employee:
    id: int
    personal_no: str
    full_name: str
    plant: Plant
    hired_on: date
    rate: float
    is_home: bool
    rotation_offset: int
    uses_transport: bool
    left_on: date | None = None
    rates: list[tuple[date, float]] = field(default_factory=list)
    vacations: set[date] = field(default_factory=set)


@dataclass
class Incident:
    plant: str
    day: date
    kind: str
    kpi: str
    strength: float


class Simulation:
    def __init__(self, end: date, seed: int) -> None:
        self.end = end
        self.horizon_end = end + timedelta(days=FUTURE_DAYS)
        self.rng = np.random.default_rng(seed)
        self.cz_holidays = holidays.country_holidays("CZ", years=range(START.year, self.horizon_end.year + 1))
        self.employees: list[Employee] = []
        self.next_id = 1

    # ------------------------------------------------------------ people
    def hire(self, plant: Plant, day: date) -> Employee:
        rng = self.rng
        employee = Employee(
            id=self.next_id,
            personal_no=f"BC{self.next_id:05d}",
            full_name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
            plant=plant,
            hired_on=day,
            rate=round((plant.worker_rate + rng.normal(0, 7)) / 5) * 5,
            is_home=bool(rng.random() < 0.55),
            rotation_offset=int(rng.integers(0, len(plant.shifts))),
            uses_transport=bool(rng.random() < plant.transport_share),
        )
        employee.rates.append((day, employee.rate))
        self.next_id += 1
        self.employees.append(employee)
        return employee

    def staff(self) -> None:
        """Hires, raises and departures month by month."""
        rng = self.rng
        month = START
        while month <= self.horizon_end:
            for plant in PLANTS:
                active = [e for e in self.employees if e.plant is plant and e.left_on is None]
                # Departures: higher in summer and after New Year.
                attrition = 0.03 + (0.015 if month.month in (1, 7, 8) else 0)
                for employee in active:
                    if month > START and rng.random() < attrition:
                        employee.left_on = month + timedelta(days=int(rng.integers(0, 27)))
                active = [e for e in active if e.left_on is None]
                target = round(plant.headcount * demand(plant, month + timedelta(days=14)))
                for _ in range(max(0, target - len(active))):
                    start = month if month == START else month + timedelta(days=int(rng.integers(0, 20)))
                    self.hire(plant, start)
            # Yearly raise of 4 % in January.
            if month.month == 1 and month > START:
                for employee in self.employees:
                    if employee.left_on is None and employee.hired_on < month:
                        employee.rate = round(employee.rate * 1.04 / 5) * 5
                        employee.rates.append((month, employee.rate))
            month = (month.replace(day=1) + timedelta(days=32)).replace(day=1)

    def plan_vacations(self) -> None:
        rng = self.rng
        for employee in self.employees:
            for year in range(START.year, self.horizon_end.year + 1):
                if rng.random() < 0.55:  # two summer weeks
                    first = date(year, 7, 1) + timedelta(days=int(rng.integers(0, 50)))
                    employee.vacations.update(first + timedelta(days=k) for k in range(14))
                if rng.random() < 0.4:  # a week around Christmas
                    first = date(year, 12, 21) + timedelta(days=int(rng.integers(0, 6)))
                    employee.vacations.update(first + timedelta(days=k) for k in range(7))

    def employed(self, employee: Employee, day: date) -> bool:
        return employee.hired_on <= day and (employee.left_on is None or day < employee.left_on)

    def shift_of(self, employee: Employee, day: date) -> str | None:
        plant = employee.plant
        if day.isoweekday() not in plant.workdays or summer_shutdown(plant, day):
            return None
        if day in self.cz_holidays and plant.season not in ("ecommerce", "food"):
            return None
        week = (day - START).days // 7
        return plant.shifts[(week + employee.rotation_offset) % len(plant.shifts)]

    # --------------------------------------------------------- incidents
    def incidents(self) -> list[Incident]:
        """Labelled events the anomaly detection should find."""
        rng = self.rng
        kinds = (
            ("no_show_spike", "no_show_rate"),
            ("overtime_spike", "overtime_share"),
            ("transport_delay", "avg_delay_min"),
            ("clock_outage", "unexplained_absence_rate"),
        )
        found: list[Incident] = []
        days = (self.end - START).days
        for _ in range(48):
            plant = PLANTS[int(rng.integers(0, len(PLANTS)))]
            kind, kpi = kinds[int(rng.integers(0, len(kinds)))]
            while True:
                day = START + timedelta(days=int(rng.integers(60, days)))
                if self.shift_of_plant(plant, day):
                    break
            found.append(Incident(plant.code, day, kind, kpi, float(rng.uniform(0.3, 1.0))))
        return found

    def shift_of_plant(self, plant: Plant, day: date) -> bool:
        if day.isoweekday() not in plant.workdays or summer_shutdown(plant, day):
            return False
        return not (day in self.cz_holidays and plant.season not in ("ecommerce", "food"))


def at(day: date, clock: time, minutes: float = 0) -> datetime:
    return datetime.combine(day, clock, PRAGUE) + timedelta(minutes=minutes)


def simulate(end: date, seed: int) -> dict[str, pd.DataFrame | list]:
    sim = Simulation(end, seed)
    rng = sim.rng
    sim.staff()
    sim.plan_vacations()
    incidents = sim.incidents()
    incident_at = {(i.plant, i.day): i for i in incidents}

    workplaces = pd.DataFrame([
        {"id": n + 1, "code": p.code, "name": p.name, "client_name": p.client, "city": p.city,
         "address": p.address, "is_active": True}
        for n, p in enumerate(PLANTS)
    ])
    plant_id = {p.code: n + 1 for n, p in enumerate(PLANTS)}
    shift_rows, shift_id = [], {}
    for p in PLANTS:
        for kind in p.shifts:
            shift_id[(p.code, kind)] = len(shift_rows) + 1
            shift_rows.append({
                "id": len(shift_rows) + 1, "workplace_id": plant_id[p.code], "code": kind,
                "name": SHIFT_NAMES[kind], "start_time": SHIFT_STARTS[kind].isoformat(),
                "duration_minutes": SHIFT_MINUTES, "break_minutes": BREAK_MINUTES,
                "agency_hourly_rate_czk": p.agency_rate * (1.1 if kind == "night" else 1.0),
            })

    planned, logs, absences, overtime, requests, adjustments = [], [], [], [], [], []
    sick_until: dict[int, date] = {}
    log_id = 1
    day = START
    riders: dict[tuple[str, date, str], tuple[int, int]] = {}
    while day <= sim.horizon_end:
        past = day <= end
        winter = flu(day)
        for e in sim.employees:
            if not sim.employed(e, day):
                continue
            kind = sim.shift_of(e, day)
            if kind is None:
                continue
            if day in e.vacations:
                if past and (day - timedelta(days=1)) not in e.vacations:
                    last = day
                    while last + timedelta(days=1) in e.vacations:
                        last += timedelta(days=1)
                    absences.append({"employee_id": e.id, "date_from": day, "date_to": last, "reason": "vacation"})
                continue
            sid = shift_id[(e.plant.code, kind)]
            planned.append({"employee_id": e.id, "work_date": day, "shift_type_id": sid})
            if not past:
                continue
            incident = incident_at.get((e.plant.code, day))
            key = (e.plant.code, day, kind)
            planned_riders, came_riders = riders.get(key, (0, 0))
            if e.uses_transport:
                planned_riders += 1
            # Illness comes in episodes of a few days.
            if e.id in sick_until and day <= sick_until[e.id]:
                riders[key] = (planned_riders, came_riders)
                continue
            if rng.random() < 0.0045 * (1 + 2.2 * winter):
                length = int(rng.integers(2, 10))
                sick_until[e.id] = day + timedelta(days=length)
                absences.append({"employee_id": e.id, "date_from": day, "date_to": day + timedelta(days=length),
                                 "reason": "sick_leave"})
                requests.append({"employee_id": e.id, "kind": "sick_leave", "amount_czk": None,
                                 "created_at": at(day, time(7), rng.uniform(0, 300)),
                                 "decision_hours": float(rng.gamma(2, 6)), "approved": True})
                riders[key] = (planned_riders, came_riders)
                continue
            p_no_show = 0.009 * (1.45 if day.isoweekday() in (1, 5) else 1.0)
            if incident and incident.kind == "no_show_spike":
                p_no_show += 0.03 + 0.12 * incident.strength
            if rng.random() < p_no_show:
                absences.append({"employee_id": e.id, "date_from": day, "date_to": day, "reason": "no_show"})
                riders[key] = (planned_riders, came_riders)
                continue
            if e.uses_transport:
                came_riders += 1
            riders[key] = (planned_riders, came_riders)
            # The time clock failed: the day is lost from the records.
            if incident and incident.kind == "clock_outage" and rng.random() < 0.04 + 0.2 * incident.strength:
                continue
            start = at(day, SHIFT_STARTS[kind], rng.normal(-4, 3))
            extra = 0
            busy = demand(e.plant, day) - 1
            p_overtime = min(0.4, max(0.02, 0.04 + 0.35 * busy))
            if incident and incident.kind == "overtime_spike":
                p_overtime += 0.12 + 0.25 * incident.strength
            if rng.random() < p_overtime:
                extra = int(rng.choice([60, 120, 180, 240] if incident and incident.kind == "overtime_spike" else [60, 60, 120, 180]))
                overtime.append({"employee_id": e.id, "work_date": day, "minutes": extra, "cancelled": False})
            end_at = start + timedelta(minutes=SHIFT_MINUTES + extra + rng.normal(3, 3))
            status = "approved"
            missing_out = rng.random() < 0.002  # forgot to clock out
            logs.append({"id": log_id, "employee_id": e.id, "work_date": day, "shift_type_id": sid,
                         "clock_in": start, "clock_out": None if missing_out else end_at,
                         "status": "open" if missing_out else status})
            log_id += 1
            if rng.random() < 0.003:  # the terminal sent the record twice
                logs.append({**logs[-1], "id": log_id})
                log_id += 1
        day += timedelta(days=1)

    employees = pd.DataFrame([
        {"id": e.id, "personal_no": e.personal_no, "full_name": e.full_name, "role": "worker",
         "workplace_id": plant_id[e.plant.code], "hourly_rate_czk": e.rate, "is_home": e.is_home,
         "housing_fee_czk": 4800 if e.is_home else 0, "uses_transport": e.uses_transport,
         "hired_on": e.hired_on, "left_on": e.left_on}
        for e in sim.employees
    ])
    rate_history = pd.DataFrame([
        {"employee_id": e.id, "valid_from": since, "hourly_rate_czk": rate}
        for e in sim.employees for since, rate in e.rates
    ])

    # Advances, bonuses and fines month by month.
    month = START
    while month <= end:
        month_end = (month + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        for e in sim.employees:
            if not sim.employed(e, month_end) or e.hired_on > month_end - timedelta(days=10):
                continue
            if rng.random() < 0.18:
                asked = at(month + timedelta(days=int(rng.integers(5, 25))), time(9), rng.uniform(0, 600))
                if asked.date() <= end:
                    requests.append({"employee_id": e.id, "kind": "advance",
                                     "amount_czk": float(rng.choice([2000, 3000, 5000, 7000, 10000])),
                                     "created_at": asked, "decision_hours": float(rng.gamma(2, 10)),
                                     "approved": bool(rng.random() < 0.86)})
            if rng.random() < 0.035:
                adjustments.append({"employee_id": e.id, "salary_month": month, "kind": "addition",
                                    "amount_czk": float(rng.choice([500, 1000, 1500, 3000])),
                                    "comment": "Bonus for the month",
                                    "created_at": at(month_end, time(12))})
            if rng.random() < 0.012:
                adjustments.append({"employee_id": e.id, "salary_month": month, "kind": "deduction",
                                    "amount_czk": float(rng.choice([300, 500, 1000, 2000])),
                                    "comment": "Damaged equipment",
                                    "created_at": at(month_end, time(12))})
            if rng.random() < 0.05:
                requests.append({"employee_id": e.id, "kind": "extra_shift", "amount_czk": None,
                                 "created_at": at(month + timedelta(days=int(rng.integers(0, 25))), time(10)),
                                 "decision_hours": float(rng.gamma(2, 12)), "approved": bool(rng.random() < 0.7)})
        month = month_end + timedelta(days=1)

    trips = build_trips(sim, riders, incident_at, shift_id, rng)
    return {
        "workplaces": workplaces, "shift_types": pd.DataFrame(shift_rows), "employees": employees,
        "rate_history": rate_history, "planned": pd.DataFrame(planned), "logs": pd.DataFrame(logs),
        "absences": pd.DataFrame(absences), "overtime": pd.DataFrame(overtime),
        "requests": pd.DataFrame(requests), "adjustments": pd.DataFrame(adjustments),
        "trips": trips, "incidents": incidents,
    }


def build_trips(sim, riders, incident_at, shift_id, rng) -> list[dict]:
    """A bus to every shift and back, for the workers who ride."""
    trips = []
    trip_no = 1
    for (plant_code, day, kind), (planned_riders, came) in sorted(riders.items()):
        if planned_riders == 0:
            continue
        incident = incident_at.get((plant_code, day))
        buses = max(1, math.ceil(planned_riders / 46))
        for direction in ("inbound", "outbound"):
            if direction == "inbound":
                departure = at(day, SHIFT_STARTS[kind], -55)
            else:
                departure = at(day, SHIFT_STARTS[kind], SHIFT_MINUTES + 10)
            for bus in range(buses):
                share_planned = planned_riders // buses + (1 if bus < planned_riders % buses else 0)
                share_came = came // buses + (1 if bus < came % buses else 0)
                cancelled = rng.random() < 0.004
                delay = max(0.0, rng.gamma(1.3, 2.6) - 1.5) * (1 + 0.6 * flu(day))
                if incident and incident.kind == "transport_delay":
                    delay += 6 + 18 * incident.strength
                trips.append({
                    "trip_id": f"T{trip_no:06d}",
                    "service_date": day.isoformat(),
                    "workplace_code": plant_code,
                    "shift": kind,
                    "direction": direction,
                    "vehicle_code": f"BUS-{(PLANT_CODES.index(plant_code) * 3 + bus) % 18 + 1:02d}",
                    "driver_code": f"DRV-{int(rng.integers(1, 15)):02d}",
                    "capacity": 50,
                    "departure_planned": departure.isoformat(),
                    "departure_actual": None if cancelled else (departure + timedelta(minutes=round(delay, 1))).isoformat(),
                    "delay_min": None if cancelled else round(delay, 1),
                    "status": "cancelled" if cancelled else "completed",
                    "passengers_planned": share_planned,
                    "passengers_boarded": 0 if cancelled else share_came,
                })
                trip_no += 1
    return trips


OLTP_DDL = """
DROP TABLE IF EXISTS work_logs, planned_shifts, absences, overtime, requests,
  salary_adjustments, employee_rate_history, employees, shift_types, workplaces CASCADE;
CREATE TABLE workplaces (
  id integer PRIMARY KEY, code text UNIQUE NOT NULL, name text NOT NULL, client_name text NOT NULL,
  city text, address text, is_active boolean NOT NULL, updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE shift_types (
  id integer PRIMARY KEY, workplace_id integer NOT NULL REFERENCES workplaces, code text NOT NULL,
  name text NOT NULL, start_time time NOT NULL, duration_minutes integer NOT NULL, break_minutes integer NOT NULL,
  agency_hourly_rate_czk numeric(10,2) NOT NULL, updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE employees (
  id integer PRIMARY KEY, personal_no text UNIQUE NOT NULL, full_name text NOT NULL, role text NOT NULL,
  workplace_id integer REFERENCES workplaces, hourly_rate_czk numeric(10,2) NOT NULL, is_home boolean NOT NULL,
  housing_fee_czk numeric(10,2) NOT NULL, uses_transport boolean NOT NULL, hired_on date NOT NULL, left_on date,
  updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE employee_rate_history (
  employee_id integer NOT NULL REFERENCES employees, valid_from date NOT NULL, hourly_rate_czk numeric(10,2) NOT NULL,
  PRIMARY KEY (employee_id, valid_from));
CREATE TABLE planned_shifts (
  id bigserial PRIMARY KEY, employee_id integer NOT NULL REFERENCES employees, work_date date NOT NULL,
  shift_type_id integer NOT NULL REFERENCES shift_types, updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE work_logs (
  id bigint PRIMARY KEY, employee_id integer NOT NULL REFERENCES employees, work_date date NOT NULL,
  shift_type_id integer NOT NULL REFERENCES shift_types, clock_in timestamptz NOT NULL, clock_out timestamptz,
  status text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE absences (
  id bigserial PRIMARY KEY, employee_id integer NOT NULL REFERENCES employees, date_from date NOT NULL,
  date_to date NOT NULL, reason text NOT NULL, updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE overtime (
  id bigserial PRIMARY KEY, employee_id integer NOT NULL REFERENCES employees, work_date date NOT NULL,
  minutes integer NOT NULL, cancelled boolean NOT NULL, updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE requests (
  id bigserial PRIMARY KEY, employee_id integer NOT NULL REFERENCES employees, kind text NOT NULL,
  amount_czk numeric(10,2), status text NOT NULL, created_at timestamptz NOT NULL, reviewed_at timestamptz,
  updated_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE salary_adjustments (
  id bigserial PRIMARY KEY, employee_id integer NOT NULL REFERENCES employees, salary_month date NOT NULL,
  kind text NOT NULL, amount_czk numeric(10,2) NOT NULL, comment text, created_at timestamptz NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX ON planned_shifts (work_date);
CREATE INDEX ON work_logs (updated_at);
"""


def write_oltp(world: dict) -> None:
    engine = source_engine()
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cursor:
            cursor.execute(OLTP_DDL)
        raw.commit()
    finally:
        raw.close()
    end = settings().sim_end_date
    stamp = datetime.combine(end, time(23, 30), PRAGUE)
    copy_frame(engine, world["workplaces"].assign(updated_at=stamp), "workplaces")
    copy_frame(engine, world["shift_types"].assign(updated_at=stamp), "shift_types")
    copy_frame(engine, world["employees"].assign(updated_at=stamp), "employees")
    copy_frame(engine, world["rate_history"], "employee_rate_history")
    planned = world["planned"]
    copy_frame(engine, planned.assign(updated_at=stamp), "planned_shifts")
    logs = world["logs"]
    copy_frame(engine, logs.assign(updated_at=logs["clock_in"]), "work_logs")
    copy_frame(engine, world["absences"].assign(updated_at=stamp), "absences")
    copy_frame(engine, world["overtime"].assign(updated_at=stamp), "overtime")
    requests = world["requests"].copy()
    requests["reviewed_at"] = requests["created_at"] + pd.to_timedelta(requests.pop("decision_hours"), unit="h")
    requests["status"] = np.where(requests.pop("approved"), "approved", "rejected")
    # Requests still waiting at the end of the history.
    waiting = requests["reviewed_at"] > stamp
    requests.loc[waiting, "status"] = "pending"
    requests.loc[waiting, "reviewed_at"] = pd.NaT
    copy_frame(engine, requests.assign(updated_at=stamp), "requests")
    copy_frame(engine, world["adjustments"].assign(updated_at=stamp), "salary_adjustments")


def write_invoices(world: dict) -> None:
    """Monthly CSV exports of the accounting system, as clerks make them."""
    rng = np.random.default_rng(settings().sim_seed + 1)
    folder = settings().raw_dir / "accounting"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.csv"):
        old.unlink()
    logs = world["logs"].drop_duplicates(subset=["employee_id", "work_date", "shift_type_id"])
    shifts = world["shift_types"].set_index("id")
    workplaces = world["workplaces"].set_index("id")
    logs = logs.join(shifts[["workplace_id", "agency_hourly_rate_czk"]], on="shift_type_id")
    overtime = world["overtime"].groupby(["employee_id", "work_date"])["minutes"].sum()
    logs = logs.join(overtime, on=["employee_id", "work_date"])
    logs["hours"] = (SHIFT_MINUTES - BREAK_MINUTES + logs["minutes"].fillna(0)) / 60
    logs["month"] = pd.to_datetime(logs["work_date"]).dt.to_period("M")
    end = settings().sim_end_date
    number = 1
    for month, part in logs.groupby("month"):
        if month.end_time.date() > end:
            continue  # the current month is invoiced after it ends
        rows = []
        for workplace_id, lines in part.groupby("workplace_id"):
            hours = round(lines["hours"].sum() * (1 + rng.normal(0, 0.003)), 1)
            amount = round((lines["hours"] * lines["agency_hourly_rate_czk"]).sum() * hours / lines["hours"].sum(), 2)
            issued = month.end_time.date() + timedelta(days=int(rng.integers(2, 9)))
            # Clerks write dates two ways.
            issued_text = issued.strftime("%d.%m.%Y") if rng.random() < 0.3 else issued.isoformat()
            rows.append({"cislo_faktury": f"FV{month.year}{number:05d}", "klient": workplaces.loc[workplace_id, "code"],
                         "obdobi": str(month), "hodiny": f"{hours:.1f}".replace(".", ","),
                         "castka_czk": f"{amount:.2f}".replace(".", ","), "vystaveno": issued_text})
            number += 1
            if rng.random() < 0.06:  # a credit note for a disputed shift
                credit = round(float(rng.uniform(2000, 15000)), 2)
                rows.append({"cislo_faktury": f"DB{month.year}{number:05d}", "klient": workplaces.loc[workplace_id, "code"],
                             "obdobi": str(month), "hodiny": "0,0", "castka_czk": f"{-credit:.2f}".replace(".", ","),
                             "vystaveno": (issued + timedelta(days=10)).isoformat()})
                number += 1
        frame = pd.DataFrame(rows)
        if rng.random() < 0.15:  # exported twice
            frame = pd.concat([frame, frame.iloc[[0]]])
        if rng.random() < 0.08:  # a line saved without the amount
            frame.iloc[-1, frame.columns.get_loc("castka_czk")] = ""
        frame.to_csv(folder / f"invoices_{month}.csv", sep=";", index=False, encoding="utf-8")


def write_trips(world: dict) -> None:
    folder = settings().raw_dir / "transport"
    folder.mkdir(parents=True, exist_ok=True)
    trips = world["trips"]
    # The service sends a trip twice now and then (paging overlap).
    rng = np.random.default_rng(settings().sim_seed + 2)
    for index in rng.choice(len(trips), size=max(1, len(trips) // 500), replace=False):
        trips.append(dict(trips[int(index)]))
    trips.sort(key=lambda trip: (trip["service_date"], trip["trip_id"]))
    (folder / "trips.json").write_text(json.dumps(trips, ensure_ascii=False), encoding="utf-8")


def write_ground_truth(world: dict) -> None:
    folder = settings().ground_truth_dir
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"workplace_code": i.plant, "date": i.day, "kind": i.kind, "kpi": i.kpi,
                   "strength": round(i.strength, 2)} for i in world["incidents"]]) \
        .sort_values("date").to_csv(folder / "incidents.csv", index=False)


def main() -> None:
    config = settings()
    world = simulate(config.sim_end_date, config.sim_seed)
    write_oltp(world)
    write_invoices(world)
    write_trips(world)
    write_ground_truth(world)
    print(
        f"employees {len(world['employees'])}, planned shifts {len(world['planned'])}, "
        f"work logs {len(world['logs'])}, absences {len(world['absences'])}, "
        f"trips {len(world['trips'])}, incidents {len(world['incidents'])}"
    )


if __name__ == "__main__":
    main()
