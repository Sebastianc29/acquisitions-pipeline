"""
Acquisitions pipeline — one dataset, five views.

    streamlit run app.py

Every view reads from db.leer_deals(). That function is the only door to
the data, which is what makes "two disconnected tools" impossible by
construction rather than by discipline.

  1. Operational   filterable, editable table with assumption locking
  2. Weekly review grouped by vertical, readable in a live meeting
  3. Analysis      patterns worth looking at, each chart with its table
  4. AI findings   Claude reads the aggregates and flags what matters
  5. Usage & roles activity and permissions — scoped to who is looking

The Refresh button runs the full ETL against the live Google Sheet. It
rewrites `deals` and preserves `deal_overrides`, so the team's edits
survive every refresh (see db.py).
"""

import json
from datetime import datetime, timedelta

import altair as alt
import pandas as pd
import streamlit as st
import yaml

import auth
import db
import etl
import insights

st.set_page_config(page_title="Acquisitions Pipeline", layout="wide",
                   page_icon="🏢")

SCHEMA = yaml.safe_load(open("config/schema.yaml", encoding="utf-8"))
FUNNEL = SCHEMA["funnel"]["order"] + SCHEMA["funnel"]["terminal"]
STAGE = {k: v["display"] for k, v in SCHEMA["status_map"].items()}
VERT = {k: v["display"] for k, v in SCHEMA["verticals"].items()}

# Validated categorical palette (see the dataviz skill): 3 slots, all-pairs
# clear on the light surface. The aqua slot sits below 3:1 contrast, so every
# bar carries a direct label — identity never depends on colour alone.
COLOURS = {"food": "#2a78d6", "av": "#eb6834", "catering": "#1baf7a"}
AMBER = "#eda100"

# The scale domain MUST match the values in the column. Declaring the internal
# keys ('food') while the data carries display names ('Food') leaves Altair
# with nothing to match, and every mark renders unfilled.
VERT_DOMAIN = [VERT[k] for k in COLOURS]
VERT_RANGE = [COLOURS[k] for k in COLOURS]
VERT_SCALE = alt.Scale(domain=VERT_DOMAIN, range=VERT_RANGE)
Y_AXIS = alt.Axis(labelFontSize=12, labelLimit=260, domain=False, ticks=False)

STYLE = """
<style>
  /* Theme-agnostic on purpose. Hard-coding backgrounds here made the cards
     white on a dark theme; Streamlit already exposes its surfaces, so this
     only touches rhythm, weight and borders. */
  .block-container { padding-top: 2.2rem; max-width: 1500px; }
  h1, h2, h3 { letter-spacing: -0.015em; font-weight: 650; }
  h3 { margin-bottom: .1rem; }
  h5 { font-size: .95rem; font-weight: 600; opacity: .85;
       margin: 0 0 .4rem 0; }

  div[data-testid="stMetric"] {
      background: var(--secondary-background-color, rgba(128,128,128,.07));
      border: 1px solid rgba(128, 128, 128, .22);
      border-radius: 10px; padding: .85rem 1rem;
  }
  div[data-testid="stMetricLabel"] p {
      font-size: .78rem; opacity: .7;
      text-transform: uppercase; letter-spacing: .04em;
  }
  div[data-testid="stMetricValue"] { font-size: 1.5rem; font-weight: 620; }

  section[data-testid="stSidebar"] {
      border-right: 1px solid rgba(128, 128, 128, .22);
  }
  section[data-testid="stSidebar"] .stButton button { width: 100%; }

  div[data-testid="stDataFrame"] { border-radius: 8px; overflow: hidden; }
  hr { margin: 1.1rem 0; opacity: .35; }
</style>
"""


# ===========================================================================
# Helpers
# ===========================================================================

def money(v):
    if v is None or pd.isna(v):
        return "—"
    v = float(v)
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:,.2f}M".replace(".00M", "M")
    if abs(v) >= 1_000:
        return f"${v / 1_000:,.0f}K"
    return f"${v:,.0f}"


def sqft(v):
    return "—" if v is None or pd.isna(v) else f"{float(v):,.0f} SF"


def empty(v):
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    if isinstance(v, str) and v.strip() in {"", "-"}:
        return True
    if isinstance(v, (list, dict)) and len(v) == 0:
        return True
    return False


def text(v, fallback="—"):
    """NaN is truthy in Python, so `row.get(x) or "—"` yields the string
    'nan' and slicing it blows up. This is the only safe way to read a
    possibly-empty field."""
    return fallback if empty(v) else str(v)


def deal_label(row):
    if not empty(row.get("address")):
        return str(row["address"])
    return f"{text(row.get('city'), 'unknown city')} (no address)"


def assumptions_of(row):
    v = row.get("assumptions")
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            v = []
    return list(v or [])


def trim_note(note, status):
    """target_weekly_view trims notes by dropping what repeats the stage:
    "full cooler, negotiating PSA - 60 day dd" -> "full cooler, 60 day dd"."""
    if empty(note):
        return "—"
    t = str(note)
    label = STAGE.get(status, "")
    for phrase in [f"{label} - ", f"{label}, ", label, "negotiating final"]:
        if phrase and phrase.lower() in t.lower():
            i = t.lower().find(phrase.lower())
            t = t[:i] + t[i + len(phrase):]
    t = t.replace(" - ,", ",").replace(", ,", ",").strip(" ,-")
    return " ".join(t.split())


def colour_by_vertical(legend=True):
    return alt.Color(
        "Vertical:N", scale=VERT_SCALE,
        legend=alt.Legend(title=None, orient="top", direction="horizontal",
                          symbolType="square", labelFontSize=12)
        if legend else None,
    )


def chart_with_table(title, chart, table, note=None, height=None):
    """Every chart ships with its table: accessibility (identity never rests
    on colour alone) and because an analyst wants to copy a number."""
    st.markdown(f"##### {title}")
    st.altair_chart(chart.properties(height=height) if height else chart,
                    use_container_width=True)
    if note:
        st.caption(note)
    with st.expander("View the data"):
        st.dataframe(table, use_container_width=True, hide_index=True)


