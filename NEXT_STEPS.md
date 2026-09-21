# What I would do next, with more time

Ordered by what I would actually pick up first, not by how impressive it
sounds.

---

## 1. Close the loop back to the Sheet

Today the sync is one-way: the app reads the Sheet, and edits made in the
app stay in the app. That is a deliberate scope call — the service account
has read-only scope — but it leaves the team with two places where a value
can change.

Two ways forward, and I would ask the team which they want rather than
decide for them: write confirmed values back to the Sheet so it stays
authoritative, or retire the Sheet as the input and make the app the source
of truth. The second is the better end state and the harder conversation.

## 2. The scheduled refresh

The architecture already supports it — `deals` is rewritten while
`deal_overrides` survives, and conflicts are recorded rather than resolved —
but nothing runs on a timer yet. A GitHub Actions cron calling `etl.py` each
morning is about twenty lines. What needs care is the conflict UI: right now
conflicts are listed, but there is no workflow to resolve one.

## 3. Real identity, real security

The current login is deliberate scope, and its limits should be named:
sessions live in `st.session_state` with no expiry, there is no rate
limiting on failed attempts, no password reset, and one shared demo
password. For anything real: SSO against the company directory, session
expiry, lockout after repeated failures, and per-user secrets. The
permission model itself — roles in config, the audit log, the scoped audit
view — would carry over unchanged.

## 4. Time in stage, for real

The stretch goal asks how long deals have sat in their current stage. That
data does not exist in the source; `days_on_market` is days *listed*, which
is a different thing. The audit log now records stage changes, so the real
figure accumulates from today. In a month it becomes a genuine metric, and
the funnel chart can show where deals stall rather than only where they sit.

## 5. The map

`google-maps` on the property addresses, with demographics on hover. It is
the second stretch goal and I left it out on purpose: it is presentation,
and presentation before the core is solid is the trap the brief warns about.
The addresses are already clean and geocodable.

## 6. Make the analysis honest at scale

The charts currently carry a warning that 18 records support indications,
not conclusions. With the real 170 tabs that warning comes off, and other
things become possible: price per square foot by market rather than by
individual property, time-in-stage distributions, broker performance,
seasonality. The chart code would need paging and sampling; the current
version assumes everything fits on one screen.

## 7. Deepen the AI findings

Three things it cannot do yet. It sees one snapshot, so it cannot say "this
deal has not moved in three weeks" — that needs history, which point 4
produces. It has no market context beyond this pipeline, so it cannot say
whether $285/SF in Atlanta is good. And it cannot be asked a question; it
only reports. A "why is Food behind?" box over the same verified-aggregates
contract is the natural next feature.

## 8. Pay down the code

Honest assessment of what I would clean up:

- `profiling.py` and `normalize.py` repeat the same emptiness and
  column-inspection helpers. They belong in one shared module.
- The Spanish comments in the ETL modules should be English, matching the
  interface and the rest of the documentation.
- There are no unit tests. There are verification scripts per step, which
  caught real bugs, but they check outputs on one known file rather than
  behaviour on edge cases. `pytest` over the parsers — `'2,400A'`,
  `'15-51'`, the 13 status spellings — would be the first test file.
- `app.py` is around a thousand lines. The five views should be five modules.
- Every view recomputes from the full dataset on every rerun. Fine at 18
  records, wrong at 5,000. `st.cache_data` on the read, with proper
  invalidation on write.

## 9. Interface work

Column presets per role, so an analyst and a partner do not see the same
sixteen columns. Bulk edit for filling the same field across several deals.
Saved filter views. Keyboard navigation in the table. Mobile layout — the
weekly review is what a partner would open on a phone before a meeting, and
right now it assumes a desktop.

---

## What I would leave exactly as it is

Worth stating, because the temptation to "improve" these is real.

**No imputation.** The instinct to fill gaps with medians would return the
first time somebody complains about empty cells. It should be refused every
time.

**The AI verification layer.** Both AI features check their output against
the source and withhold what they cannot support. It costs a few lines and
it is the entire reason a model is allowed near this data.

**Overrides in a separate table.** The simplification of "just update the
row" will look tempting to whoever maintains this next. It is the one change
that would quietly destroy the team's work on the first scheduled refresh.