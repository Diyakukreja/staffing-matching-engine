"""Staffing Matching Engine - Streamlit UI for the operations lead.  Run: streamlit run app.py"""
import os

import pandas as pd
import streamlit as st

from db import Store
from matcher import DEFAULT_RULES, DEFAULT_WEIGHTS, WEIGHT_LABELS, Engine

st.set_page_config(page_title="Staffing Matching Engine", page_icon="🧭", layout="wide")


def _secret(name, default=None):
    """Streamlit secret first (cloud), then environment variable (local / CI)."""
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:          # no secrets.toml at all
        pass
    return os.environ.get(name, default)


DB_TARGET = _secret("DATABASE_URL") or os.environ.get("MATCHER_DB", "matcher.db")
SHOW_PHONES = str(_secret("SHOW_PHONES", "false")).lower() in ("1", "true", "yes")


@st.cache_resource
def get_engine():
    return Engine()


@st.cache_resource
def get_store(target):
    return Store(target)


E, S = get_engine(), get_store(DB_TARGET)
RULE_KEYS = list(DEFAULT_RULES)
SHORT = {"experience": "Exp", "distance": "Prox", "pay": "Pay", "joining": "Join", "completeness": "Data"}
STATUS_ICON = {"Closed": "⚪ Closed", "Filled": "🟢 Filled", "Fillable": "🔵 Fillable"}

st.markdown("""<style>
.block-container {padding-top: 2.2rem;}
.flag {display:inline-block; background:#fff4e5; color:#8a4b00; border:1px solid #f3d19e; border-radius:10px;
       padding:0 8px; margin:2px 4px 2px 0; font-size:0.78rem;}
.score {font-size:1.9rem; font-weight:700; line-height:1.1;}
.muted {color:#6b7280; font-size:0.82rem;}
@media (prefers-color-scheme: dark) { .flag {background:#3a2a10; color:#f6c56f; border-color:#6b4d1d;} }
</style>""", unsafe_allow_html=True)


def phone(p):
    return p if SHOW_PHONES else f"••••••{p[-4:]}"


# ---------- settings: saved values override defaults ----------
def _saved_weights():
    w = S.get("weights")
    # ignore weights saved under the retired 35/25/20/10/10 model (different factor names)
    return w if isinstance(w, dict) and set(w) == set(DEFAULT_WEIGHTS) else None


def _load_into_state():
    w = _saved_weights() or DEFAULT_WEIGHTS
    r = {**DEFAULT_RULES, **{k: v for k, v in (S.get("rules") or {}).items() if k in DEFAULT_RULES}}
    for k in DEFAULT_WEIGHTS:
        st.session_state[f"w_{k}"] = int(w[k])
    for k in RULE_KEYS:
        st.session_state[f"r_{k}"] = r[k]


if "loaded" not in st.session_state:
    _load_into_state()
    st.session_state.loaded = True


def save_weights():
    S.set("weights", {k: st.session_state[f"w_{k}"] for k in DEFAULT_WEIGHTS})


def reset_weights():
    S.delete("weights")
    for k, v in DEFAULT_WEIGHTS.items():
        st.session_state[f"w_{k}"] = v


def save_rules():
    S.set("rules", {k: st.session_state[f"r_{k}"] for k in RULE_KEYS})


def reset_rules():
    S.delete("rules")
    for k, v in DEFAULT_RULES.items():
        st.session_state[f"r_{k}"] = v


def do_assign(cid, did, headcount):
    ok, msg = S.assign(cid, did, headcount)
    st.session_state.flash = ("success" if ok else "error", msg)


def do_unassign(cid):
    S.unassign(cid)
    st.session_state.flash = ("success", f"{cid} unassigned")