# ===========================================================================
# Login
# ===========================================================================

def login_screen(conn):
    st.markdown("## Acquisitions pipeline")
    st.caption("Restricted access · activity is logged")

    col, _ = st.columns([1, 2])
    with col:
        with st.form("login"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Sign in", type="primary"):
                user = auth.autenticar(email, password, conn)
                if user:
                    st.session_state["user"] = user
                    st.rerun()
                else:
                    st.error("Invalid credentials. The attempt was logged.")
        with st.expander("First time?"):
            st.write("Run `python crear_auth.py` to generate credentials "
                     "from the users in the database.")


# ===========================================================================
# 1. OPERATIONAL
# ===========================================================================

TABLE_COLS = [
    "client", "vertical", "market", "city", "state", "address", "status",
    "list_price", "current_price", "bldg_sf", "lot_sf", "price_per_sf",
    "power_amps", "property_type", "broker", "completitud",
]

LABELS = {
    "client": "Client", "vertical": "Vertical", "market": "Market",
    "city": "City", "state": "State", "address": "Address", "status": "Stage",
    "list_price": "List price", "current_price": "Negotiated",
    "bldg_sf": "Bldg SF", "lot_sf": "Lot SF", "price_per_sf": "$/SF",
    "power_amps": "Power (A)", "property_type": "Type", "broker": "Broker",
    "completitud": "Complete %", "key_dates": "Key dates",
    "deposit": "Deposit", "rent_year": "Annual rent",
}

# Without explicit formats pandas prints 1100000.000000
FORMATS = {
    "List price": "${:,.0f}", "Negotiated": "${:,.0f}",
    "Bldg SF": "{:,.0f}", "Lot SF": "{:,.0f}", "$/SF": "${:,.0f}",
    "Power (A)": "{:,.0f}", "Complete %": "{:.0f}%",
}


def filters(deals):
    st.markdown("#### Filters")
    f1, f2, f3, f4 = st.columns(4)
    with f1:
        verticals = st.multiselect("Vertical",
                                   sorted(deals["vertical"].dropna().unique()),
                                   format_func=lambda v: VERT.get(v, v))
        clients = st.multiselect("Client",
                                 sorted(deals["client"].dropna().unique()))
    with f2:
        markets = st.multiselect("Market",
                                 sorted(deals["market"].dropna().unique()))
        cities = st.multiselect("City",
                                sorted(deals["city"].dropna().unique()))
    with f3:
        states = st.multiselect("State",
                                sorted(deals["state"].dropna().unique()))
        stages = st.multiselect(
            "Stage", [e for e in FUNNEL if e in set(deals["status"].dropna())],
            format_func=lambda e: STAGE.get(e, e))
    with f4:
        confirmed_only = st.toggle("Confirmed data only", value=False,
                                   help="Hides values flagged as assumptions")
        sites_only = st.toggle("Properties only", value=False,
                               help="Excludes market searches")

    st.caption("Numeric filters keep records WITHOUT a value by default — "
               "otherwise the rows with gaps, the ones that matter most, "
               "would silently disappear.")
    r1, r2, r3 = st.columns(3)
    ranges = {}
    for col, holder, label in [("list_price", r1, "List price"),
                               ("current_price", r2, "Negotiated price"),
                               ("power_amps", r3, "Power (A)")]:
        series = pd.to_numeric(deals[col], errors="coerce").dropna()
        with holder:
            if series.empty:
                st.caption(f"{label}: no data")
                continue
            lo, hi = float(series.min()), float(series.max())
            if lo == hi:
                hi = lo + 1
            sel = st.slider(label, lo, hi, (lo, hi), format="%.0f")
            keep = st.checkbox(f"include rows without {label.lower()}",
                               value=True, key=f"nn_{col}")
            ranges[col] = (sel, keep)

    d = deals.copy()
    for col, chosen in [("vertical", verticals), ("client", clients),
                        ("market", markets), ("city", cities),
                        ("state", states), ("status", stages)]:
        if chosen:
            d = d[d[col].isin(chosen)]
    if sites_only:
        d = d[d["record_type"] == "site"]
    for col, ((lo, hi), keep) in ranges.items():
        values = pd.to_numeric(d[col], errors="coerce")
        d = d[values.between(lo, hi) | (values.isna() & keep)]

    return d, confirmed_only


def paint_assumptions(df, marks):
    """Assumed cells in amber — the signal the team used to make by colouring
    text in the Sheet, now filterable and countable."""
    def style(row):
        flagged = marks.get(row.name, [])
        return [f"background-color: {AMBER}33; border-left: 2px solid {AMBER}"
                if c in flagged else "" for c in df.columns]
    fmt = {c: f for c, f in FORMATS.items() if c in df.columns}
    return df.style.apply(style, axis=1).format(fmt, na_rep="—")


def operational_view(conn, user, deals):
    st.markdown("### Operational view")
    st.caption(f"{len(deals)} records from {deals['source_sheet'].nunique()} "
               f"sheets · replaces scrolling through 170 tabs")

    filtered, confirmed_only = filters(deals)
    if confirmed_only:
        filtered = db.leer_deals(conn, solo_confirmados=True).loc[
            lambda d: d["deal_id"].isin(filtered["deal_id"])]

    st.divider()
    left, right = st.columns([3, 1])

    with left:
        can_edit = auth.puede(user, "edit_assumed")
        edit_mode = st.toggle(
            "Edit mode", value=False, disabled=not can_edit,
            help="Empty cells and amber (assumed) cells can be edited. "
                 "Confirmed values are locked and only a lead can unlock "
                 "them." if can_edit else "Your role is read-only")
        table = filtered.set_index("deal_id")[TABLE_COLS].rename(columns=LABELS)
        marks = {r["deal_id"]: [LABELS.get(c, c) for c in assumptions_of(r)]
                 for _, r in filtered.iterrows()}

        if not edit_mode:
            st.dataframe(paint_assumptions(table, marks),
                         use_container_width=True, height=460)
            st.caption(
                "Amber cells are **assumptions**: working numbers somebody "
                "entered, or values the source spreadsheet had flagged by "
                "colouring the text. Everything else came from the Sheet as "
                "confirmed data. Use the panel on the right to fill a gap, "
                "and the section below to confirm one."
            )
        else:
            cfg = {c: st.column_config.NumberColumn(c, format="%.0f")
                   for c in FORMATS if c in table.columns}
            edited = st.data_editor(table, use_container_width=True,
                                    height=460, num_rows="fixed",
                                    key="editor", column_config=cfg)
            if st.button("Save changes", type="primary"):
                save_edits(conn, user, table, edited, marks)

    with right:
        gaps_panel(conn, user, filtered)

    st.divider()
    confirm_panel(conn, user, filtered)


def save_edits(conn, user, before, after, marks):
    """Locking: a field is editable if it is empty or flagged as an
    assumption. A confirmed value is locked and needs an explicit unlock."""
    inverse = {v: k for k, v in LABELS.items()}
    applied, rejected = [], []

    for deal_id in before.index:
        for col in before.columns:
            old, new = before.at[deal_id, col], after.at[deal_id, col]
            if str(old) == str(new):
                continue
            editable = empty(old) or col in marks.get(deal_id, [])
            if not editable and not auth.puede(user, "edit_confirmed"):
                rejected.append(f"**{col}** on `{deal_id[:8]}`: confirmed and "
                                f"locked. A lead can unlock it.")
                continue
            db.registrar_override(
                conn, deal_id, inverse.get(col, col), new,
                user_id=user["user_id"], role=user["role"],
                is_assumption=True, source=f"edited in-app by {user['name']}")
            applied.append(f"**{col}** on `{deal_id[:8]}` → {new}")

    if applied:
        st.success(f"{len(applied)} change(s) saved as assumptions")
        for a in applied:
            st.caption(f"· {a}")
    if rejected:
        st.warning(f"{len(rejected)} change(s) rejected")
        for r in rejected:
            st.caption(f"· {r}")
    if applied:
        st.rerun()


def gaps_panel(conn, user, deals):
    st.markdown("#### Gaps that matter")
    st.caption(
        "Fields the pipeline **expects** at this deal's funnel stage but "
        "that have no value. A Sourcing record with no price is normal and "
        "does not appear here; a deal In Contract with no price does."
    )
    rows = []
    for _, row in deals.iterrows():
        reasons = row.get("null_reason") or {}
        if isinstance(reasons, str):
            reasons = json.loads(reasons)
        for field, r in reasons.items():
            if r.get("tipo") == "missing":
                rows.append({"Deal": deal_label(row),
                             "Missing": LABELS.get(field, field),
                             "Stage": STAGE.get(row["status"], row["status"]),
                             "_id": row["deal_id"], "_field": field})
    if not rows:
        st.success("No gaps in the expected fields")
        return

    df = pd.DataFrame(rows)
    st.caption(f"{len(df)} expected fields with no value, "
               f"across {df['Deal'].nunique()} deals")
    st.dataframe(df[["Deal", "Missing", "Stage"]], use_container_width=True,
                 hide_index=True, height=230)

    if not auth.puede(user, "add_assumption"):
        st.caption("Your role cannot record assumptions.")
        return

    st.markdown("**Fill one as an assumption**")
    st.caption(
        "Put in your best working number so the dashboard is usable today, "
        "without pretending it is confirmed. It is stored flagged, shown in "
        "amber everywhere, and excluded when anyone switches on "
        "\"Confirmed data only\"."
    )
    choice = st.selectbox(
        "Gap", list(df.index), label_visibility="collapsed",
        format_func=lambda i: f"{df.at[i, 'Missing']} · {df.at[i, 'Deal']}")
    with st.form("gap_form"):
        value = st.text_input(
            "Assumed value",
            help="What you believe the value is, pending verification. "
                 "It never overwrites the Sheet.")
        source = st.text_input(
            "Where does it come from?",
            placeholder="comparable nearby property / broker estimate",
            help="Who or what this number is based on. Recorded with your "
                 "name and the time, so anyone can trace it later.")
        if st.form_submit_button(
                "Record assumption", type="primary",
                help="Saves it as an assumption — visible, reversible and "
                     "attributed to you") and value:
            db.registrar_override(conn, df.at[choice, "_id"],
                                  df.at[choice, "_field"], value,
                                  user["user_id"], user["role"],
                                  is_assumption=True,
                                  source=source or "no source given")
            st.rerun()


def confirm_panel(conn, user, deals):
    if not auth.puede(user, "confirm"):
        return
    st.markdown("#### Confirm an assumption")
    st.caption(
        "The opposite move: you now have the real number from a trusted "
        "source. Confirming clears the amber flag and **locks** the value, "
        "so it can no longer be edited without an explicit unlock. This is "
        "what replaces changing the text colour in the spreadsheet."
    )

    options = []
    for _, row in deals.iterrows():
        for field in assumptions_of(row):
            options.append((f"{deal_label(row)} · {LABELS.get(field, field)} "
                            f"= {row.get(field)}",
                            row["deal_id"], field, row.get(field)))
    if not options:
        st.info("No pending assumptions in the current selection")
        return

    c1, c2, c3 = st.columns([3, 2, 1])
    with c1:
        chosen = st.selectbox(
            "Assumption", options, format_func=lambda o: o[0],
            help="Every value currently flagged as unconfirmed in your "
                 "filtered selection")
    with c2:
        source = st.text_input(
            "Confirmation source", placeholder="broker call, 21 Sep",
            help="Required. Who confirmed it and how — this is what makes "
                 "the value trustworthy later.")
    with c3:
        st.write("")
        if st.button("Confirm", type="primary", disabled=not source):
            db.registrar_override(conn, chosen[1], chosen[2], chosen[3],
                                  user["user_id"], user["role"],
                                  is_assumption=False, source=source)
            st.success("Confirmed and locked")
            st.rerun()


# ===========================================================================
# 2. WEEKLY REVIEW
# ===========================================================================

def weekly_view(conn, user, deals):
    st.markdown("### Weekly review")
    st.caption("Generated from the same dataset. Nobody retypes a row into "
               "a slide again.")

    sites = deals[deals["record_type"] == "site"]
    searches = deals[deals["record_type"] == "market_search"]
    active = sites[~sites["status"].isin(["closed", "on_hold"])]

    unconfirmed = sum(len(assumptions_of(r)) for _, r in active.iterrows())
    value = pd.to_numeric(active["list_price"], errors="coerce").sum()
    in_contract = (active["status"] == "in_contract").sum()
    negotiating = active["status"].isin(
        ["negotiating_loi", "negotiating_psa"]).sum()

    with st.container(border=True):
        st.markdown(f"#### {len(active)} active properties · "
                    f"{money(value)} in pipeline")
        st.markdown(f"**{in_contract}** in contract · "
                    f"**{negotiating}** in negotiation · "
                    f"**{len(searches)}** markets being searched")
        if unconfirmed:
            st.markdown(f":orange[**{unconfirmed} unconfirmed values** — "
                        f"flagged below]")

    pipeline_chart(active)
    st.divider()

    for vertical in sorted(active["vertical"].dropna().unique()):
        group = active[active["vertical"] == vertical].copy()
        group["_order"] = group["status"].map(
            lambda s: FUNNEL.index(s) if s in FUNNEL else 99)
        group = group.sort_values("_order", ascending=False)
        v_searches = searches[searches["vertical"] == vertical]
        subtotal = pd.to_numeric(group["list_price"], errors="coerce").sum()

        st.markdown(f"#### {VERT.get(vertical, vertical)}")
        st.caption(f"{len(group)} properties · {money(subtotal)}")

        rows = []
        for _, f in group.iterrows():
            flagged = assumptions_of(f)

            def mark(field, shown, flagged=flagged):
                return f"{shown} ⚠" if field in flagged else shown

            has_negotiated = not empty(f.get("current_price"))
            price = f["current_price"] if has_negotiated else f.get("list_price")
            price_cell = mark("current_price" if has_negotiated
                              else "list_price", money(price))
            if has_negotiated and not empty(f.get("list_price")):
                price_cell += f"  (list {money(f['list_price'])})"
            rows.append({
                "Market": text(f.get("market")),
                "Address": mark("address", text(f.get("address"))),
                "Stage": STAGE.get(f["status"], f["status"]),
                "$": price_cell,
                "SF": mark("bldg_sf", sqft(f.get("bldg_sf"))),
                "$/SF": "—" if empty(f.get("price_per_sf"))
                        else f"${float(f['price_per_sf']):,.0f}",
                "Lot": sqft(f.get("lot_sf")),
                "Notes": trim_note(f.get("notes"), f["status"]),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True,
                     hide_index=True)

        if len(v_searches):
            cities = [c for c in v_searches["city"].dropna().unique()]
            st.caption(f"↳ {len(v_searches)} market(s) being searched: "
                       + (", ".join(cities) if cities else "no city set"))
        st.write("")

    st.caption("⚠ = assumed value, pending confirmation")
    st.divider()

    c1, c2 = st.columns([1, 4])
    with c1:
        data = build_pdf(active, searches)
        if data:
            st.download_button("Download PDF", data,
                               file_name=f"weekly_review_"
                                         f"{datetime.now():%Y%m%d}.pdf",
                               mime="application/pdf", type="primary")
    with c2:
        st.caption("The PDF comes from the same dataset as the operational "
                   "table. Sheet and deck stop being two sources of truth.")


def pipeline_chart(active):
    """Magnitude by category -> horizontal bars, single axis, direct labels."""
    data = []
    for vertical, group in active.groupby("vertical"):
        value = float(pd.to_numeric(group["list_price"],
                                    errors="coerce").sum())
        data.append({"Vertical": VERT.get(vertical, vertical),
                     "Value": value, "Properties": len(group),
                     "label": f"{money(value)} · {len(group)} properties"})
    if not data:
        return
    df = pd.DataFrame(data).sort_values("Value", ascending=False)
    scale = alt.Scale(domain=[0, df["Value"].max() * 1.4], nice=False)

    base = alt.Chart(df).encode(
        y=alt.Y("Vertical:N", sort="-x", title=None, axis=Y_AXIS))
    bars = base.mark_bar(cornerRadiusEnd=4, height=26, stroke=None).encode(
        x=alt.X("Value:Q", title="Pipeline (US$)", scale=scale,
                # A low tick count: with many ticks and a short format Vega
                # rounds and repeats labels ("$20M $20M $20M").
                axis=alt.Axis(format="$~s", grid=True, domain=False,
                              tickCount=4)),
        color=colour_by_vertical(legend=False),
        tooltip=[alt.Tooltip("Vertical:N"),
                 alt.Tooltip("Value:Q", format="$,.0f", title="Pipeline"),
                 alt.Tooltip("Properties:Q")])
    labels = base.mark_text(align="left", dx=8, fontSize=12,
                            baseline="middle").encode(
        x=alt.X("Value:Q", scale=scale), text="label:N")

    st.altair_chart((bars + labels).properties(height=40 * len(df) + 40)
                    .configure_view(stroke=None), use_container_width=True)


def build_pdf(active, searches):
    try:
        from io import BytesIO

        from reportlab.lib import colors
        from reportlab.lib.pagesizes import landscape, letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer,
                                        Table, TableStyle)
    except ImportError:
        st.caption("Install `reportlab` to enable the PDF export")
        return None

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(letter),
                            topMargin=28, bottomMargin=28)
    styles = getSampleStyleSheet()
    total = pd.to_numeric(active["list_price"], errors="coerce").sum()
    flow = [Paragraph("Weekly Pipeline Review", styles["Title"]),
            Paragraph(f"{datetime.now():%d %B %Y} · {len(active)} active "
                      f"properties · {money(total)} in pipeline",
                      styles["Normal"]),
            Spacer(1, 14)]

    for vertical in sorted(active["vertical"].dropna().unique()):
        group = active[active["vertical"] == vertical]
        flow.append(Paragraph(f"<b>{VERT.get(vertical, vertical)}</b>",
                              styles["Heading3"]))
        rows = [["Market", "Address", "Stage", "$", "SF", "$/SF", "Notes"]]
        for _, f in group.iterrows():
            flagged = assumptions_of(f)
            price = f["current_price"] if not empty(f.get("current_price")) \
                else f.get("list_price")
            rows.append([
                text(f.get("market")), text(f.get("address"))[:30],
                STAGE.get(f["status"], f["status"]),
                money(price) + (" *" if flagged else ""),
                sqft(f.get("bldg_sf")),
                "—" if empty(f.get("price_per_sf"))
                else f"${float(f['price_per_sf']):,.0f}",
                trim_note(f.get("notes"), f["status"])[:45]])

        table = Table(rows, repeatRows=1,
                      colWidths=[60, 150, 85, 80, 70, 55, 220])
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0ee")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d8d8d4")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        flow += [table, Spacer(1, 12)]

    if len(searches):
        flow.append(Paragraph(f"<i>{len(searches)} markets being searched "
                              f"with no property identified yet</i>",
                              styles["Normal"]))
    flow.append(Paragraph("<i>* assumed value, pending confirmation</i>",
                          styles["Normal"]))
    doc.build(flow)
    return buffer.getvalue()


