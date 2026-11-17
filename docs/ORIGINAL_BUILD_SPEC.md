> **Historical.** This is the original prototype brief. Its scoring model (role 35 / distance 25 / pay 20 / joining 10 / experience 10) has been **superseded** by the final model in README.md (experience 40 / proximity 25 / salary 20 / joining 10 / completeness 5). Storage is now Postgres in the cloud, SQLite locally.

Build a candidate–job matching tool for a Bengaluru blue-collar staffing ops lead (Meena). Python + Streamlit + SQLite. Data is in ./data: demands.json (15), candidates.json (200), localities.json (60, lat/long). Data is messy on purpose. Work in the 4 stages below, run the checks at the end of each stage, and show me the result before moving on. Keep the code small: matcher.py (engine, pure functions), db.py (SQLite), app.py (UI), tests/test_matcher.py. Put every tunable in two dicts, DEFAULT_WEIGHTS and DEFAULT_RULES, so later changes are one-line edits. Do NOT build auto-fill.

## Stage 1 — Clean the data (matcher.py)
- Pay: accept int, "19,000", "15.5k", "Rs. 22000", "18500", null → int or None.
- Phone: digits only, last 10. **Deduplicate candidates by phone, never by name** (several different people share names). Keep the more complete record, store merged ids in `dup_ids`. Expect 200 → 196 (C077→C002, C182→C051, C187→C067, C118→C076).
- Education ladder: below_10=0, 10th/SSLC/"10th pass"=1, ITI=1.5, 12th/PUC/"12th pass"=2, diploma=2.5, graduate=3. Rule toggle `iti_counts_as_12th` (default False) makes ITI=2.
- Locality: lowercase, strip suffixes ("5th Block", "2nd Stage", "6th Phase", "Sector 2", "Main Road", "Bridge", "Industrial Area"), compare on letters only, then an explicit alias map {yesvantpur, yeshwantpur → Yeshwanthpur; kormangala → Koramangala; marathalli → Marathahalli; btm → BTM Layout; krishnarajapuram → KR Puram; bommanhalli → Bommanahalli}, then difflib fuzzy at cutoff 0.85 as a last resort (flagged). Bommanhalli must NOT map to Bommasandra. Applies to demand sites too ("Whitefield Main Road", "BTM", "Peenya Industrial Area").
- Role titles → canonical role → family:
  picker (Picker, Warehouse Picker), packer (Packer, Packing helper, Packaging staff), picker_packer (Picker/Packer), warehouse_general (Warehouse Associate, Godown helper) → family warehouse;
  rider (Rider, Delivery Rider, Delivery Partner, Delivery boy, Bike delivery executive) → delivery;
  security (Security, Security Guard, Guard, Watchman) → security;
  barista (Barista, Barista / Counter, Coffee maker), cafe_general (Cafe staff) → cafe;
  housekeeping (Housekeeping, HK Attendant, House keeping staff), cleaner (Cleaner) → housekeeping;
  forklift (Forklift Operator), reach_truck (Reach truck operator) → forklift.
  Exact match = same canonical role, or picker_packer vs picker/packer, or reach_truck vs forklift when toggle `reach_truck_is_forklift` is on. Related = same family.
- Status: only `available` is ranked. `deployed` and `offer_accepted` are never shown. `not_reachable` is shown (flagged "call first") only when toggle `show_not_reachable` is on.
- Profile completeness: fields = experience, pay, shifts, joining date, education. Missing ≥3 → "needs profile": never ranked, listed separately as "call to complete", nearest first (there are 30). Missing ≤2 → ranked on what's known; unknown components score 0.5 and are flagged.
- Demands: headcount 0 (D15) = closed. A start date in the past (D13) = treated as today ("ASAP") and flagged.
- Keep a data-quality log (type, record, detail) of every fix for display.
Check: all localities resolve, all titles/education values recognised, 196 unique, 143 rankable, 30 incomplete, 23 excluded by status.

## Stage 2 — Match and score (matcher.py)
Hard filters (rule out; record every blocker with a plain message):
- shift not in candidate's shifts (unknown shifts → allowed, flagged)
- education below the demand minimum (unknown → allowed, flagged "education unverified")
- skilled role (`skilled_roles` = forklift, rider, barista) and no exact/related experience; other roles accept freshers
- distance > `max_km` (25) — haversine between locality coordinates
- expected pay − offered > `max_pay_over` (3000)
- joining date − start > `max_days_late` (7)

