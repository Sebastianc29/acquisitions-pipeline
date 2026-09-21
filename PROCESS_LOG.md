# Process log

What I asked the AI for, what I had to redirect, and what I assumed about
the messy data.

**Time:** roughly 5 hours across two sittings, including breaks. Planning and
data exploration on day one; building, deploying and polishing on day two.
Timestamps in the working session run from about 13:20 to 17:55 on the
second day.

**Tools:** Claude (Sonnet/Opus) as the coding partner throughout, Claude
Haiku via the Anthropic API inside the pipeline for note extraction, Claude
Sonnet via the API for the AI findings view.

---

## How I worked with the AI

I did not start by asking for code. I started by asking it to **criticise my
plan**, and that changed the shape of the project twice before a line was
written.

My original plan was: notebook → load each tab → clean → merge → push to an
HTML page. The two objections that landed were that a notebook generating
static HTML cannot satisfy the brief (point 1 asks for an *editable* table
and point 3 asks for *logins and permissions*), and that the assumption
data the brief mentions is encoded in **font colour**, which disappears the
moment anything touches a CSV. Both were right, and both would have cost me
hours to discover by building the wrong thing first.

From there the working rhythm was fixed and I kept it for the whole project:

1. Ask for a detailed plan for the next step, with expected output.
2. Approve it, or push back.
3. Get the code plus a verification script.
4. Run both on my machine and paste the actual output back.
5. Fix whatever the output revealed before moving on.

Step 5 mattered more than I expected. Four separate defects surfaced this
way, described below. None of them would have appeared in a review of the
code; all of them appeared the moment real output was compared against
expectations.

---

## What I had to fix or redirect

**The AI proposed generating missing values with a model.** I asked whether
we could use the API to invent plausible numbers for empty fields and flag
them as assumptions. The pushback was firm and I agreed with it: a model
producing "480A" for a warehouse in Atlanta creates a number with no source,
and even flagged it enters the pipeline and eventually gets repeated to a
client. We kept the API for something defensible instead — *disambiguating*
values that are already written in the notes.

**A validator found a hole in the AI's own schema.** The schema validation
script for step 0 reported that the `closed` stage had no expectations
matrix, meaning a closed deal would never raise an alert for missing fields.
The schema had been written by hand minutes earlier and the error was not
visible by reading it.

**The post-validation caught a wrong classification.** Step 3 classified any
row without an address as a "market search". The validation flagged
`catering!5`: no address, but 16,072 SF and $4,018,000. That is a real
property with a missing address. Left uncorrected, its gaps would have been
classified as "not applicable yet" and a $4M deal would have vanished from
the alerts. The rule now looks at every signal that a property exists, not
just the address.

**The AI findings view crashed on first run.** `'ThinkingBlock' object has no
attribute 'text'`. Extended-thinking models return a reasoning block before
the text block, and the code read `content[0].text`. The note-extraction
step never hit this because it uses Haiku, which does not emit that block.
Fixed in both places rather than only where it broke.

**One bug class appeared three times.** `NaN` is truthy in Python, so
`row.get("address") or "—"` returns the string `'nan'`, and slicing it
raises. It showed up in the gap classifier, in the analysis labels and in
the PDF export before I stopped patching instances and wrote a single helper
that is now the only way the code reads a possibly-empty field.

**The charts rendered empty.** Bars and dots appeared as hollow outlines.
The colour scale declared its domain with internal keys (`food`) while the
data carried display names (`Food`); with nothing to match, Altair fills
nothing. Not a styling problem — a data problem wearing a styling costume.

---

## What the data told us

The profiling step (`reports/perfilado.md`) is the evidence behind every
decision below.

**13 status spellings across 3 tabs collapse to 7 funnel stages.** `nego PSA`,
`SOURCING`, `in pipeline`, `Under Contract`. Two required judgement:
`Under Contract` and `In Contract` are the same stage under two names, and
`Listed` does not exist in the brief's funnel at all — its only row has the
note "market prep", so it maps to `In Pipeline` and is flagged as a
debatable mapping.