# ---------- sidebar ----------
with st.sidebar:
    st.header("Scoring weights")
    custom = _saved_weights() is not None
    st.caption("Using **saved custom weights**" if custom else "Using **default weights** (40/25/20/10/5)")
    for k, label in WEIGHT_LABELS.items():
        st.slider(label, 0, 100, key=f"w_{k}", on_change=save_weights)
    total = sum(st.session_state[f"w_{k}"] for k in DEFAULT_WEIGHTS)
    if total:
        st.caption("Effective share: " + " · ".join(
            f"{SHORT[k]} {100 * st.session_state[f'w_{k}'] / total:.0f}%" for k in DEFAULT_WEIGHTS))
    else:
        st.warning("All weights are 0, so every score is 0.")
    st.button("Reset weights to default", on_click=reset_weights, disabled=not custom)

    st.header("Eligibility rules")
    st.caption("Using **saved custom rules**" if S.get("rules") else "Using **default rules**")
    st.slider("Max commute (km)", 5.0, 50.0, step=1.0, key="r_max_km", on_change=save_rules)
    st.number_input("Max ₹ above offered pay (still shown)", 0, 10000, step=500, key="r_max_pay_over",
                    on_change=save_rules)
    st.slider("Max days joining after start", 0, 30, key="r_max_days_late", on_change=save_rules)
    st.multiselect("Skilled roles (need experience)", ["forklift", "rider", "barista", "security", "housekeeping",
                                                       "picker", "packer", "picker_packer"],
                   key="r_skilled_roles", on_change=save_rules)
    st.toggle("Reach-truck operator = forklift operator", key="r_reach_truck_is_forklift", on_change=save_rules)
    st.toggle("ITI counts as 12th", key="r_iti_counts_as_12th", on_change=save_rules)
    st.toggle("Include not-reachable candidates (flagged)", key="r_show_not_reachable", on_change=save_rules)
    st.button("Reset rules to default", on_click=reset_rules, disabled=S.get("rules") is None)
    st.caption(f"Experience is capped at {DEFAULT_RULES['exp_cap_months']} relevant months.")

    st.divider()
    if S.durable:
        st.caption("🟢 Storage: Postgres. Assignments are durable.")
    else:
        st.caption("🟡 Storage: local SQLite file. Fine on a laptop; **not durable on a cloud host**. "
                   "Set `DATABASE_URL` for persistent storage.")
    st.caption(f"Today: {E.today:%d %b %Y} · Phones {'shown' if SHOW_PHONES else 'masked'}")

W = {k: st.session_state[f"w_{k}"] for k in DEFAULT_WEIGHTS}
R = {k: st.session_state[f"r_{k}"] for k in RULE_KEYS}
assigned = S.assignments()

# ---------- header ----------
st.title("Staffing Matching Engine")
st.caption("Rank candidates for every open demand, assign them, and see why positions stay unfilled.")
if "flash" in st.session_state:   # toast, not an inline alert: keeps the layout stable so the open tab stays open
    kind, msg = st.session_state.pop("flash")
    st.toast(msg, icon="✅" if kind == "success" else "⛔")

DIAG = {d.id: E.diagnose(d.id, W, R, assigned) for d in E.demands.values()}


def status_of(d, dg):
    if d.headcount == 0:
        return "Closed"
    if dg["remaining"] == 0:
        return "Filled"
    return f"🔴 Short by {dg['short']}" if dg["short"] else "Fillable"


open_d = [d for d in E.demands.values() if d.headcount > 0]
k = st.columns(5)
k[0].metric("Open demands", len(open_d))
k[1].metric("Positions", sum(d.headcount for d in open_d))
k[2].metric("Assigned", sum(DIAG[d.id]["filled"] for d in open_d))
k[3].metric("Still open", sum(DIAG[d.id]["remaining"] for d in open_d))
k[4].metric("Demands short of candidates", sum(1 for d in open_d if DIAG[d.id]["short"]))

tabs = st.tabs(["Demand board", "Shortlist", "Assignments", "Unfilled report", "Candidates", "Data quality"])
tab_board, tab_short, tab_assign, tab_unfilled, tab_cand, tab_data = tabs

# ---------- Demand board ----------
with tab_board:
    rows = []
    for d in E.demands.values():
        dg = DIAG[d.id]
        stt = status_of(d, dg)
        rows.append({"ID": d.id, "Status": STATUS_ICON.get(stt, stt), "Employer": d.employer,
                     "Role": d.role_title, "Site": d.raw_locality,
                     "Shift": d.shift.title(), "Start": f"{d.start:%d %b}" + (" → ASAP" if d.start_passed else ""),
                     "Pay (₹)": d.pay, "Headcount": d.headcount, "Filled": dg["filled"],
                     "Remaining": dg["remaining"], "Eligible pool": dg["eligible"],
                     "Fill %": (dg["filled"] / d.headcount) if d.headcount else 0.0})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=36 * (len(rows) + 1) + 4,
                 column_config={"Fill %": st.column_config.ProgressColumn("Fill %", min_value=0, max_value=1,
                                                                          format="percent"),
                                "Pay (₹)": st.column_config.NumberColumn(format="%d")})
    st.caption("Eligible pool = unassigned candidates who pass every hard rule for that demand. "
               "Pools overlap: one person can be eligible for several demands (e.g. D05 and D06 forklift).")