# ===========================================================================
# 3. ANALYSIS
# ===========================================================================

def prepare(deals):
    d = deals.copy()
    d["Vertical"] = d["vertical"].map(lambda v: VERT.get(v, v))
    d["Stage"] = d["status"].map(lambda s: STAGE.get(s, s))
    d["Deal"] = d.apply(deal_label, axis=1)
    return d


def analysis_filters(d):
    c1, c2, c3, c4 = st.columns([2, 2, 2, 1.4])
    with c1:
        verticals = st.multiselect("Vertical", VERT_DOMAIN, key="an_v")
    with c2:
        clients = st.multiselect("Client",
                                 sorted(d["client"].dropna().unique()),
                                 key="an_c")
    with c3:
        stages = st.multiselect(
            "Stage", [STAGE[e] for e in FUNNEL
                      if e in set(d["status"].dropna())], key="an_e")
    with c4:
        sites_only = st.toggle("Properties only", value=True, key="an_s",
                               help="Excludes market searches")

    f = d.copy()
    if verticals:
        f = f[f["Vertical"].isin(verticals)]
    if clients:
        f = f[f["client"].isin(clients)]
    if stages:
        f = f[f["Stage"].isin(stages)]
    if sites_only:
        f = f[f["record_type"] == "site"]
    return f, sites_only


