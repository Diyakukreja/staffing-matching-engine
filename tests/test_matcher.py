"""Run: pytest -q   (from the project folder). Test IDs match TESTING.md."""
import os
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db import Store  # noqa: E402
from matcher import (DEFAULT_RULES, DEFAULT_WEIGHTS, Candidate, Engine, norm_edu,  # noqa: E402
                     parse_pay)

# CI / local runs use the anonymised data in ./data. Set MATCHER_TEST_DATA to re-run on the raw files.
DATA = Path(os.environ.get("MATCHER_TEST_DATA") or ROOT / "data")

TODAY = date(2026, 11, 17)


@pytest.fixture(scope="module")
def E():
    return Engine(DATA, today=TODAY)


@pytest.fixture
def R():
    return dict(DEFAULT_RULES)


W = DEFAULT_WEIGHTS


def cand(**kw):
    base = dict(id="X1", name="Test", phone="9999999999", raw_locality="Whitefield", locality="Whitefield",
                loc_how="exact", roles=[("Warehouse Picker", "picker", 12)], pay=18000, shifts={"morning"},
                join=date(2026, 11, 20), edu="10th", status="available")
    base.update(kw)
    return Candidate(**base)


def ids(matches):
    return {m.candidate.id for m in matches}


# ---------------- cleaning ----------------
@pytest.mark.parametrize("raw,exp", [(19000, 19000), ("19,000", 19000), ("15.5k", 15500), ("Rs. 22000", 22000),
                                     ("18500", 18500), ("17k", 17000), (None, None), ("abc", None)])
def test_T01_pay_parsing(raw, exp):
    assert parse_pay(raw) == exp


@pytest.mark.parametrize("raw,exp", [("SSLC", "10th"), ("10th pass", "10th"), ("PUC", "12th"), ("12th pass", "12th"),
                                     ("ITI", "iti"), ("iti", "iti"), ("diploma", "diploma"), (None, None)])
def test_T02_education_normalisation(raw, exp):
    assert norm_edu(raw) == exp


@pytest.mark.parametrize("raw,exp", [
    ("Indira Nagar", "Indiranagar"), ("White Field", "Whitefield"), ("Yesvantpur", "Yeshwanthpur"),
    ("Yeshwantpur", "Yeshwanthpur"), ("Kormangala", "Koramangala"), ("koramangala", "Koramangala"),
    ("Koramangala 5th Block", "Koramangala"), ("Marathalli", "Marathahalli"),
    ("Marathahalli Bridge", "Marathahalli"), ("BTM 2nd Stage", "BTM Layout"), ("BTM", "BTM Layout"),
    ("J P Nagar", "JP Nagar"), ("JP Nagar 6th Phase", "JP Nagar"), ("K R Puram", "KR Puram"),
    ("Krishnarajapuram", "KR Puram"), ("Rajaji Nagar", "Rajajinagar"), ("HSR Layout Sector 2", "HSR Layout"),
    ("Peenya Industrial Area", "Peenya"), ("Whitefield Main Road", "Whitefield"),
    ("Bommanhalli", "Bommanahalli"),  # must NOT become Bommasandra
])
def test_T03_locality_resolution(E, raw, exp):
    assert E.locs.resolve(raw)[0] == exp


def test_T03b_every_locality_in_data_resolves(E):
    assert all(c.locality for c in E.candidates.values())
    assert all(d.locality for d in E.demands.values())


def test_T04_dedupe_by_phone_not_name(E):
    assert len(E.candidates) == 196
    for keep, dup in [("C002", "C077"), ("C051", "C182"), ("C067", "C187"), ("C076", "C118")]:
        assert keep in E.candidates and dup not in E.candidates
        assert dup in E.candidates[keep].dup_ids
    # same name, different phone -> different people
    assert {"C001", "C052", "C165"} <= set(E.candidates)       # three people sharing one name
    assert {"C115", "C169", "C181"} <= set(E.candidates)       # another same-name trio


