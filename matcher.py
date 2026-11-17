"""Matching engine: clean messy data, apply hard filters, score, explain, diagnose shortages.

Every score component produces the phrase used in the reason, so score and reason never disagree.
All tunables live in DEFAULT_WEIGHTS / DEFAULT_RULES so a new request is usually a one-line change.
"""
import difflib
import json
import math
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# Public, anonymised data ships in ./data. Point MATCHER_DATA_DIR at a private folder for real data.
DATA_DIR = Path(os.environ.get("MATCHER_DATA_DIR") or Path(__file__).parent / "data")

# Final scoring model (v2). Relative weights; the score is normalised by their sum, so they need not
# add up to 100 when Meena edits them.
DEFAULT_WEIGHTS = {"experience": 40, "distance": 25, "pay": 20, "joining": 10, "completeness": 5}
WEIGHT_LABELS = {"experience": "Relevant experience", "distance": "Geographical proximity",
                 "pay": "Salary compatibility", "joining": "Joining readiness",
                 "completeness": "Data completeness"}
PROFILE_FIELDS = 5   # experience, pay, shifts, joining date, education

DEFAULT_RULES = {
    "max_km": 25.0,                 # beyond this commute -> ruled out
    "max_pay_over": 3000,           # expects up to this much more -> shown but penalised; beyond -> ruled out
    "max_days_late": 7,             # can join up to N days after start -> penalised; beyond -> ruled out
    "reach_truck_is_forklift": False,
    "iti_counts_as_12th": False,
    "show_not_reachable": False,
    "skilled_roles": ["forklift", "rider", "barista"],  # need relevant experience; others accept freshers
    "exp_cap_months": 24,           # experience score saturates at 24 relevant months
}

# ---------- normalisation tables ----------
EDU_ALIASES = {
    "below_10": "below_10", "sslc": "10th", "10th": "10th", "10th pass": "10th",
    "iti": "iti", "puc": "12th", "12th": "12th", "12th pass": "12th",
    "diploma": "diploma", "graduate": "graduate",
}
EDU_LEVEL = {"below_10": 0, "10th": 1, "iti": 1.5, "12th": 2, "diploma": 2.5, "graduate": 3}

ROLE_ALIASES = {
    "warehouse picker": "picker", "picker": "picker", "picker/packer": "picker_packer",
    "packer": "packer", "packing helper": "packer", "packaging staff": "packer",
    "warehouse associate": "warehouse_general", "godown helper": "warehouse_general",
    "delivery rider": "rider", "rider": "rider", "delivery partner": "rider",
    "delivery boy": "rider", "bike delivery executive": "rider",
    "security guard": "security", "security": "security", "guard": "security", "watchman": "security",
    "barista": "barista", "barista / counter": "barista", "coffee maker": "barista",
    "cafe staff": "cafe_general",
    "housekeeping": "housekeeping", "hk attendant": "housekeeping",
    "house keeping staff": "housekeeping", "cleaner": "cleaner",
    "forklift operator": "forklift", "reach truck operator": "reach_truck",
}
ROLE_FAMILY = {
    "picker": "warehouse", "packer": "warehouse", "picker_packer": "warehouse",
    "warehouse_general": "warehouse", "rider": "delivery", "security": "security",
    "barista": "cafe", "cafe_general": "cafe", "housekeeping": "housekeeping",
    "cleaner": "housekeeping", "forklift": "forklift", "reach_truck": "forklift",
}

# keys are compacted (lowercase letters only) after suffix stripping
LOC_ALIASES = {
    "yesvantpur": "Yeshwanthpur", "yeshwantpur": "Yeshwanthpur", "kormangala": "Koramangala",
    "marathalli": "Marathahalli", "btm": "BTM Layout", "krishnarajapuram": "KR Puram",
    "bommanhalli": "Bommanahalli",
}
_LOC_SUFFIX = re.compile(
    r"\b(\d+(st|nd|rd|th)\s+(block|stage|phase|cross|main)|sector\s*\d+|main road|bridge|industrial area)\b"
)


def parse_pay(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).lower().replace("rs.", "").replace("rs", "").replace("₹", "").replace(",", "").strip()
    mult = 1000 if s.endswith("k") else 1
    try:
        return int(round(float(s.rstrip("k").strip()) * mult))
    except ValueError:
        return None