def analysis_view(conn, user, deals):
    st.markdown("### Analysis")
    st.caption("18 records: these are indications of where to look, not "
               "statistical conclusions.")

    d = prepare(deals)
    f, sites_only = analysis_filters(d)
    if f.empty:
        st.warning("No records match those filters")
        return

    value = pd.to_numeric(f["list_price"], errors="coerce").sum()
    median = pd.to_numeric(f["price_per_sf"], errors="coerce").median()
    unconfirmed = sum(len(assumptions_of(r)) for _, r in f.iterrows())

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Records", len(f))
    k2.metric("Pipeline", money(value))
    k3.metric("Median $/SF", "—" if pd.isna(median) else f"${median:,.0f}")
    k4.metric("Unconfirmed", unconfirmed)

    st.divider()
    funnel_chart(d if sites_only else f)
    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        price_per_sf_chart(f)
    with c2:
        discount_chart(f)
    st.divider()
    c3, c4 = st.columns(2)
    with c3:
        completeness_chart(f)
    with c4:
        unconfirmed_chart(f)


def funnel_chart(d):
    order = [STAGE[e] for e in FUNNEL
             if e in STAGE and STAGE[e] in set(d["Stage"])]
    counts = d.groupby(["Stage", "Vertical"]).size().reset_index(name="Records")
    if counts.empty:
        return
    totals = d.groupby("Stage").size().reset_index(name="Total")
    scale = alt.Scale(domain=[0, totals["Total"].max() + 1], nice=False)

    bars = alt.Chart(counts).mark_bar(height=24, cornerRadiusEnd=3,
                                      stroke=None).encode(
        y=alt.Y("Stage:N", sort=order, title=None, axis=Y_AXIS),
        x=alt.X("Records:Q", title="Records", scale=scale,
                axis=alt.Axis(tickMinStep=1, grid=True, domain=False)),
        color=colour_by_vertical(),
        tooltip=["Stage:N", "Vertical:N", "Records:Q"])
    labels = alt.Chart(totals).mark_text(align="left", dx=8, fontSize=12,
                                         baseline="middle").encode(
        y=alt.Y("Stage:N", sort=order),
        x=alt.X("Total:Q", scale=scale), text="Total:Q")

    table = (counts.pivot(index="Stage", columns="Vertical", values="Records")
             .reindex(order).fillna(0).astype(int).reset_index())
    chart_with_table(
        "Where is the pipeline sitting?",
        (bars + labels).configure_view(stroke=None), table,
        "Early stages hold market searches with no property yet; later "
        "stages hold deals with a file behind them.",
        height=38 * max(len(order), 1) + 40)