def test_T05_all_role_titles_recognised(E):
    assert not [i for i in E.issues if i[0] in ("role", "education")]


# ---------------- hard filters ----------------
def test_T06_status_filter(E, R):
    blocked = {"C006", "C032", "C087", "C144", "C090", "C027"}           # deployed / offer_accepted
    not_reach = {"C031", "C038", "C055", "C056", "C059", "C113", "C142"}
    for did in E.demands:
        el, inel = E.shortlist(did, W, R)
        assert not (ids(el) | ids(inel)) & (blocked | not_reach)
    R["show_not_reachable"] = True
    el, inel = E.shortlist("D10", W, R)
    seen = ids(el) | ids(inel)
    assert not seen & blocked and seen & not_reach


def test_T07_shift_is_hard(E, R):
    m = E.evaluate(cand(shifts={"night"}), E.demands["D01"], W, R)
    assert not m.eligible and m.blockers[0][0] == "shift"


def test_T08_education_ladder(E, R):
    d = E.demands["D07"]  # Barista, min 12th
    assert not E.evaluate(cand(edu="10th", roles=[("Barista", "barista", 12)]), d, W, R).eligible
    assert not E.evaluate(cand(edu="iti", roles=[("Barista", "barista", 12)]), d, W, R).eligible
    assert E.evaluate(cand(edu="diploma", roles=[("Barista", "barista", 12)]), d, W, R).eligible
    R["iti_counts_as_12th"] = True
    assert E.evaluate(cand(edu="iti", roles=[("Barista", "barista", 12)]), d, W, R).eligible


def test_T09_skilled_vs_entry_roles(E, R):
    fresher = cand(roles=[], locality="Bommasandra", edu="iti", pay=20000)
    m = E.evaluate(fresher, E.demands["D05"], W, R)                      # forklift = skilled
    assert not m.eligible and ("role" in dict(m.blockers))
    m = E.evaluate(cand(roles=[], locality="Marathahalli", pay=15000), E.demands["D11"], W, R)  # housekeeping
    assert m.eligible and m.match_type == "none" and m.comps["experience"] == 0


def test_T10_distance_cutoff(E, R):
    m = E.evaluate(cand(locality="Kengeri"), E.demands["D01"], W, R)        # ~30 km from Whitefield
    assert not m.eligible and "distance" in dict(m.blockers)
    R["max_km"] = 40
    assert E.evaluate(cand(locality="Kengeri"), E.demands["D01"], W, R).eligible


def test_T11_pay_tolerance(E, R):
    d = E.demands["D01"]  # pays 19,000
    over2k = E.evaluate(cand(pay=21000), d, W, R)
    assert over2k.eligible and "expects ₹2,000 more" in over2k.reason and over2k.comps["pay"] < 1
    assert not E.evaluate(cand(pay=22500), d, W, R).eligible
    under = E.evaluate(cand(pay=18500), d, W, R)
    assert under.comps["pay"] == 1 and "expects ₹500 less" in under.reason


def test_T12_joining_date(E, R):
    d = E.demands["D01"]  # start 23 Nov
    late3 = E.evaluate(cand(join=date(2026, 11, 26)), d, W, R)
    assert late3.eligible and "3 days after start" in late3.reason and 0 < late3.comps["joining"] < 1
    assert not E.evaluate(cand(join=date(2026, 12, 3)), d, W, R).eligible
    d13 = E.demands["D13"]  # start 5 Oct (passed) -> today
    assert d13.start_passed and d13.eff_start == TODAY


def test_T13_incomplete_profiles_not_ranked(E, R):
    _, incomplete, _ = E.pool(R)
    assert len(incomplete) == 30
    inc_ids = {c.id for c in incomplete}
    for did in E.demands:
        el, inel = E.shortlist(did, W, R)
        assert not (ids(el) | ids(inel)) & inc_ids
    near = E.incomplete_near("D01", R)
    assert {c.id for c, _ in near} == inc_ids
    assert near[0][1] <= near[-1][1]