# ---------- Shortlist ----------
with tab_short:
    did = st.selectbox("Demand", list(E.demands), format_func=lambda i: E.demands[i].label, key="demand")
    d = E.demands[did]
    elig, inelig = E.shortlist(did, W, R, assigned)
    dg = DIAG[did]

    c = st.columns(7)
    c[0].metric("Pay", f"₹{d.pay:,}")
    c[1].metric("Start", f"{d.start:%d %b}", "passed → ASAP" if d.start_passed else None, delta_color="off")
    c[2].metric("Min education", d.min_edu or "—")
    c[3].metric("Headcount", d.headcount)
    c[4].metric("Filled", dg["filled"])
    c[5].metric("Remaining", dg["remaining"])
    c[6].metric("Eligible", dg["eligible"])
    if d.headcount == 0:
        st.info("This demand is closed (headcount 0). Assignment is disabled.")
    elif dg["remaining"] == 0:
        st.success("All positions filled. Unassign someone to free a slot.")

    here = [cid for cid, dd in assigned.items() if dd == did]
    if here:
        with st.container(border=True):
            st.markdown(f"**Assigned to {did}** ({len(here)}/{d.headcount})")
            for cid in here:
                cand = E.candidates[cid]
                a, b = st.columns([6, 1])
                a.write(f"{cand.name} · {cid} · {phone(cand.phone)} · {cand.raw_locality}")
                b.button("Unassign", key=f"un_{cid}", on_click=do_unassign, args=(cid,))

    if dg["short"]:
        with st.expander(f"⚠ Unfilled diagnostics: short by {dg['short']}", expanded=True):
            if dg["blockers"]:
                st.write("Candidates ruled out, by rule: " + ", ".join(
                    f"**{k}** {v}" for k, v in sorted(dg["blockers"].items(), key=lambda x: -x[1])))
            for t in dg["tips"]:
                st.write("• " + t)

    st.subheader("Ranked shortlist")
    f1, f2 = st.columns([3, 1])
    q = f1.text_input("Search shortlist (name, ID or locality)", key="q_short", placeholder="e.g. C020 or Hoodi")
    top_n = f2.number_input("Show top", 5, 200, 15, step=5, key="top_n")
    ranked = [(i, m) for i, m in enumerate((m for m in elig if assigned.get(m.candidate.id) != did), 1)]
    if q:
        ql = q.strip().lower()
        ranked = [(i, m) for i, m in ranked if ql in m.candidate.name.lower() or ql in m.candidate.id.lower()
                  or ql in m.candidate.raw_locality.lower() or ql in (m.candidate.locality or "").lower()]
    full = dg["remaining"] <= 0
    for i, m in ranked[:top_n]:
        cand = m.candidate
        with st.container(border=True):
            a, b, cc = st.columns([0.9, 7, 1.3])
            a.markdown(f"<div class='muted'>#{i}</div><div class='score'>{m.score:.1f}</div>", unsafe_allow_html=True)
            b.markdown(f"**{cand.name}** · {cand.id} · {phone(cand.phone)} · {cand.raw_locality}  \n{m.reason}")
            pts = m.points(W)
            b.markdown("<span class='muted'>" + " · ".join(f"{SHORT[k]} {v:.1f}" for k, v in pts.items())
                       + "</span>" + "".join(f"<span class='flag'>⚠ {f}</span>" for f in m.flags),
                       unsafe_allow_html=True)
            cc.button("Assign", key=f"as_{cand.id}", on_click=do_assign, args=(cand.id, did, d.headcount),
                      disabled=full, type="primary", width="stretch")
    if not ranked:
        st.warning("No eligible candidates match." if q else
                   "No eligible candidates under the current rules. See the diagnostics above.")

    with st.expander("Score breakdown (points per factor; they add up to the score)"):
        st.dataframe(pd.DataFrame([{"Rank": i, "ID": m.candidate.id, "Name": m.candidate.name, "Score": m.score,
                                    **{WEIGHT_LABELS[k]: round(v, 1) for k, v in m.points(W).items()},
                                    "Relevant months": m.relevant_months, "Match": m.match_type,
                                    "km": None if m.distance_km is None else round(m.distance_km, 1)}
                                   for i, m in ranked[:top_n]]), hide_index=True, width="stretch")
        st.caption("Factor points = weight share × factor score (0-1). Experience = (exact months + ½ × "
                   f"related months) / {R['exp_cap_months']}, capped at 1. Proximity = 1 − km / max km. "
                   "Salary = 1 if expectation ≤ offer, else falls linearly to 0 at the tolerance. "
                   "Joining = 1 if on time, else falls by day. Completeness = known fields / 5. "
                   "Unknown pay/joining/location score 0.5.")

    with st.expander(f"Ruled out ({len(inelig)}): rejection reasons"):
        if inelig:
            df = pd.DataFrame([{"ID": m.candidate.id, "Name": m.candidate.name, "Locality": m.candidate.raw_locality,
                                "Rules failed": ", ".join(b for b, _ in m.blockers),
                                "Why": "; ".join(msg for _, msg in m.blockers)} for m in inelig])
            st.dataframe(df, hide_index=True, width="stretch")

    with st.expander("Incomplete profiles nearby: call to complete"):
        inc = E.incomplete_near(did, R, assigned)
        st.dataframe(pd.DataFrame([{"ID": x.id, "Name": x.name, "Phone": phone(x.phone), "Locality": x.raw_locality,
                                    "km": None if km is None else round(km, 1), "Missing": ", ".join(x.missing)}
                                   for x, km in inc]), hide_index=True, width="stretch")