def price_per_sf_chart(sites):
    df = sites[sites["price_per_sf"].notna()].copy()
    if df.empty:
        st.markdown("##### Price per square foot")
        st.info("No $/SF data with these filters")
        return
    df["USD_SF"] = pd.to_numeric(df["price_per_sf"], errors="coerce")
    median = df["USD_SF"].median()

    dots = alt.Chart(df).mark_circle(size=150, opacity=0.95,
                                     stroke=None).encode(
        x=alt.X("USD_SF:Q", title="US$ per SF",
                axis=alt.Axis(format="$,.0f", grid=True, domain=False,
                              tickCount=5)),
        y=alt.Y("Deal:N", sort="-x", title=None, axis=Y_AXIS),
        color=colour_by_vertical(),
        tooltip=["Deal:N", "Vertical:N", "Stage:N",
                 alt.Tooltip("USD_SF:Q", format="$,.0f", title="$/SF")])
    rule = alt.Chart(pd.DataFrame({"m": [median]})).mark_rule(
        strokeDash=[4, 4], color="#8a8a86", strokeWidth=1.5).encode(x="m:Q")

    table = (df[["Deal", "Vertical", "Stage", "USD_SF"]]
             .sort_values("USD_SF", ascending=False)
             .rename(columns={"USD_SF": "$/SF"}))
    table["$/SF"] = table["$/SF"].map(lambda v: f"${v:,.0f}")
    chart_with_table("Price per square foot",
                     (dots + rule).configure_view(stroke=None), table,
                     f"Dashed line = median ({money(median)}/SF).",
                     height=30 * len(df) + 60)