def test_T14_half_empty_ranked_on_known(E, R):
    c = cand(pay=None, join=None)                      # 2 of 5 missing -> still ranked
    assert not c.needs_profile
    m = E.evaluate(c, E.demands["D01"], W, R)
    assert m.eligible and m.comps["pay"] == 0.5 and m.comps["joining"] == 0.5
    assert m.comps["completeness"] == pytest.approx(0.6)            # 3 of 5 fields known
    assert "pay expectation unknown" in m.reason and any("missing" in f for f in m.flags)


# ---------------- scoring ----------------
def test_T15_score_bounds(E, R):
    perfect = cand(roles=[("Warehouse Picker", "picker", 48)], pay=15000, join=date(2026, 11, 18))
    assert E.evaluate(perfect, E.demands["D01"], W, R).score == 100.0
    for did in E.demands:
        for m in E.shortlist(did, W, R)[0]:
            assert 0 <= m.score <= 100


def test_T16_closer_ranks_higher(E, R):
    d = E.demands["D01"]
    near = E.evaluate(cand(locality="Kadugodi"), d, W, R)
    far = E.evaluate(cand(locality="Indiranagar"), d, W, R)
    assert near.distance_km < far.distance_km and near.score > far.score


def test_T17_reason_matches_data(E, R):
    m = E.evaluate(E.candidates["C005"], E.demands["D01"], W, R)
    assert m.reason.startswith(f"{m.distance_km:.1f} km away")
    assert "4 months as Warehouse Picker" in m.reason and "expects exactly the pay" in m.reason


def test_T18_meena_weights_change_ranking(E, R):
    dist_only = {"experience": 0, "distance": 100, "pay": 0, "joining": 0, "completeness": 0}
    el, _ = E.shortlist("D01", dist_only, R)
    kms = [m.distance_km for m in el]
    assert kms == sorted(kms)
    default_top = E.shortlist("D01", W, R)[0][0].candidate.id
    assert el[0].candidate.id != default_top          # order genuinely changes
    zero = {k: 0 for k in W}
    assert all(m.score == 0 for m in E.shortlist("D01", zero, R)[0])


def test_T19_reach_truck_toggle(E, R):
    d = E.demands["D05"]
    m = E.evaluate(E.candidates["C101"], d, W, R)           # 40 months reach truck -> related, half credit
    assert m.match_type == "related" and m.comps["experience"] == pytest.approx(20 / 24)
    R["reach_truck_is_forklift"] = True
    m = E.evaluate(E.candidates["C101"], d, W, R)
    assert m.match_type == "exact" and m.comps["experience"] == 1.0


# ---------------- real-data expectations ----------------
def test_T20_known_outcomes(E, R):
    assert E.shortlist("D01", W, R)[0][0].candidate.id == "C020"
    assert ids(E.shortlist("D05", W, R)[0]) == {"C034", "C061", "C101", "C138", "C160"}
    assert ids(E.shortlist("D05", W, R)[0]) == ids(E.shortlist("D06", W, R)[0])   # contested pool
    c052 = E.evaluate(E.candidates["C052"], E.demands["D09"], W, R)              # lives in Devanahalli
    assert [b for b, _ in c052.blockers] == ["pay"]
    dg = E.diagnose("D09", W, R)
    assert dg["short"] >= 5 and any(t.startswith("Pay:") for t in dg["tips"])
    assert E.diagnose("D01", W, R)["tips"] == []                                    # no advice when fillable


# ---------------- store ----------------
@pytest.fixture
def S(tmp_path):
    return Store(str(tmp_path / "t.db"))


def test_T21_no_double_booking(S):
    assert S.assign("C020", "D01", 12)[0]
    ok, msg = S.assign("C020", "D02", 6)
    assert not ok and "already assigned to D01" in msg


def test_T22_no_overfill(S):
    assert S.assign("C001", "D05", 2)[0] and S.assign("C002", "D05", 2)[0]
    ok, msg = S.assign("C003", "D05", 2)
    assert not ok and "full" in msg and S.filled("D05") == 2