Component scores 0..1:
- role: exact 1.0, related 0.6, none 0
- experience: (exact months + 0.5 × related months) / 24, capped at 1
- distance: 1 − km/max_km (unknown location 0.5)
- pay: gap ≤ 0 → 1; else 1 − gap/max_pay_over (unknown 0.5). Gap > 0 is flagged "pay gap ₹X — negotiate"
- joining: on/before start → 1; else 1 − days_late/(max_days_late+1) (unknown 0.5)

Score = 100 × Σ(wᵢ·sᵢ) / Σwᵢ, 1 decimal. DEFAULT_WEIGHTS = role 35, distance 25, pay 20, joining 10, experience 10.
Reason = phrases built from the same components, in order: "4.4 km away" (or "same locality"), "14 months as Warehouse Picker" (candidate's own title, "(related)" if family-only, or "no X experience"), "expects ₹500 less than / exactly / ₹2,000 more than the pay", "can join by the start date" / "can join 3 days after start". Flags are separate (pay gap, merged duplicate, missing fields, approximate location, not reachable).
shortlist(demand, weights, rules, assignments) → (eligible sorted by score desc then km, ineligible). Skip candidates assigned to other demands.
diagnose(demand) → headcount, filled, remaining, eligible, short = remaining − eligible, blocker counts. Only when short > 0, list single-change suggestions computed from candidates blocked by exactly one rule: pay ("raising pay to ₹X brings k within tolerance"), distance ("widening radius to N km adds k"), joining, education, shift, experience, plus "N incomplete profiles within max_km — call them".
Check: D01 #1 = C020; D05 and D06 share the same 5 eligible people (C034, C061, C101, C138, C160); C052 blocked for D09 only by pay; D09 short ≈7 with a pay tip.

## Stage 3 — Store (db.py)
SQLite `assignments(candidate_id TEXT PRIMARY KEY, demand_id, assigned_at)` — the primary key makes double-booking impossible. assign() runs in BEGIN IMMEDIATE, refuses if the candidate is already assigned (message names the demand) or the demand is full (count ≥ headcount; headcount 0 always refuses). unassign(). `settings(key, value JSON)` for Meena's weights and rules. DB path from env MATCHER_DB (default matcher.db).

## Stage 4 — UI (app.py, Streamlit)
- Sidebar "Scoring weights": 5 sliders 0–100. Values load from settings if Meena saved her own, else DEFAULT_WEIGHTS. On change → save. Caption shows "Using Meena's weights" vs "Using default weights". "Reset weights to default" deletes the saved key. Same pattern for Rules: max km, max ₹ over, max days late, skilled roles multiselect, the three toggles, reset button.
- Tab Shortlist: demand selector; metrics (pay, start with "passed → ASAP", min education, headcount, filled, remaining, eligible); Assigned list with Unassign; unfilled report expander (open when short); ranked list top N with rank, score, name/id/phone/locality, reason, flags, Assign button (disabled when full; key `as_<candidate_id>`); expanders for score breakdown (component × 100 per weight), incomplete profiles nearby, ruled-out candidates with reasons.
- Tab Demand board: every demand with headcount, filled, remaining, eligible pool, status (Closed / Filled / Short by N / Fillable).
- Tab Data quality: counts (unique, rankable, incomplete, excluded by status) and the fix log, filterable by type.
Cache the Engine with st.cache_resource. Cache the Store keyed by DB path.

## Tests (tests/test_matcher.py, pytest, pin today = 2026-11-17)
Cover: pay/education/locality parsing (incl. Bommanhalli), dedupe 196 + same-name kept, every status/shift/education/skilled-role/distance/pay/joining rule (both sides of each threshold), incomplete profiles excluded, half-empty profile ranked with 0.5 components, score bounds and ideal = 100, closer ranks higher, reason text matches numbers, distance-only weights sort by km, all-zero weights don't crash, reach-truck toggle 0.6→1.0, the real-data expectations listed in Stage 2, no double-booking, no over-fill, persistence across a new Store, unassign frees, closed demand refuses, settings reset. UI via streamlit.testing.v1.AppTest: weight slider saves and survives a re-run; reset restores; clicking `as_C020` persists an assignment.
Finish by running `pytest -q` and `streamlit run app.py`, and tell me anything that failed.