def discount_chart(sites):
    df = sites[sites["current_price"].notna()
               & sites["list_price"].notna()].copy()
    if df.empty:
        st.markdown("##### List vs. negotiated")
        st.info("No deal in this selection has a negotiated price")
        return
    df["List"] = pd.to_numeric(df["list_price"], errors="coerce")
    df["Negotiated"] = pd.to_numeric(df["current_price"], errors="coerce")
    df["Delta"] = (df["Negotiated"] - df["List"]) / df["List"] * 100
    df["label"] = df["Delta"].map(lambda v: f"{v:.1f}%")
    scale = alt.Scale(domain=[min(df["Delta"].min() * 1.6, -2), 0], nice=False)

    base = alt.Chart(df).encode(y=alt.Y("Deal:N", sort="x", title=None,
                                        axis=Y_AXIS))
    bars = base.mark_bar(height=26, cornerRadiusEnd=3, stroke=None).encode(
        x=alt.X("Delta:Q", title="% against list price", scale=scale,
                axis=alt.Axis(format=".0f", grid=True, domain=False,
                              tickCount=4)),
        color=colour_by_vertical(),
        tooltip=["Deal:N", alt.Tooltip("List:Q", format="$,.0f"),
                 alt.Tooltip("Negotiated:Q", format="$,.0f"),
                 alt.Tooltip("Delta:Q", format=".1f", title="% diff")])
    labels = base.mark_text(align="right", dx=-8, fontSize=12,
                            baseline="middle").encode(
        x=alt.X("Delta:Q", scale=scale), text="label:N")

    table = df[["Deal", "Vertical", "List", "Negotiated", "Delta"]].copy()
    table["List"] = table["List"].map(money)
    table["Negotiated"] = table["Negotiated"].map(money)
    table["Delta"] = table["Delta"].map(lambda v: f"{v:.1f}%")
    chart_with_table("List vs. negotiated",
                     (bars + labels).configure_view(stroke=None), table,
                     "These figures are NOT a column in the Sheet — they were "
                     "extracted from free-text notes. The gap between what is "
                     "published and what is actually being negotiated.",
                     height=50 * len(df) + 50)


def completeness_chart(d):
    df = d[d["completitud"].notna()].copy()
    if df.empty:
        return
    df["Complete"] = pd.to_numeric(df["completitud"], errors="coerce")
    df = df.sort_values("Complete")
    scale = alt.Scale(domain=[0, 118], nice=False)

    base = alt.Chart(df).encode(
        y=alt.Y("Deal:N", sort=alt.EncodingSortField("Complete"),
                title=None, axis=Y_AXIS))
    bars = base.mark_bar(height=20, cornerRadiusEnd=3, stroke=None).encode(
        x=alt.X("Complete:Q", title="% of expected fields with a value",
                scale=scale,
                axis=alt.Axis(values=[0, 25, 50, 75, 100], format=".0f",
                              grid=True, domain=False)),
        color=colour_by_vertical(),
        tooltip=["Deal:N", "Vertical:N", "Stage:N",
                 alt.Tooltip("Complete:Q", format=".0f")])
    labels = base.mark_text(align="left", dx=7, fontSize=11,
                            baseline="middle").encode(
        x=alt.X("Complete:Q", scale=scale),
        text=alt.Text("Complete:Q", format=".0f"))

    table = df[["Deal", "Vertical", "Stage", "Complete"]].sort_values("Complete")
    table["Complete"] = table["Complete"].map(lambda v: f"{v:.0f}%")
    chart_with_table("Completeness by deal",
                     (bars + labels).configure_view(stroke=None), table,
                     "Only counts fields EXPECTED at that stage. The lowest "
                     "ones in late stages are this week's work.",
                     height=26 * len(df) + 50)