def norm_phone(p):
    return re.sub(r"\D", "", str(p or ""))[-10:]


def norm_edu(v):
    return None if v is None else EDU_ALIASES.get(str(v).strip().lower())


def norm_role(title):
    return ROLE_ALIASES.get(re.sub(r"\s+", " ", str(title).strip().lower()))


def parse_date(v):
    return date.fromisoformat(v) if v else None


def haversine_km(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _compact(s):
    return re.sub(r"[^a-z]", "", s.lower())


class Localities:
    def __init__(self, rows):
        self.coords = {r["locality"]: (r["latitude"], r["longitude"]) for r in rows}
        self.index = {_compact(n): n for n in self.coords}

    def resolve(self, raw):
        """Return (canonical_name | None, how) with how in exact/cleaned/alias/fuzzy/None."""
        if not raw:
            return None, None
        key = _compact(_LOC_SUFFIX.sub(" ", raw.lower()))
        if key in self.index:
            return self.index[key], ("exact" if raw in self.coords else "cleaned")
        if key in LOC_ALIASES:
            return LOC_ALIASES[key], "alias"
        m = difflib.get_close_matches(key, list(self.index), n=1, cutoff=0.85)
        if m:
            return self.index[m[0]], "fuzzy"
        return None, None


# ---------- domain objects ----------
@dataclass
class Candidate:
    id: str
    name: str
    phone: str
    raw_locality: str
    locality: str | None
    loc_how: str | None
    roles: list            # [(title, canon_role|None, months)]
    pay: int | None
    shifts: set
    join: date | None
    edu: str | None
    status: str
    dup_ids: list = field(default_factory=list)

    @property
    def missing(self):
        return [k for k, v in (("experience", self.roles), ("pay", self.pay), ("shifts", self.shifts),
                               ("joining date", self.join), ("education", self.edu)) if v in (None, [], set())]

    @property
    def needs_profile(self):
        """More than half the profile unknown -> not ranked, goes to 'call to complete' list."""
        return len(self.missing) >= 3


@dataclass
class Demand:
    id: str
    employer: str
    role_title: str
    role: str | None
    raw_locality: str
    locality: str | None
    headcount: int
    pay: int
    shift: str
    start: date
    eff_start: date        # start, or today if start already passed
    start_passed: bool
    min_edu: str | None

    @property
    def label(self):
        return f"{self.id} · {self.employer} · {self.role_title} · {self.raw_locality} · {self.shift}"


@dataclass
class Match:
    candidate: Candidate
    eligible: bool
    blockers: list         # [(code, message)]
    comps: dict            # component -> 0..1
    score: float
    reason: str
    flags: list
    distance_km: float | None
    match_type: str = "none"      # exact / related / none
    relevant_months: float = 0.0  # exact + 0.5 x related, before the cap

    def points(self, weights):
        """Points each factor contributes; they add up to the score."""
        wsum = sum(max(0, weights.get(k, 0)) for k in self.comps)
        if not wsum:
            return {k: 0.0 for k in self.comps}
        return {k: 100 * max(0, weights.get(k, 0)) * v / wsum for k, v in self.comps.items()}


def _fmt_inr(n):
    return f"₹{n:,}"


class Engine:
    def __init__(self, data_dir=DATA_DIR, today=None):
        data_dir = Path(data_dir)
        self.today = today or date.today()
        self.locs = Localities(json.loads((data_dir / "localities.json").read_text()))
        self.issues = []   # data-quality log shown to Meena
        self.candidates = self._load_candidates(json.loads((data_dir / "candidates.json").read_text()))
        self.demands = {d.id: d for d in self._load_demands(json.loads((data_dir / "demands.json").read_text()))}

    # ----- loading -----
    def _load_candidates(self, rows):
        parsed = []
        for r in rows:
            loc, how = self.locs.resolve(r.get("home_locality"))
            if how in ("cleaned", "alias", "fuzzy"):
                self.issues.append(("locality", r["id"], f"'{r['home_locality']}' → {loc} ({how})"))
            elif loc is None:
                self.issues.append(("locality", r["id"], f"'{r.get('home_locality')}' not recognised"))
            roles = []
            for pr in r.get("past_roles") or []:
                canon = norm_role(pr["title"])
                if canon is None:
                    self.issues.append(("role", r["id"], f"unknown title '{pr['title']}'"))
                roles.append((pr["title"], canon, int(pr.get("months") or 0)))
            pay = parse_pay(r.get("expected_monthly_pay"))
            if r.get("expected_monthly_pay") is not None and not isinstance(r["expected_monthly_pay"], int):
                self.issues.append(("pay", r["id"], f"'{r['expected_monthly_pay']}' → {pay}"))
            edu = norm_edu(r.get("education"))
            if r.get("education") is not None and edu is None:
                self.issues.append(("education", r["id"], f"unknown '{r['education']}'"))
            parsed.append(Candidate(
                id=r["id"], name=" ".join(str(r["name"]).split()).title(), phone=norm_phone(r.get("phone")),
                raw_locality=r.get("home_locality") or "", locality=loc, loc_how=how, roles=roles, pay=pay,
                shifts=set(r.get("shifts_available") or []), join=parse_date(r.get("earliest_joining_date")),
                edu=edu, status=r.get("status") or "unknown"))
        # dedupe by normalised phone (NOT name: several different people share names)
        by_phone = defaultdict(list)
        for c in parsed:
            by_phone[c.phone or c.id].append(c)
        out = []
        for group in by_phone.values():
            group.sort(key=lambda c: (len(c.missing), c.id))
            keep = group[0]
            keep.dup_ids = [c.id for c in group[1:]]
            if keep.dup_ids:
                self.issues.append(("duplicate", keep.id, f"same phone as {', '.join(keep.dup_ids)} — merged"))
            out.append(keep)
        return {c.id: c for c in sorted(out, key=lambda c: c.id)}

    def _load_demands(self, rows):
        for r in rows:
            loc, how = self.locs.resolve(r["site_locality"])
            if how != "exact":
                self.issues.append(("locality", r["id"], f"site '{r['site_locality']}' → {loc} ({how})"))
            start = parse_date(r["start_date"])
            passed = start < self.today
            if passed:
                self.issues.append(("demand", r["id"], f"start date {start} already passed — treated as ASAP"))
            if int(r["headcount"]) <= 0:
                self.issues.append(("demand", r["id"], "headcount 0 — treated as closed"))
            yield Demand(
                id=r["id"], employer=r["employer"], role_title=r["role"], role=norm_role(r["role"]),
                raw_locality=r["site_locality"], locality=loc, headcount=int(r["headcount"]),
                pay=parse_pay(r["monthly_pay"]), shift=r["shift"], start=start,
                eff_start=max(start, self.today), start_passed=passed, min_edu=norm_edu(r.get("min_education")))

    # ----- pool -----
    def pool(self, rules):
        """Split deduped candidates into (rankable, needs_profile, excluded_by_status)."""
        ok_status = {"available"} | ({"not_reachable"} if rules["show_not_reachable"] else set())
        rankable, incomplete, excluded = [], [], Counter()
        for c in self.candidates.values():
            if c.status not in ok_status:
                excluded[c.status] += 1
            elif c.needs_profile:
                incomplete.append(c)
            else:
                rankable.append(c)
        return rankable, incomplete, excluded

    # ----- scoring -----
    @staticmethod
    def _is_exact(cr, dr, rules):
        if cr == dr:
            return True
        pp = {"picker", "packer", "picker_packer"}
        if "picker_packer" in (cr, dr) and {cr, dr} <= pp:
            return True
        return rules["reach_truck_is_forklift"] and {cr, dr} == {"forklift", "reach_truck"}

    def _edu_level(self, e, rules):
        if e is None:
            return None
        return 2 if (e == "iti" and rules["iti_counts_as_12th"]) else EDU_LEVEL[e]

    def distance(self, c, d):
        if not (c.locality and d.locality):
            return None
        return haversine_km(self.locs.coords[c.locality], self.locs.coords[d.locality])

    def evaluate(self, c, d, weights, rules):
        blockers, flags, comps, phrases = [], [], {}, []
        # comps is filled per factor below; reordered to DEFAULT_WEIGHTS order at the end

        # shift (hard)
        if c.shifts and d.shift not in c.shifts:
            blockers.append(("shift", f"not available for {d.shift} shift (has {', '.join(sorted(c.shifts))})"))
        elif not c.shifts:
            flags.append("shift availability unknown")

        # education (hard)
        req, have = self._edu_level(d.min_edu, rules), self._edu_level(c.edu, rules)
        if req is not None and have is not None and have < req:
            blockers.append(("education", f"education {c.edu} below required {d.min_edu}"))
        elif req is not None and have is None:
            flags.append("education unverified")

        # relevant experience (40): exact months count fully, same-family months count half,
        # capped at exp_cap_months (24). Role fit is not scored separately any more.
        exact = [(t, m) for t, cr, m in c.roles if cr and self._is_exact(cr, d.role, rules)]
        related = [(t, m) for t, cr, m in c.roles
                   if cr and not self._is_exact(cr, d.role, rules) and ROLE_FAMILY.get(cr) == ROLE_FAMILY.get(d.role)]
        if exact:
            match_type = "exact"
            top = sorted(exact, key=lambda x: -x[1])
            phrases.append(", ".join(f"{m} months as {t}" for t, m in top[:2]))
        elif related:
            match_type = "related"
            top = sorted(related, key=lambda x: -x[1])
            phrases.append(", ".join(f"{m} months as {t} (related)" for t, m in top[:2]))
        else:
            match_type = "none"
            if d.role in rules["skilled_roles"]:
                blockers.append(("role", f"no {d.role_title} or related experience (skilled role)"))
            phrases.append(f"no {d.role_title.lower()} experience")
        months = sum(m for _, m in exact) + 0.5 * sum(m for _, m in related)
        cap = rules["exp_cap_months"]
        comps["experience"] = min(1.0, months / cap) if cap else 0.0
        if not c.roles:
            flags.append("work history unknown")

        # distance
        km = self.distance(c, d)
        if km is None:
            comps["distance"] = 0.5
            phrases.insert(0, f"location unclear ('{c.raw_locality}')")
        else:
            if km > rules["max_km"]:
                blockers.append(("distance", f"{km:.1f} km away (max {rules['max_km']:.0f})"))
            comps["distance"] = max(0.0, 1 - km / rules["max_km"]) if rules["max_km"] else 0.0
            phrases.insert(0, "same locality" if km < 0.5 else f"{km:.1f} km away")
            if c.loc_how == "fuzzy":
                flags.append(f"location matched approximately ('{c.raw_locality}' → {c.locality})")

        # pay
        if c.pay is None:
            comps["pay"] = 0.5
            phrases.append("pay expectation unknown — confirm")
        else:
            gap = c.pay - d.pay
            if gap > rules["max_pay_over"]:
                blockers.append(("pay", f"expects {_fmt_inr(gap)} more than the pay"))
            if gap <= 0:
                comps["pay"] = 1.0
                phrases.append("expects exactly the pay" if gap == 0 else f"expects {_fmt_inr(-gap)} less than the pay")
            else:
                comps["pay"] = max(0.0, 1 - gap / rules["max_pay_over"]) if rules["max_pay_over"] else 0.0
                phrases.append(f"expects {_fmt_inr(gap)} more than the pay")
                flags.append(f"pay gap {_fmt_inr(gap)} — negotiate")

        # joining date
        if c.join is None:
            comps["joining"] = 0.5
            phrases.append("joining date unknown")
        else:
            late = (c.join - d.eff_start).days
            if late > rules["max_days_late"]:
                blockers.append(("joining", f"can join only {late} days after start"))
            if late <= 0:
                comps["joining"] = 1.0
                phrases.append("can join by the start date")
            else:
                comps["joining"] = max(0.0, 1 - late / (rules["max_days_late"] + 1))
                phrases.append(f"can join {late} day{'s' if late != 1 else ''} after start")

        if c.status == "not_reachable":
            flags.append("was not reachable — call first")
        if c.dup_ids:
            flags.append(f"duplicate profile merged ({', '.join(c.dup_ids)})")
        # data completeness (5): share of the five profile fields we actually know
        comps["completeness"] = 1 - len(c.missing) / PROFILE_FIELDS
        if c.missing:
            flags.append("missing: " + ", ".join(c.missing))
            phrases.append(f"profile {PROFILE_FIELDS - len(c.missing)}/{PROFILE_FIELDS} complete")

        comps = {k: comps[k] for k in DEFAULT_WEIGHTS}
        wsum = sum(max(0, weights.get(k, 0)) for k in comps)
        score = round(100 * sum(max(0, weights.get(k, 0)) * v for k, v in comps.items()) / wsum, 1) if wsum else 0.0
        return Match(c, not blockers, blockers, comps, score, ", ".join(phrases), flags, km, match_type, months)

    def shortlist(self, demand_id, weights, rules, assigned=None):
        """Return (eligible ranked, ineligible). Candidates assigned to *other* demands are skipped."""
        assigned = assigned or {}
        d = self.demands[demand_id]
        rankable, _, _ = self.pool(rules)
        results = [self.evaluate(c, d, weights, rules) for c in rankable
                   if assigned.get(c.id) in (None, demand_id)]
        eligible = sorted([m for m in results if m.eligible],
                          key=lambda m: (-m.score, m.distance_km if m.distance_km is not None else 1e9, m.candidate.id))
        return eligible, [m for m in results if not m.eligible]

    def incomplete_near(self, demand_id, rules, assigned=None):
        """Incomplete profiles (call to complete), nearest first."""
        assigned = assigned or {}
        d = self.demands[demand_id]
        _, incomplete, _ = self.pool(rules)
        rows = [(c, self.distance(c, d)) for c in incomplete if c.id not in assigned]
        return sorted(rows, key=lambda x: (x[1] if x[1] is not None else 1e9, x[0].id))

    def diagnose(self, demand_id, weights, rules, assigned=None):
        """Why a demand can't be filled and what single change would help."""
        assigned = assigned or {}
        d = self.demands[demand_id]
        eligible, inelig = self.shortlist(demand_id, weights, rules, assigned)
        filled = sum(1 for v in assigned.values() if v == demand_id)
        unassigned_eligible = [m for m in eligible if assigned.get(m.candidate.id) != demand_id]
        remaining = max(0, d.headcount - filled)
        short = max(0, remaining - len(unassigned_eligible))
        counts = Counter(code for m in inelig for code, _ in m.blockers)
        only = defaultdict(list)
        for m in inelig:
            if len(m.blockers) == 1:
                only[m.blockers[0][0]].append(m)
        tips = []
        need = max(1, short)
        if only["pay"]:
            pays = sorted(m.candidate.pay for m in only["pay"])
            k = min(need, len(pays))
            tips.append(f"Pay: {len(pays)} candidate(s) fail only on pay. Raising pay to {_fmt_inr(pays[k-1] - rules['max_pay_over'])} "
                        f"brings {k} within tolerance (their asks: {', '.join(_fmt_inr(p) for p in pays[:5])}).")
        if only["distance"]:
            kms = sorted(m.distance_km for m in only["distance"])
            k = min(need, len(kms))
            tips.append(f"Distance: {len(kms)} candidate(s) fail only on commute. Widening radius to {math.ceil(kms[k-1])} km adds {k}.")
        if only["joining"]:
            lates = sorted((m.candidate.join - d.eff_start).days for m in only["joining"])
            k = min(need, len(lates))
            tips.append(f"Start date: {len(lates)} candidate(s) fail only on joining date. Accepting {lates[k-1]} days late adds {k}.")
        if only["education"]:
            tips.append(f"Education: {len(only['education'])} candidate(s) fail only on the {d.min_edu} minimum — ask if the employer can relax it.")
        if only["shift"]:
            tips.append(f"Shift: {len(only['shift'])} candidate(s) fail only on shift — ask if the employer can offer another shift.")
        if only["role"]:
            tips.append(f"Experience: {len(only['role'])} candidate(s) fail only on missing {d.role_title} experience — accept trainees?")
        inc = self.incomplete_near(demand_id, rules, assigned)
        near_inc = [c for c, km in inc if km is not None and km <= rules["max_km"]]
        if near_inc:
            tips.append(f"Sourcing: {len(near_inc)} incomplete profile(s) live within {rules['max_km']:.0f} km — call to complete them.")
        if not short:
            tips = []   # enough candidates; no advice needed
        elif not tips:
            tips.append("No single change unlocks candidates — source new candidates for this role/area.")
        return {"headcount": d.headcount, "filled": filled, "remaining": remaining,
                "eligible": len(unassigned_eligible), "short": short, "blockers": dict(counts), "tips": tips}
