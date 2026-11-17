# How to verify the prototype

## Run
```
pip install -r requirements-dev.txt
pytest -q                 # 83 checks, IDs T01–T43 below (T41–T43 need TEST_DATABASE_URL, else skipped)
streamlit run app.py      # opens http://localhost:8501
```
Delete `matcher.db` (local SQLite) to start from a clean slate (no assignments, default weights).
Expected values below assume today = 17 Nov 2026 and default weights/rules.

## Automated (pytest)
| ID | What it proves |
|---|---|
| T01 | Pay parsing: `19,000`, `15.5k`, `Rs. 22000`, `"18500"`, `17k`, null, garbage |
| T02 | Education: SSLC→10th, PUC→12th, "12th pass"→12th, ITI/iti, diploma |
| T03 | 20 locality variants resolve correctly; **Bommanhalli → Bommanahalli, not Bommasandra**; every locality in data resolves |
| T04 | Dedupe by phone: 200 → 196 (C077, C182, C187, C118 merged); same-name/different-phone kept separate |
| T05 | Every job title and education value in data is recognised |
| T06 | Deployed / offer-accepted never shown; not-reachable only when toggle on |
| T07 | Shift is a hard filter |
| T08 | Education ladder: 10th and ITI fail a 12th requirement; diploma passes; "ITI counts as 12th" toggle works |
| T09 | Skilled roles (forklift/rider/barista) need experience; housekeeping accepts freshers |
| T10 | Commute over max km → ruled out; raising max km lets them in |
| T11 | Expects ₹2,000 more → shown, flagged, penalised; ₹3,500 more → ruled out |
| T12 | Joining 3 days late → shown, penalised; 10 days late → ruled out; D13's past start treated as today |
| T13 | 30 empty profiles never ranked; listed nearest-first under "call to complete" |
| T14 | Half-empty profile (pay + date missing) ranks on what's known, neutral 0.5 on unknowns, flagged |
| T15 | Scores within 0–100; ideal candidate = 100 |
| T16 | Closer candidate outranks identical farther one |
| T17 | Reason text matches the underlying numbers |
| T18 | Meena's weights change the ranking (distance-only = sorted by km); all-zero weights don't crash |
| T19 | Reach-truck toggle upgrades C101 from related (20 effective months → 0.83) to exact (1.0) for forklift |
| T20 | Real data: D01 #1 = C020; D05 and D06 compete for the same 5 people; D09 short with a pay tip; no tips when fillable |
| T21–T25 | No double-booking, no over-fill, survives restart, unassign frees, closed demand refuses |
| T26 | Reset deletes Meena's weights → defaults used |
| T27 | UI: moving a weight slider saves it; survives refresh; Reset restores defaults |
| T28 | UI: Assign button persists across refresh; Filled = 1 |
| T29 | Default weights are the final model 40/25/20/10/5; old `role` factor gone; 24-month cap |
| T30 | Experience: 12 mo → 0.5, 24 → 1, 60 → 1 (cap), related = half credit, unrelated = 0 |
| T31 | Hand-computed score: C020 for D01 = 40 + 25×(1−3.5/25) + 20 + 10 + 5 = 96.5, exact reason text |
| T32 | Per-factor points add up to the score for every eligible candidate on every demand |
| T33 | Completeness factor: full profile 1.0, 3/5 fields 0.6 and stated in the reason |
| T34 | Ranking is deterministic and independent of input order |
| T35 | 200 candidates and 15 demands load; 196/143/30/23 pool split; pays are ints; phones are 10 digits |
| T36 | Haversine distances are sane (C020 → D01 ≈ 3.5 km) |
| T37 | 8 concurrent users assigning to a 4-seat demand: exactly 4 succeed (SQLite) |
| T38 | Committed candidate data is anonymised; private data and secrets are git-ignored |
| T39 | UI: Unassign works; weights saved under the old model are ignored |
| T40 | UI: all 6 tabs render; full demand disables Assign; candidate search; no full phone number on the page |
| T41 | Postgres: no double-booking, no over-fill, closed refuses, persists across connections, unassign, settings reset |
| T42 | Postgres: concurrent over-fill race and same-candidate race on two demands are both safe |
| T43 | UI on Postgres: Assign persists across a fresh app session |

## Manual checks in the browser (5 minutes)
1. **Shortlist D01** (NovaMart picker, Whitefield). #1 should be **C020** with a score of 96.5: "3.5 km away, 37 months as Picker/Packer, expects ₹2,000 less than the pay, can join by the start date". Points: Exp 40 · Prox 21.5 · Pay 20 · Join 10 · Data 5. (Names in the public data are synthetic.)
2. **Custom weights.** Drag *Geographical proximity* to 100 and everything else to 0. The sidebar says "Using saved custom weights" and the list re-sorts by km. Refresh the page — the weights stay. Click **Reset weights to default** — order returns to step 1.
3. **Score breakdown** expander: each column × weight explains the score.
4. **Assign** C020 to D01 → Filled 1, Remaining 11. Switch to D02: C020 is gone from that list. Refresh: still assigned. **Unassign** → back in D02.
5. **Over-fill.** D05 (headcount 4): assign 4 people → Assign buttons disable. 
6. **Contested candidates.** D05 and D06 both show C034, C061, C101, C138, C160. Assign C034 to D05 → it disappears from D06.
7. **Reach-truck toggle.** On D05 turn on "Reach-truck operator = forklift operator" → reach-truck operators (e.g. C138, C160) gain full experience credit and lose "(related)".
8. **Pay tolerance.** D07: C049 shows "expects ₹500 more" with a ⚠ negotiate flag. Set "Max ₹ above offered pay" to 0 → C049 disappears into "Ruled out".
9. **Unfilled report.** D09 (Devanahalli guards, ₹13,000): short by ~7. Tips name the pay level and radius that would unlock candidates. C052 (lives in Devanahalli) appears in "Ruled out" only for pay.
10. **Education.** D07 Barista (12th): turn on "ITI counts as 12th" → eligible count rises.
11. **Closed / stale demands.** D15 shows "closed", Assign disabled. D13 shows "Start 05 Oct — passed → ASAP".
12. **Incomplete profiles** expander on any demand: 30 people with name/phone/locality only, nearest first.
13. **Demand board** tab: all 15 demands with Filled / Remaining / Eligible pool / Status.
14. **Data quality** tab: 4 duplicates merged, locality fixes, pay strings parsed, D13/D15 notes.

## What is deliberately not built
Auto-fill across all demands (left out on request). The diagnosis per demand already lists the blocking rules, so adding auto-fill later only needs a greedy pass: the most constrained demand picks first.