def test_T23_persists_across_restart(tmp_path):
    p = str(tmp_path / "p.db")
    Store(p).assign("C020", "D01", 12)
    Store(p).set("weights", {"role": 50})
    s2 = Store(p)
    assert s2.assignments() == {"C020": "D01"} and s2.get("weights") == {"role": 50}


def test_T24_unassign_and_visibility(E, R, S):
    S.assign("C020", "D01", 12)
    el, _ = E.shortlist("D02", W, R, S.assignments())
    assert "C020" not in ids(el)                                   # hidden from other demands
    S.unassign("C020")
    el, _ = E.shortlist("D02", W, R, S.assignments())
    assert "C020" in ids(el)


def test_T25_closed_demand(S):
    assert not S.assign("C020", "D15", 0)[0]


def test_T26_settings_reset(S):
    S.set("weights", {"role": 1})
    S.delete("weights")
    assert S.get("weights", DEFAULT_WEIGHTS) == DEFAULT_WEIGHTS


# ---------------- UI ----------------
def _app(db):
    from streamlit.testing.v1 import AppTest
    os.environ.pop("DATABASE_URL", None)
    os.environ["MATCHER_DB"] = db
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)


def test_T27_ui_weights_persist_and_reset(tmp_path):
    db = str(tmp_path / "ui.db")
    at = _app(db).run()
    assert not at.exception
    at.slider(key="w_distance").set_value(90).run()
    assert Store(db).get("weights")["distance"] == 90
    at2 = _app(db).run()                                          # "page refresh"
    assert at2.slider(key="w_distance").value == 90
    at2.button[[b.label for b in at2.button].index("Reset weights to default")].click().run()
    assert Store(db).get("weights") is None and at2.slider(key="w_distance").value == DEFAULT_WEIGHTS["distance"]


def test_T28_ui_assign_persists(tmp_path):
    db = str(tmp_path / "ui2.db")
    at = _app(db).run()
    at.button(key="as_C020").click().run()
    assert not at.exception
    assert Store(db).assignments() == {"C020": "D01"}
    at2 = _app(db).run()
    assert [m.value for m in at2.metric if m.label == "Filled"][0] == "1"


# ---------------- final scoring model (v2: 40/25/20/10/5) ----------------
def test_T29_default_weights_are_final_model():
    assert DEFAULT_WEIGHTS == {"experience": 40, "distance": 25, "pay": 20, "joining": 10, "completeness": 5}
    assert sum(DEFAULT_WEIGHTS.values()) == 100
    assert "role" not in DEFAULT_WEIGHTS                     # old 35/25/20/10/10 model retired
    assert DEFAULT_RULES["exp_cap_months"] == 24


@pytest.mark.parametrize("roles,exp", [
    ([("Warehouse Picker", "picker", 12)], 0.5),
    ([("Warehouse Picker", "picker", 24)], 1.0),
    ([("Warehouse Picker", "picker", 60)], 1.0),                       # capped at 24 months
    ([("Godown helper", "warehouse_general", 24)], 0.5),               # related = half credit
    ([("Picker", "picker", 6), ("Godown helper", "warehouse_general", 12)], 0.5),   # 6 + 0.5*12 = 12
    ([("Barista", "barista", 48)], 0.0),                               # unrelated family
    ([], 0.0),
])
def test_T30_experience_cap_and_relevance(E, R, roles, exp):
    m = E.evaluate(cand(roles=roles), E.demands["D01"], W, R)
    assert m.comps["experience"] == pytest.approx(exp)


def test_T31_hand_computed_score(E, R):
    # C020 for D01: 37 months Picker/Packer (exact, capped 1.0), 3.5 km, expects 2,000 less, joins on time,
    # complete profile -> 40 + 25*(1-km/25) + 20 + 10 + 5
    m = E.evaluate(E.candidates["C020"], E.demands["D01"], W, R)
    expected = 40 + 25 * (1 - m.distance_km / 25) + 20 + 10 + 5
    assert m.score == round(expected, 1) == 96.5
    assert m.reason == ("3.5 km away, 37 months as Picker/Packer, expects ₹2,000 less than the pay, "
                        "can join by the start date")