# ---------- Assignments ----------
with tab_assign:
    rows = S.assignment_rows()
    if not rows:
        st.info("No assignments yet. Assign candidates from the Shortlist tab.")
    for cid, did_, at in rows:
        cand, dem = E.candidates.get(cid), E.demands.get(did_)
        a, b = st.columns([7, 1])
        a.write(f"**{cand.name if cand else cid}** ({cid}) → **{did_}** "
                f"{dem.employer + ' · ' + dem.role_title + ' · ' + dem.raw_locality if dem else ''}  \n"
                f"<span class='muted'>assigned {at}</span>", unsafe_allow_html=True)
        b.button("Unassign", key=f"ua_{cid}", on_click=do_unassign, args=(cid,))

# ---------- Unfilled report ----------
with tab_unfilled:
    st.markdown("Every open demand with unfilled positions: what blocks candidates, and the single change "
                "that would unlock more. *No auto-fill: assignments stay a human decision.*")
    matrix = []
    for d in open_d:
        dg = DIAG[d.id]
        matrix.append({"Demand": d.id, "Role": d.role_title, "Remaining": dg["remaining"], "Eligible": dg["eligible"],
                       "Short": dg["short"], **{lbl: dg["blockers"].get(k, 0) for k, lbl in
                                                (("shift", "Shift"), ("education", "Education"),
                                                 ("role", "No experience (skilled)"), ("distance", "Too far"),
                                                 ("pay", "Pay too high"), ("joining", "Joins too late"))}})
    st.markdown("**Rejection reasons by demand** (number of candidates failing each rule; one candidate can fail several)")
    st.dataframe(pd.DataFrame(matrix), hide_index=True, width="stretch", height=36 * (len(matrix) + 1) + 4)
    short = [d for d in open_d if DIAG[d.id]["short"]]
    if not short:
        st.success("Every open demand has enough eligible candidates to fill its remaining positions.")
    for d in short:
        dg = DIAG[d.id]
        with st.container(border=True):
            st.markdown(f"**{d.label}**: {dg['remaining']} open, {dg['eligible']} eligible → "
                        f"**short by {dg['short']}**")
            for t in dg["tips"]:
                st.write("• " + t)

