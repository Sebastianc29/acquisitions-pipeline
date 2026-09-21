# Acquisitions Pipeline

One dataset, five views. Replaces both the 170-tab spreadsheet and the deck
that somebody rebuilds by hand every Monday.

**Live app:** _paste your Streamlit URL here after deploying_

---

## Sign in

Every account below uses the password **`profood2026`**. They exist to show
the permission model, which is what the brief asks for — in production this
would be an activation email per user or SSO against the company directory.

| Role | Email | What it can do |
|---|---|---|
| `admin` | felipe.cardona@acme-re.com | Everything, and sees org-wide activity |
| `lead` | ana.restrepo@acme-re.com | Confirm and unlock values; sees their team's activity |
| `analyst` | marco.villa@acme-re.com | Edit and assume, cannot confirm; sees only their own activity |
| `viewer` | tom.becker@acme-re.com | Read-only; edit mode is disabled |

There are 22 seeded users across 4 roles. Passwords are stored only as
PBKDF2-SHA256 hashes with a per-user salt — no plaintext exists on disk or
in the deployment secrets.

**Worth trying:** sign in as `tom.becker` and then as `felipe.cardona`, and
compare the *Usage & permissions* page. The brief says "logins by person who
is logged in", so the audit panel itself honours the permission model: a
viewer sees their own activity, a lead sees their team's, an admin sees
everything. An audit log anyone can read in full would contradict the
system it documents.

---

## The five views

**Operational** — every deal from every sheet in one filterable, editable
table. Filters by client, vertical, market, city, state, funnel stage, and
price/power ranges. Amber cells are assumptions; confirmed values are
locked. Numeric filters keep rows *without* a value by default, because the
records with gaps are the ones that matter most.

**Weekly review** — grouped by vertical, formatted to be read out loud,
mirroring the shape of `target_weekly_view`. Exports to PDF. This is the
step that used to be manual.

**Analysis** — five charts, each answering one question, each with its
underlying table. Where the pipeline is sitting, price per square foot
against the median, list vs. negotiated price, completeness by deal, and
where the unconfirmed values are concentrated.

**AI findings** — Claude reads the pre-computed aggregates and flags what
deserves attention before a Monday meeting. Every figure it cites is checked
against the numbers this pipeline produced; anything unverifiable is
withheld and shown as such.

**Usage & permissions** — logins over time, activity per person, the role
matrix, the audit log with CSV export, and the status of the last data
refresh including any conflicts.

---

## How the data gets there

```
Google Sheets (live)
   │  ingest.py      raw values + font colours   (a CSV export loses the colours)
   │  normalize.py   statuses, types, headers, derived fields
   │  extract.py     free-text notes -> columns  (regex + Claude)
   │  nulls.py       not_yet_known vs missing
   │  unify.py       three sheets -> one dataset
   ▼
 data/deals.db  ──►  app.py
```

`python etl.py` runs the whole chain in one command. In the app it is the
**Refresh from Sheet** button.

### Why edits survive a refresh

`deals` mirrors the Sheet and is rewritten on every run. Human work —
assumptions, confirmations, corrections — lives in a separate
`deal_overrides` table the ETL never touches. What you see is one laid over
the other. That separation is what lets a scheduled refresh run every
morning without destroying the team's work. When the Sheet changes a field
somebody had edited, it is recorded as a conflict rather than silently
resolved.

---

## Running it locally

```bash
pip install -r requirements.txt

python validate_schema.py     # checks the canonical schema
python etl.py                 # builds data/deals.db
python crear_auth.py          # generates credentials from the users table
streamlit run app.py
```

### Configuration

| File | Contents | In the repo? |
|---|---|---|
| `config/schema.yaml` | Canonical schema, status mapping, expectations matrix | yes |
| `config/sheets.json` | `{"spreadsheet_id": "..."}` | no |
| `config/google_credentials.json` | Google service-account key | **no** |
| `config/auth.yaml` | Users and password hashes | **no** |
| `.env` | `ANTHROPIC_API_KEY=sk-ant-...` | **no** |

Without Google credentials the pipeline falls back to the `.xlsx` in
`data/`. Without an Anthropic key, note extraction runs on regex alone and
the AI findings view explains that it is disabled. Neither is fatal.

---

## Deploying

Streamlit Community Cloud, pointed at this repo. Secrets go in
**Settings → Secrets**, never in the repository:

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
SPREADSHEET_ID = "..."

[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "...@....iam.gserviceaccount.com"
client_id = "..."
token_uri = "https://oauth2.googleapis.com/token"

[auth]
# paste the contents of config/auth.yaml, converted to TOML tables.
# Hashes only.
```

`settings.py` reads st.secrets first, then the environment, then local
files, so nothing else in the code changes between laptop and deployment.
On a cold start the app builds the database from the live Sheet by itself.

---

## What the code is made of

| File | Role |
|---|---|
| `config/schema.yaml` | Every judgement call, in one readable place |
| `settings.py` | Credentials, from secrets or files |
| `readers.py` | Two source readers behind one interface: Sheets and `.xlsx` |
| `ingest.py` | Step 1 — raw load plus the font-colour scan |
| `profiling.py` | Step 2 — nine quality profiles. Changes nothing |
| `normalize.py` | Step 3 — normalisation and post-validation |
| `extract.py` | Step 4 — notes to columns, with hallucination checks |
| `nulls.py` | Step 4.5 — classifies every gap |
| `unify.py` | Step 5 — one dataset, deterministic ids |
| `db.py` | SQLite, the overrides layer, the audit log |
| `etl.py` | The whole chain, one command |
| `insights.py` | AI findings, with figure verification |
| `auth.py` / `app.py` | Permissions and the interface |

Each step has a `verificar_*.py` that checks it: `python verificar_paso1.py`,
`python verificar_paso5.py`. `python profiling.py` writes a full data-quality
report to `reports/perfilado.md`.

---

## Decisions worth knowing about

**Assumptions are data, not formatting.** The source sheet marks unconfirmed
values by colouring the text — a signal that dies the moment anyone exports
to CSV. The pipeline reads those colours and turns them into a field that
can be filtered, counted and confirmed with a named source.

**Nothing is imputed.** No averages fill gaps. A missing price stays missing;
what changed is that we now know which gaps matter, based on the deal's
funnel stage, property type and whether it is a sale or a lease.

**`market` is not the city.** In the source, `Metro` is the client's search
mandate, not where the property sits: one tab reads `Metro = Atlanta` for
properties in Culver City, Torrance, Miami and San Diego. Both are kept.

**Outliers are kept.** A retail unit at $1,139/SF in Culver City and a
listing 423 days on market are flagged for review, not corrected. A real
value that looks strange is still a real value.

**The AI never invents a number.** In note extraction it must quote the
fragment it read, and the code verifies that fragment exists in the source
text. In AI findings it receives only pre-computed aggregates and every
figure it cites is checked against them.