def test_T32_points_add_up_to_score(E, R):
    for did in E.demands:
        for m in E.shortlist(did, W, R)[0]:
            pts = m.points(W)
            assert list(pts) == list(DEFAULT_WEIGHTS)
            assert sum(pts.values()) == pytest.approx(m.score, abs=0.051)
            assert pts["experience"] <= 40 + 1e-9 and pts["completeness"] <= 5 + 1e-9


def test_T33_completeness_component(E, R):
    full = E.evaluate(cand(), E.demands["D01"], W, R)
    half = E.evaluate(cand(pay=None, join=None), E.demands["D01"], W, R)
    assert full.comps["completeness"] == 1.0 and half.comps["completeness"] == pytest.approx(0.6)
    assert "profile 3/5 complete" in half.reason and "profile" not in full.reason


def test_T34_ranking_is_deterministic(E, R):
    import random
    before = {did: [(m.candidate.id, m.score) for m in E.shortlist(did, W, R)[0]] for did in E.demands}
    again = {did: [(m.candidate.id, m.score) for m in E.shortlist(did, W, R)[0]] for did in E.demands}
    assert before == again
    # shuffle the candidate order in memory: ranking must not depend on input order
    items = list(E.candidates.items())
    random.Random(7).shuffle(items)
    saved = E.candidates
    try:
        E.candidates = dict(items)
        shuffled = {did: [(m.candidate.id, m.score) for m in E.shortlist(did, W, R)[0]] for did in E.demands}
    finally:
        E.candidates = saved
    assert shuffled == before
    for did, lst in before.items():                            # sorted by score desc
        assert [s for _, s in lst] == sorted((s for _, s in lst), reverse=True)


def test_T35_data_loads_and_normalises(E, R):
    import json
    raw = json.loads((DATA / "candidates.json").read_text())
    assert len(raw) == 200 and len(json.loads((DATA / "demands.json").read_text())) == 15
    assert len(E.demands) == 15 and len(json.loads((DATA / "localities.json").read_text())) == 60
    rankable, incomplete, excluded = E.pool(R)
    assert (len(rankable), len(incomplete), sum(excluded.values())) == (143, 30, 23)
    assert all(isinstance(c.pay, int) for c in rankable if c.pay is not None)
    assert all(len(c.phone) == 10 and c.phone.isdigit() for c in E.candidates.values())
    assert E.demands["D15"].headcount == 0


def test_T36_distance_haversine(E):
    from matcher import haversine_km
    assert haversine_km((12.9698, 77.75), (12.9698, 77.75)) == 0
    km = E.distance(E.candidates["C020"], E.demands["D01"])      # Brookefield -> Whitefield
    assert 3.4 < km < 3.6
    assert 10 < haversine_km(E.locs.coords["Koramangala"], E.locs.coords["Whitefield"]) < 15