def unconfirmed_chart(d):
    counts = {}
    for _, row in d.iterrows():
        for field in assumptions_of(row):
            key = LABELS.get(field, field)
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        st.markdown("##### Which fields are unconfirmed?")
        st.success("No assumed values in this selection")
        return

    df = (pd.DataFrame({"Field": list(counts), "Values": list(counts.values())})
          .sort_values("Values", ascending=False))
    scale = alt.Scale(domain=[0, df["Values"].max() + 1], nice=False)
    base = alt.Chart(df).encode(y=alt.Y("Field:N", sort="-x", title=None,
                                        axis=Y_AXIS))
    bars = base.mark_bar(height=24, cornerRadiusEnd=3, color=AMBER,
                         stroke=None).encode(
        x=alt.X("Values:Q", title="Unconfirmed values", scale=scale,
                axis=alt.Axis(tickMinStep=1, grid=True, domain=False)),
        tooltip=["Field:N", "Values:Q"])
    labels = base.mark_text(align="left", dx=8, fontSize=12,
                            baseline="middle").encode(
        x=alt.X("Values:Q", scale=scale), text="Values:Q")

    chart_with_table("Which fields are unconfirmed?",
                     (bars + labels).configure_view(stroke=None), df,
                     "A single series needs no legend. The amber is the same "
                     "visual code that marks assumptions in the table.",
                     height=38 * len(df) + 50)


# ===========================================================================
# 4. AI FINDINGS
# ===========================================================================

BADGE = {"risk": "🔴 Risk", "opportunity": "🟢 Opportunity",
         "data_quality": "🟡 Data quality", "process": "🔵 Process"}


def ai_findings_view(conn, user, deals):
    st.markdown("### AI findings")
    st.caption("Claude reads the pre-computed aggregates — never the raw "
               "rows — and flags what deserves attention. Every figure it "
               "cites is checked against the numbers this pipeline produced.")

    c1, c2 = st.columns([1, 4])
    with c1:
        regenerate = st.button("Regenerate", use_container_width=True)
    with c2:
        st.caption("Cached by a hash of the data: re-running costs nothing "
                   "and the demo is reproducible.")

    try:
        with st.spinner("Reading the pipeline..."):
            result, meta = insights.generate(deals, force=regenerate)
    except Exception as exc:                        # noqa: BLE001
        st.error(f"Could not generate findings: {exc}")
        return

    if result is None:
        st.warning("No API key configured. Add ANTHROPIC_API_KEY to .env "
                   "to enable this view.")
        with st.expander("What Claude would receive"):
            st.json(meta["context"])
        return

    if result.get("headline"):
        with st.container(border=True):
            st.markdown(f"#### {result['headline']}")
    st.write("")

    for finding in result["findings"]:
        with st.container(border=True):
            st.markdown(f"**{BADGE.get(finding.get('category'), '·')}** — "
                        f"**{finding['title']}**")
            st.write(finding["body"])
            if finding.get("action"):
                st.markdown(f"→ *{finding['action']}*")
            if finding.get("evidence"):
                with st.expander("Evidence"):
                    for e in finding["evidence"]:
                        st.markdown(f"- {e}")

    if result["flagged"]:
        st.divider()
        st.warning(f"{len(result['flagged'])} finding(s) withheld: they cite "
                   f"figures this pipeline never produced.")
        for f in result["flagged"]:
            with st.expander(f"Withheld — {f['title']}"):
                st.write(f["body"])
                st.caption(f"Unverified figures: {f['unverified_figures']}")
        st.caption("This is the guardrail working. A model that restates a "
                   "number we cannot trace does not get to present it as "
                   "fact.")

    st.divider()
    cols = st.columns(4)
    cols[0].metric("Findings", len(result["findings"]))
    cols[1].metric("Withheld", len(result["flagged"]))
    cols[2].metric("Source", meta["source"])
    cols[3].metric("Model", (meta["model"] or "—").split("-2")[0])

    with st.expander("Exactly what was sent to the model"):
        st.caption("Aggregates only, all computed in Python. Handing a model "
                   "a raw table invites it to do arithmetic in its head and "
                   "get it wrong.")
        st.json(meta["context"])


# ===========================================================================
# 5. USAGE & PERMISSIONS
# ===========================================================================