**`Metro` is not the property's location.** `av_clientC_atl` reads
`Metro = Atlanta` for properties in Culver City, Torrance, Miami and San
Diego. It is the client's search mandate. `target_weekly_view` uses this
field as its "Market" column, so treating it as the city would have made the
weekly view disagree with the one the team produces by hand.

**Exactly one cell carries the colour convention.** `av_clientC_atl!O2`,
`Power = 400A`, in blue. One coloured cell among roughly 400 with values. I
took the minority colour to mean "assumed" — if blue meant *confirmed*, the
team would be saying almost their entire pipeline is guesswork, which does
not square with notes like "closing 11/25, deposit $250,000".

**The real prices are in the notes, not the columns.** `list_price` says
$3,325,000; the note says "agreed to $3M". Another says "best and final
$5.75M" against a list of $5,975,000. A third says "countered at $3.25M, we
responded at $3.05M" — two amounts where only the second is live. A partner
in a Monday meeting wants the negotiated number, and it exists nowhere as a
column. This is what the LLM extraction is for.

**Gaps are explained by three things, not by randomness.** Funnel stage (a
Sourcing record has no property yet), property type (a land parcel has no
building square footage), and deal type (a lease has no sale price — one row
turned out to be a lease, discovered only by reading its note). Once those
three are accounted for, the gaps that remain are real and actionable.

**Outliers that are not errors.** $1,139/SF in Culver City is a small retail
unit in an expensive area. 423 days on market is a listing that has sat for
fourteen months. A building of 17,000 SF on a 13,068 SF lot means more than
one storey. All three are flagged for review and kept.

---

## Assumptions I made

- The copy of the spreadsheet has no config tab, so the vertical is derived
  from the sheet-name prefix. The mapping lives in `schema.yaml`, not in
  code, which is what a config tab would have given us.
- Dates in notes carry no year ("DD date 9/18"); the current year is assumed
  and every such date is flagged as assumed in the UI.
- `days_on_market` is days *listed*, not days in the funnel stage. The
  stretch goal asks for time-in-stage, which does not exist in the source; it
  is approximated and labelled as such, and the audit log records stage
  changes from now on so the real figure exists going forward.
- `contact_tracker` joins to deals by **phone**, not by name. Phone
  `404-555-0110` appears as broker "Sam Ortiz" in the deal and as
  "James Ford" in the tracker. The discrepancy is displayed, not resolved —
  guessing which name is right is not the pipeline's job.
- The 22 users and their login history are seeded, marked `source='seed'`
  in the audit log. The brief asks to assume more than 20 users; inventing
  them is the honest way to demonstrate the permission model.
- One shared demo password, stored as a PBKDF2 hash per user. In production
  this is an activation email or SSO. The brief evaluates the permission
  model, not the identity provider.

---

## A real incident worth reporting

Midway through, while editing my own copy of the Sheet, I overwrote cell A1
of the catering tab with the text `li`. The client column silently went
blank in the app and the filter dropped from three clients to two.

Nothing crashed. The header found no mapping, so its values were preserved
in the `extras` field rather than discarded, the `client` field came back
empty, and both the load report and the completeness validation flagged it.
Fixing it meant correcting one cell in the Sheet and pressing Refresh.

That is the behaviour the pipeline was designed for when the brief mentions
170 tabs maintained by people: a source that breaks should not take the tool
down, and should not swallow the damage in silence.

---

## Where the AI genuinely earned its place

**Disambiguating notes.** Regex extracted 2 of 5 amounts and *refused* the
other 3 because they were ambiguous — one had "deposit" beside it, one had
"rent", one had two figures. The LLM resolved all three correctly: the
deposit stayed a deposit, the annual rent became a lease, and "we responded
at $3.05M" won over the earlier counter. No regex resolves that.

**Cross-referencing for the findings view.** Given only aggregates, it
surfaced that two-thirds of pipeline value sits in a single funnel stage —
true, useful, and not visible in any single table.

**What I would not trust it with.** Any number it cannot point at. That is
why both AI features verify against the source: extraction checks the quoted
fragment exists in the note, findings check every cited figure against the
numbers Python computed. The withheld counter on the findings page is that
guardrail made visible.