# ---------- Candidates ----------
with tab_cand:
    q2 = st.text_input("Find a candidate (name, ID, last 4 phone digits, locality)", key="q_cand")
    pool = list(E.candidates.values())
    if q2:
        ql = q2.strip().lower()
        pool = [c for c in pool if ql in c.name.lower() or ql in c.id.lower() or c.phone.endswith(ql)
                or ql in c.raw_locality.lower()]
    st.caption(f"{len(pool)} candidate(s)")
    if pool:
        cid = st.selectbox("Candidate", [c.id for c in pool], key="cand_pick",
                           format_func=lambda i: f"{i} · {E.candidates[i].name} · {E.candidates[i].raw_locality}")
        c = E.candidates[cid]
        a, b = st.columns([1, 2])
        with a:
            st.markdown(f"### {c.name}\n{c.id} · {phone(c.phone)}")
            st.write(f"**Status:** {c.status}" + (f" · **assigned to {assigned[cid]}**" if cid in assigned else ""))
            st.write(f"**Home:** {c.raw_locality}" + (f" → {c.locality}" if c.locality != c.raw_locality else ""))
            st.write("**Experience:** " + (", ".join(f"{t} ({m} mo)" for t, _, m in c.roles) or "unknown"))
            st.write(f"**Expected pay:** {'₹{:,}'.format(c.pay) if c.pay else 'unknown'} · "
                     f"**Shifts:** {', '.join(sorted(c.shifts)) or 'unknown'}")
            st.write(f"**Joins:** {c.join or 'unknown'} · **Education:** {c.edu or 'unknown'}")
            if c.dup_ids:
                st.write(f"**Merged duplicates:** {', '.join(c.dup_ids)}")
            if c.missing:
                st.warning("Missing: " + ", ".join(c.missing) + (" (needs profile; not ranked)" if c.needs_profile else ""))
        with b:
            st.markdown("**Fit against every open demand**")
            ev = [E.evaluate(c, d, W, R) for d in open_d]
            st.dataframe(pd.DataFrame([{"Demand": d.id, "Role": d.role_title, "Site": d.raw_locality,
                                        "Eligible": "✅" if m.eligible else "❌", "Score": m.score,
                                        "Why / blockers": m.reason if m.eligible else "; ".join(x for _, x in m.blockers),
                                        "Flags": "; ".join(m.flags)}
                                       for d, m in sorted(zip(open_d, ev), key=lambda x: (not x[1].eligible, -x[1].score))]),
                         hide_index=True, width="stretch")
            if c.status != "available":
                st.caption("Note: this candidate's status keeps them off shortlists.")

# ---------- Data quality ----------
with tab_data:
    rankable, incomplete, excluded = E.pool(R)
    c = st.columns(4)
    c[0].metric("Unique candidates", len(E.candidates))
    c[1].metric("Rankable", len(rankable))
    c[2].metric("Incomplete profiles", len(incomplete))
    c[3].metric("Excluded by status", sum(excluded.values()))
    st.write("Excluded by status: " + ", ".join(f"{k} {v}" for k, v in excluded.items()))

    st.markdown("**Candidate verification flags** (rankable candidates)")
    flag_rows = []
    for cnd in rankable:
        f = []
        if cnd.dup_ids:
            f.append("merged duplicate")
        if cnd.missing:
            f.append("missing " + ", ".join(cnd.missing))
        if cnd.loc_how == "fuzzy":
            f.append("approximate location")
        if cnd.status == "not_reachable":
            f.append("not reachable: call first")
        if cnd.edu is None:
            f.append("education unverified")
        if f:
            flag_rows.append({"ID": cnd.id, "Name": cnd.name, "Flags": "; ".join(f)})
    st.dataframe(pd.DataFrame(flag_rows or [{"ID": "", "Name": "", "Flags": "none"}]), hide_index=True, width="stretch")
    st.caption("Demand-specific flags (pay gap to negotiate, joining late, unknown shift) appear on each shortlist card.")

    st.markdown("**Data cleaning log**")
    issues = pd.DataFrame(E.issues, columns=["Type", "Record", "Detail"])
    kinds = st.multiselect("Show", sorted(issues["Type"].unique()), default=["duplicate", "demand", "locality"])
    st.dataframe(issues[issues["Type"].isin(kinds)], hide_index=True, width="stretch")