def usage_view(conn, user):
    st.markdown("### Usage & permissions")

    scope = auth.alcance_auditoria(user)
    blurb = {"todos": "You see activity across the whole organisation (admin)",
             "equipo": f"You see your team's activity: {user['team']} (lead)",
             "propio": "You see only your own activity"}
    st.caption(f"{blurb[scope]} · this panel honours the permission model, "
               f"like the rest of the app")

    log = pd.read_sql_query(
        "SELECT a.*, u.name, u.team FROM audit_log a "
        "LEFT JOIN users u ON u.user_id = a.user_id", conn)
    users = pd.read_sql_query("SELECT * FROM users", conn)

    if scope == "propio":
        log = log[log["user_id"] == user["user_id"]]
        users = users[users["user_id"] == user["user_id"]]
    elif scope == "equipo":
        log = log[log["team"] == user["team"]]
        users = users[users["team"] == user["team"]]

    log["ts"] = pd.to_datetime(log["ts"], errors="coerce")
    logins = log[log["action"] == "login"]
    edits = log[log["action"].isin(["assume", "confirm"])]

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Users", len(users))
    k2.metric("Logins (30 days)", len(logins))
    k3.metric("Edits", len(edits))
    recent = logins[logins["ts"] > datetime.now() - timedelta(days=7)]
    k4.metric("Active (7 days)", recent["user_id"].nunique())

    st.divider()
    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("##### Logins per day")
        if logins.empty:
            st.info("No activity recorded")
        else:
            daily = (logins.assign(day=logins["ts"].dt.date)
                     .groupby("day").size().reset_index(name="Logins"))
            line = alt.Chart(daily).mark_line(
                strokeWidth=2, color=COLOURS["food"],
                point=alt.OverlayMarkDef(size=45, color=COLOURS["food"])
            ).encode(
                x=alt.X("day:T", title=None, axis=alt.Axis(format="%d %b")),
                y=alt.Y("Logins:Q", title="Logins",
                        axis=alt.Axis(tickMinStep=1, grid=True)),
                tooltip=[alt.Tooltip("day:T", title="Day", format="%d %b"),
                         alt.Tooltip("Logins:Q")])
            st.altair_chart(line.properties(height=220)
                            .configure_view(stroke=None),
                            use_container_width=True)

    with c2:
        st.markdown("##### Activity per person")
        if logins.empty:
            st.info("No activity")
        else:
            per_user = (logins.groupby("name").size()
                        .reset_index(name="Logins")
                        .sort_values("Logins", ascending=False).head(10))
            scale = alt.Scale(domain=[0, per_user["Logins"].max() + 1],
                              nice=False)
            base = alt.Chart(per_user).encode(
                y=alt.Y("name:N", sort="-x", title=None, axis=Y_AXIS))
            bars = base.mark_bar(cornerRadiusEnd=3, height=16, stroke=None,
                                 color=COLOURS["food"]).encode(
                x=alt.X("Logins:Q", title=None, scale=scale,
                        axis=alt.Axis(grid=True, tickMinStep=1)),
                tooltip=["name:N", "Logins:Q"])
            labels = base.mark_text(align="left", dx=6, fontSize=11,
                                    baseline="middle").encode(
                x=alt.X("Logins:Q", scale=scale), text="Logins:Q")
            st.altair_chart((bars + labels)
                            .properties(height=26 * len(per_user) + 20)
                            .configure_view(stroke=None),
                            use_container_width=True)

    st.divider()
    t1, t2, t3 = st.tabs(["Recent activity", "Users & roles", "System status"])

    with t1:
        recent_log = log.sort_values("ts", ascending=False).head(60)
        view = recent_log[["ts", "name", "role", "action", "field",
                           "value_after", "source"]].rename(columns={
            "ts": "When", "name": "Who", "role": "Role", "action": "Action",
            "field": "Field", "value_after": "Value", "source": "Source"})
        st.dataframe(view, use_container_width=True, hide_index=True,
                     height=320)
        st.download_button("Export log (CSV)",
                           view.to_csv(index=False).encode(),
                           file_name=f"audit_log_{datetime.now():%Y%m%d}.csv",
                           mime="text/csv")

    with t2:
        matrix = [{"Role": role, "Users": int((users["role"] == role).sum()),
                   "Permissions": ", ".join(cfg["can"])}
                  for role, cfg in SCHEMA["roles"].items()]
        st.dataframe(pd.DataFrame(matrix), use_container_width=True,
                     hide_index=True)
        st.dataframe(users[["name", "email", "role", "team"]].rename(
            columns={"name": "Name", "email": "Email", "role": "Role",
                     "team": "Team"}),
            use_container_width=True, hide_index=True, height=260)
        if not auth.puede(user, "manage_users"):
            st.caption("Only an admin can change roles.")

    with t3:
        runs = pd.read_sql_query(
            "SELECT ts, origen, n_deals, n_contacts, conflictos FROM etl_runs "
            "ORDER BY run_id DESC LIMIT 10", conn)
        st.markdown("**Latest data refreshes**")
        st.dataframe(runs.rename(columns={
            "ts": "When", "origen": "Source", "n_deals": "Records",
            "n_contacts": "Contacts", "conflictos": "Conflicts"}),
            use_container_width=True, hide_index=True)

        conflicts = pd.read_sql_query(
            "SELECT * FROM conflicts WHERE resolved = 0", conn)
        if len(conflicts):
            st.warning(f"{len(conflicts)} conflict(s): the Sheet changed a "
                       f"field someone had edited here. Nothing was resolved "
                       f"automatically.")
            st.dataframe(conflicts, use_container_width=True, hide_index=True)
        else:
            st.success("No conflicts between the Sheet and local edits")


# ===========================================================================
# Main
# ===========================================================================

@st.cache_resource(show_spinner=False)
def bootstrap():
    """
    First boot on a fresh machine — which is every cold start on Streamlit
    Cloud, where the filesystem is ephemeral. Build the database from the
    live Sheet instead of showing an error nobody can act on.

    Cached as a resource so it happens once per container, not per rerun.
    """
    conn = db.conectar()
    empty = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0] == 0
    if empty:
        etl.correr(verbose=False)
        conn = db.conectar()
    return True


def main():
    st.markdown(STYLE, unsafe_allow_html=True)

    with st.spinner("Preparing the pipeline..."):
        bootstrap()
    conn = db.conectar()

    if "user" not in st.session_state:
        login_screen(conn)
        return
    user = st.session_state["user"]

    with st.sidebar:
        st.markdown(f"**{user['name']}**")
        st.caption(f"{user['role']} · {user['team']}")
        st.divider()
        page = st.radio("View", ["Operational", "Weekly review", "Analysis",
                                 "AI findings", "Usage & permissions"],
                        label_visibility="collapsed")
        st.divider()

        last = conn.execute(
            "SELECT ts, origen FROM etl_runs ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        if last:
            st.caption(f"Data from {last['origen']}")
            st.caption(f"Updated {last['ts'][:16].replace('T', ' ')}")

        if st.button("Refresh from Sheet", use_container_width=True):
            with st.spinner("Reading the Google Sheet and recomputing..."):
                _, info = etl.correr(verbose=False)
            st.success(f"{info['deals']} records · {info['segundos']:.1f}s")
            if info["conflictos"]:
                st.warning(f"{info['conflictos']} conflict(s) — see Usage")
            st.rerun()
        st.caption("Re-reads the Sheet and recomputes everything. Edits made "
                   "here are preserved.")

        st.divider()
        if st.button("Sign out", use_container_width=True):
            del st.session_state["user"]
            st.rerun()

    deals = db.leer_deals(conn)
    if deals.empty:
        st.error("The database is empty and the ETL could not fill it. "
                 "Check the Google credentials in Settings → Secrets.")
        st.json(__import__("settings").describe())
        return

    if page == "Operational":
        operational_view(conn, user, deals)
    elif page == "Weekly review":
        weekly_view(conn, user, deals)
    elif page == "Analysis":
        analysis_view(conn, user, deals)
    elif page == "AI findings":
        ai_findings_view(conn, user, deals)
    else:
        usage_view(conn, user)


if __name__ == "__main__":
    main()