def test_T37_concurrent_assign_never_overfills(tmp_path):
    import threading
    p = str(tmp_path / "c.db")
    stores = [Store(p) for _ in range(8)]                         # separate connections, like separate users
    results = []
    ts = [threading.Thread(target=lambda s=s, i=i: results.append(s.assign(f"C{i:03d}", "D05", 4)[0]))
          for i, s in enumerate(stores)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(results) == 4 and Store(p).filled("D05") == 4


def test_T38_committed_data_is_anonymised():
    import json
    import re
    rows = json.loads((ROOT / "data" / "candidates.json").read_text())
    assert all(re.sub(r"\D", "", r["phone"])[-10:].startswith("70") for r in rows)   # synthetic range
    raw_path = ROOT / "data" / "private" / "candidates.raw.json"
    if raw_path.exists():                                         # only on the machine holding raw data
        raw = json.loads(raw_path.read_text())
        assert not {r["name"] for r in raw} & {r["name"] for r in rows}
    gi = (ROOT / ".gitignore").read_text()
    assert "data/private/" in gi and ".streamlit/secrets.toml" in gi


def test_T39_ui_unassign_and_stale_weights(tmp_path):
    db = str(tmp_path / "ui3.db")
    s = Store(db)
    s.set("weights", {"role": 35, "distance": 25, "pay": 20, "joining": 10, "experience": 10})   # old model
    s.assign("C020", "D01", 12)
    at = _app(db).run()
    assert not at.exception
    assert at.slider(key="w_experience").value == 40 and at.slider(key="w_completeness").value == 5
    at.button(key="un_C020").click().run()
    assert not at.exception and Store(db).assignments() == {}


def test_T40_ui_all_tabs_render_with_assignments(tmp_path):
    db = str(tmp_path / "ui4.db")
    s = Store(db)
    for cid in ("C034", "C061", "C101", "C138"):
        s.assign(cid, "D05", 4)
    at = _app(db).run()
    assert not at.exception
    assert len(at.tabs) == 6
    at.selectbox(key="demand").set_value("D05").run()
    assert not at.exception
    assert all(b.disabled for b in at.button if b.key and b.key.startswith("as_"))   # full -> disabled
    at.text_input(key="q_cand").set_value("C020").run()
    assert not at.exception
    # phones are masked by default: no full phone number from the data appears anywhere on the page
    import json
    phones = {r["phone"][-10:] for r in json.loads((ROOT / "data" / "candidates.json").read_text())}
    page = " ".join(str(m.value) for m in list(at.markdown) + list(at.caption))
    page += " ".join(df.value.to_csv() for df in at.dataframe)
    assert not any(p in page for p in phones) and "••••••" in page


# ---------------- Postgres backend (runs when TEST_DATABASE_URL is set) ----------------
PG = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture
def PGS():
    if not PG:
        pytest.skip("TEST_DATABASE_URL not set")
    s = Store(PG)
    s.clear_all()
    yield s
    s.clear_all()


def test_T41_postgres_invariants(PGS):
    assert PGS.backend == "postgres"
    assert PGS.assign("C020", "D01", 12)[0]
    ok, msg = PGS.assign("C020", "D02", 6)
    assert not ok and "already assigned to D01" in msg
    assert PGS.assign("C001", "D05", 1)[0] and not PGS.assign("C002", "D05", 1)[0]
    assert not PGS.assign("C003", "D15", 0)[0]
    PGS.set("weights", {"experience": 1})
    s2 = Store(PG)                                               # new connection = "refresh / restart"
    assert s2.assignments() == {"C020": "D01", "C001": "D05"} and s2.get("weights") == {"experience": 1}
    assert s2.unassign("C020") and "C020" not in Store(PG).assignments()
    PGS.delete("weights")
    assert PGS.get("weights") is None


def test_T42_postgres_concurrent_assign(PGS):
    import threading
    stores = [Store(PG) for _ in range(8)]
    res = []
    ts = [threading.Thread(target=lambda s=s, i=i: res.append(s.assign(f"C{i:03d}", "D06", 4)[0]))
          for i, s in enumerate(stores)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    # same candidate raced onto two demands
    a, b = Store(PG), Store(PG)
    r2 = []
    t1 = threading.Thread(target=lambda: r2.append(a.assign("C150", "D01", 12)[0]))
    t2 = threading.Thread(target=lambda: r2.append(b.assign("C150", "D02", 6)[0]))
    t1.start(); t2.start(); t1.join(); t2.join()  # noqa: E702
    assert sum(res) == 4 and PGS.filled("D06") == 4 and sum(r2) == 1


def test_T43_ui_on_postgres_persists(PGS, tmp_path):
    from streamlit.testing.v1 import AppTest
    os.environ["DATABASE_URL"] = PG
    try:
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
        assert not at.exception
        at.button(key="as_C020").click().run()
        at2 = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
        assert [m.value for m in at2.metric if m.label == "Filled"][0] == "1"
        assert Store(PG).assignments() == {"C020": "D01"}
    finally:
        os.environ.pop("DATABASE_URL", None)
