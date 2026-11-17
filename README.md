# Staffing Matching Engine

A Streamlit tool for a blue-collar staffing operations lead in Bengaluru. It ranks candidates for each employer demand, explains every score in plain language, records assignments safely, and shows why positions stay unfilled.

**There is deliberately no auto-fill.** Every assignment is a human decision.

## Features
| Area | What it does |
|---|---|
| Demand board | All 15 demands: headcount, filled, remaining, eligible pool, fill %, status (Closed / Filled / Short by N / Fillable) |
| Ranked shortlist | Eligible candidates per demand, sorted deterministically (score ↓, distance ↑, ID ↑), with search |
| Explanations | A sentence Meena can read to the employer: *"3.5 km away, 37 months as Picker/Packer, expects ₹2,000 less than the pay, can join by the start date"* |
| Score breakdown | Points per factor on every card and in a table. The points add up to the score |
| Editable weights and rules | Sidebar sliders, saved to the database, with reset-to-default |
| Assign / unassign | From the shortlist or the Assignments tab. Enforced in the database: one assignment per candidate, no over-fill, closed demands refuse |
| Persistent assignments | Postgres (`DATABASE_URL`) in the cloud, SQLite locally |
| Unfilled diagnostics | Per demand: blocker counts plus the single change that unlocks candidates (pay level, radius, start date, education, shift, trainees, incomplete profiles to call) |
| Rejection reasons | Ruled-out list per demand with every failed rule, plus a demand × rule matrix |
| Verification flags | Pay gap to negotiate, education unverified, merged duplicate, missing fields, approximate location, not reachable |
| Candidate search | Find anyone by name / ID / last 4 phone digits / locality and see their fit against every open demand |
| Data quality | Cleaning log (duplicates, locality fixes, pay strings, stale/closed demands) |

## Scoring model (final, v2)
Score = 100 × Σ(wᵢ·sᵢ) / Σwᵢ, where each factor score sᵢ is between 0 and 1.

| Factor | Weight | Factor score |
|---|---:|---|
| Relevant experience | 40 | (exact-role months + ½ × same-family months) / **24**, capped at 1 |
| Geographical proximity | 25 | 1 − km / max_km (haversine between locality centroids). Unknown = 0.5 |
| Salary compatibility | 20 | 1 if expectation ≤ offer, else 1 − gap / max_pay_over. Unknown = 0.5 |
| Joining readiness | 10 | 1 if joining by the start date, else 1 − days_late / (max_days_late + 1). Unknown = 0.5 |
| Data completeness | 5 | known fields / 5 (experience, pay, shifts, joining date, education) |

**Hard filters** (the candidate is ruled out, and the reason is recorded): wrong shift, education below the minimum, no relevant experience for a skilled role (forklift, rider, barista), commute > 25 km, expects more than ₹3,000 above the offer, joins more than 7 days after the start. Status `deployed` and `offer_accepted` are never shown. `not_reachable` is shown (flagged) only when that toggle is on. Profiles missing 3 or more of the 5 fields are not ranked; they're listed as "call to complete", nearest first.

**Normalisation:** pay strings (`"15.5k"`, `"Rs. 22000"`, `"19,000"`), phone formats (`+91 …`, leading 0), duplicates merged **by phone, never by name** (200 → 196), an education ladder (SSLC → 10th, PUC → 12th, ITI, diploma), locality spellings (suffix stripping, then aliases, then fuzzy matching; *Bommanhalli → Bommanahalli, not Bommasandra*), role titles → canonical role → family, past start dates treated as ASAP, headcount 0 treated as closed.

All defaults live in `DEFAULT_WEIGHTS` / `DEFAULT_RULES` in `matcher.py`.

## Project layout
```
app.py                 Streamlit UI (entry point)
matcher.py             engine: cleaning, filters, scoring, explanations, diagnostics (pure Python)
db.py                  storage: PostgresStore (cloud) / SqliteStore (local), same API
data/                  demands, localities, ANONYMISED candidates (synthetic names and phones)
scripts/anonymize.py   regenerates data/candidates.json from a raw export
tests/test_matcher.py  pytest suite (engine, storage, Postgres, Streamlit AppTest)
.streamlit/            config + secrets template
```

## Run locally
```bash
pip install -r requirements-dev.txt
pytest -q                       # Postgres tests run too if TEST_DATABASE_URL is set
streamlit run app.py            # SQLite file matcher.db (set MATCHER_DB to change)
```
To work with the **real** candidate export privately, put it in `data/private/` (git-ignored) and run with `MATCHER_DATA_DIR=data/private` and `SHOW_PHONES=true`.

## Deploy (Streamlit Community Cloud)
1. Create a free Postgres database (e.g. [Neon](https://neon.tech) or [Supabase](https://supabase.com)) and copy its connection string (`postgresql://…?sslmode=require`). The app creates its two tables on first run.
2. On [share.streamlit.io](https://share.streamlit.io): **Create app**, pick this repo, branch `main`, main file `app.py`, Python 3.12.
3. In **Advanced settings → Secrets**, paste:
   ```toml
   DATABASE_URL = "postgresql://USER:PASSWORD@HOST/DBNAME?sslmode=require"
   SHOW_PHONES = "false"
   ```
4. Deploy. The sidebar should say **"Storage: Postgres. Assignments are durable."**

Why not SQLite in the cloud? Community Cloud containers are ephemeral: the filesystem resets on every reboot, redeploy or sleep, so a SQLite file would silently lose assignments. Without `DATABASE_URL` the app still runs but shows a warning.

## Privacy
- The repo contains **no real candidate names or phone numbers**. `data/candidates.json` is anonymised by `scripts/anonymize.py`, and the matching results are identical to the raw data (verified for all 15 demands).
- Phones are masked (`••••••1234`) unless `SHOW_PHONES=true`.
- Secrets live only in Streamlit's secret store. `.streamlit/secrets.toml` and `data/private/` are git-ignored.

## Known limitations
- Distance is straight-line between locality centroids, not travel time.
- Settings (weights and rules) are shared by everyone using the app. There is no login (out of scope in the brief).
- "Today" is the server date, so demands with passed start dates are treated as ASAP.
- Contested candidates (e.g. the 5 forklift operators who fit both D05 and D06) are left to the operator. That's by design: no auto-fill.
