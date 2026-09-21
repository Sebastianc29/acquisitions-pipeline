"""
AI Findings — Claude reads the aggregated dataset and surfaces patterns.

The point is NOT to have a model narrate the charts. It is to have it do
the thing a human analyst does badly at 8am on a Monday: cross-reference
eighteen records across three sheets and say what deserves attention.

SAME DISCIPLINE AS STEP 4 (note extraction):
  The model receives a compact, pre-computed summary of the data — never
  raw rows it could misread — and every finding must cite evidence.
  Each cited figure is checked against the numbers we actually computed.
  A finding that cites a number we never produced is flagged, not shown
  as fact. The model interprets; it does not invent.

WHY AGGREGATES AND NOT RAW ROWS:
  Handing a model a table and asking "what do you see" invites it to
  recompute sums in its head and get them wrong. We compute every number
  in Python, hand it the results, and ask only for interpretation.

Cached by a hash of the context, so re-runs cost nothing and the demo is
reproducible.
"""

import hashlib
import json
import os
import re
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

CACHE_PATH = Path("cache/insights.json")
SCHEMA = yaml.safe_load(Path("config/schema.yaml").read_text(encoding="utf-8"))
FUNNEL = SCHEMA["funnel"]["order"] + SCHEMA["funnel"]["terminal"]
DISPLAY_STAGE = {k: v["display"] for k, v in SCHEMA["status_map"].items()}
DISPLAY_VERT = {k: v["display"] for k, v in SCHEMA["verticals"].items()}


def _empty(v):
    if v is None:
        return True
    if isinstance(v, float) and pd.isna(v):
        return True
    if isinstance(v, str) and v.strip() in {"", "-"}:
        return True
    if isinstance(v, (list, dict)) and len(v) == 0:
        return True
    return False


def _assumptions(row):
    v = row.get("assumptions")
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            v = []
    return list(v or [])


def _label(row):
    if not _empty(row.get("address")):
        return str(row["address"])
    city = row.get("city")
    return f"{city} (no address)" if not _empty(city) else "unnamed search"


# ===========================================================================
# Context: every number computed in Python, never by the model
# ===========================================================================

def build_context(deals):
    sites = deals[deals["record_type"] == "site"]
    searches = deals[deals["record_type"] == "market_search"]
    active = sites[~sites["status"].isin(["closed", "on_hold"])]

    def num(col, frame=None):
        frame = sites if frame is None else frame
        return pd.to_numeric(frame[col], errors="coerce")

    ctx = {
        "generated_on": date.today().isoformat(),
        "totals": {
            "records": len(deals),
            "sites": len(sites),
            "market_searches": len(searches),
            "active_sites": len(active),
            "pipeline_value_usd": round(float(num("list_price", active).sum())),
        },
        "by_vertical": [],
        "by_stage": [],
        "price_per_sf": {},
        "negotiated_vs_list": [],
        "data_gaps": [],
        "unconfirmed_values": [],
        "outliers": [],
        "cross_sheet_notes": [],
    }

    for vertical, group in deals.groupby("vertical"):
        g_sites = group[group["record_type"] == "site"]
        ctx["by_vertical"].append({
            "vertical": DISPLAY_VERT.get(vertical, vertical),
            "records": len(group),
            "sites": len(g_sites),
            "pipeline_value_usd": round(float(num("list_price", g_sites).sum())),
            "median_price_per_sf": _round(num("price_per_sf", g_sites).median()),
            "median_completeness_pct": _round(
                pd.to_numeric(group["completitud"], errors="coerce").median()),
        })

    counts = deals["status"].value_counts().to_dict()
    for stage in FUNNEL:
        if stage in counts:
            stage_rows = deals[deals["status"] == stage]
            ctx["by_stage"].append({
                "stage": DISPLAY_STAGE.get(stage, stage),
                "funnel_position": FUNNEL.index(stage) + 1,
                "records": int(counts[stage]),
                "sites": int((stage_rows["record_type"] == "site").sum()),
                "value_usd": round(float(num("list_price", stage_rows).sum())),
            })

    pps = num("price_per_sf").dropna()
    if len(pps):
        ctx["price_per_sf"] = {
            "median": _round(pps.median()),
            "min": _round(pps.min()),
            "max": _round(pps.max()),
            "count": int(len(pps)),
        }

    for _, r in sites.iterrows():
        if _empty(r.get("current_price")) or _empty(r.get("list_price")):
            continue
        lst, cur = float(r["list_price"]), float(r["current_price"])
        ctx["negotiated_vs_list"].append({
            "deal": _label(r),
            "vertical": DISPLAY_VERT.get(r["vertical"], r["vertical"]),
            "stage": DISPLAY_STAGE.get(r["status"], r["status"]),
            "list_usd": round(lst),
            "negotiated_usd": round(cur),
            "delta_pct": _round((cur - lst) / lst * 100),
            "source": "extracted from free-text notes, not a sheet column",
        })

    for _, r in deals.iterrows():
        reasons = r.get("null_reason") or {}
        if isinstance(reasons, str):
            reasons = json.loads(reasons)
        missing = [f for f, v in reasons.items() if v.get("tipo") == "missing"]
        if missing:
            ctx["data_gaps"].append({
                "deal": _label(r),
                "vertical": DISPLAY_VERT.get(r["vertical"], r["vertical"]),
                "stage": DISPLAY_STAGE.get(r["status"], r["status"]),
                "funnel_position": FUNNEL.index(r["status"]) + 1
                if r["status"] in FUNNEL else None,
                "missing_fields": sorted(missing),
                "completeness_pct": _round(r.get("completitud")),
            })
        for field in _assumptions(r):
            ctx["unconfirmed_values"].append({
                "deal": _label(r), "field": field,
                "value": str(r.get(field)),
            })

    for _, r in sites.iterrows():
        pps_v = r.get("price_per_sf")
        if not _empty(pps_v) and (float(pps_v) > 800 or float(pps_v) < 20):
            ctx["outliers"].append({
                "deal": _label(r), "metric": "price_per_sf",
                "value": _round(pps_v),
                "note": "flagged by the pipeline, kept as-is (looks real)",
            })
        dom = r.get("days_on_market")
        if not _empty(dom) and float(dom) > 365:
            ctx["outliers"].append({
                "deal": _label(r), "metric": "days_on_market",
                "value": _round(dom),
                "note": "listed for more than a year",
            })
        if (not _empty(r.get("bldg_sf")) and not _empty(r.get("lot_sf"))
                and float(r["bldg_sf"]) > float(r["lot_sf"])):
            ctx["cross_sheet_notes"].append({
                "deal": _label(r),
                "observation": "building SF exceeds lot SF — implies "
                               "multi-storey, not a data error",
            })

    markets = deals.groupby("market")["city"].nunique().to_dict()
    ctx["market_vs_city"] = [
        {"assigned_market": m, "distinct_cities": int(n)}
        for m, n in markets.items() if n > 1
    ]
    if ctx["market_vs_city"]:
        ctx["market_vs_city_note"] = (
            "'market' is the client's search mandate, not the property "
            "location — several mandates span multiple cities"
        )

    return ctx


def _round(v, nd=2):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, nd) if abs(f) < 1000 else round(f)


# ===========================================================================
# Prompt
# ===========================================================================

SYSTEM = """You are a real-estate acquisitions analyst reviewing a pipeline
dashboard before a Monday leadership meeting.

You receive a PRE-COMPUTED summary. Every number in it was calculated in
Python from the underlying dataset.

ABSOLUTE RULES:
1. Never compute, estimate or invent a number. Only cite figures that
   appear literally in the summary you were given.
2. Every finding must include the specific figures and deal names it rests
   on, copied from the summary.
3. This is 18 records. Say "indication" or "worth checking", never
   "trend", "correlation" or "statistically".
4. If the data does not support an interesting finding, say so. A short
   honest list beats a padded one.
5. Prioritise what a partner would act on this week over what is merely
   describable.

Return ONLY JSON, no prose around it, no code fences."""

SCHEMA_OUT = """{
  "findings": [
    {
      "title": "<max 10 words, states the finding, not the topic>",
      "category": "risk" | "opportunity" | "data_quality" | "process",
      "body": "<2-3 sentences: what it is and why it matters>",
      "action": "<one concrete next step for the team>",
      "evidence": ["<figure or deal name copied from the summary>", "..."],
      "figures": [<every number you cited, as plain numbers>]
    }
  ],
  "headline": "<one sentence a partner could read out loud>"
}"""


def response_text(resp):
    """
    Extended-thinking models return a ThinkingBlock BEFORE the text block,
    so resp.content[0].text raises AttributeError. Concatenate every text
    block instead of assuming the first one is text.
    """
    parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    if not parts:                       # older SDKs may not set .type
        parts = [getattr(b, "text", "") for b in resp.content]
    return "".join(parts).strip()


def _client_and_model():
    try:
        import anthropic
        from dotenv import load_dotenv
    except ImportError:
        return None, None
    load_dotenv()
    import settings
    key = settings.get("ANTHROPIC_API_KEY")
    if not key:
        return None, None
    client = anthropic.Anthropic(api_key=key)
    model = settings.get("ANTHROPIC_MODEL")
    if not model:
        try:
            ids = [m.id for m in client.models.list(limit=50).data]
        except Exception:
            return None, None
        pick = ([m for m in ids if "sonnet" in m.lower()]
                or [m for m in ids if "haiku" in m.lower()] or ids)
        model = pick[0]
    return client, model


# ===========================================================================
# Verification
# ===========================================================================

def _numbers_in(obj, acc=None):
    """Every number that appears anywhere in the context we handed over."""
    acc = set() if acc is None else acc
    if isinstance(obj, dict):
        for v in obj.values():
            _numbers_in(v, acc)
    elif isinstance(obj, list):
        for v in obj:
            _numbers_in(v, acc)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        acc.add(round(float(obj), 2))
    elif isinstance(obj, str):
        for m in re.finditer(r"-?\d[\d,]*\.?\d*", obj):
            try:
                acc.add(round(float(m.group().replace(",", "")), 2))
            except ValueError:
                pass
    return acc


def verify(findings, ctx):
    """
    A cited figure must exist in the summary. Tolerance for rounding and for
    derived magnitudes the model may restate (65.5 for 65,540,000).
    """
    known = _numbers_in(ctx)
    checked, flagged = [], []

    for f in findings:
        bad = []
        for fig in f.get("figures", []):
            try:
                x = round(float(fig), 2)
            except (TypeError, ValueError):
                continue
            variants = {x, round(x), round(x, 1), abs(x),
                        round(x * 1_000_000), round(x * 1_000)}
            if any(any(abs(v - k) <= max(0.51, abs(k) * 0.011) for k in known)
                   for v in variants):
                continue
            bad.append(fig)
        f = dict(f)
        f["unverified_figures"] = bad
        f["verified"] = not bad
        (flagged if bad else checked).append(f)

    return checked, flagged


# ===========================================================================
# Cache + entry point
# ===========================================================================

def _cache():
    return json.loads(CACHE_PATH.read_text(encoding="utf-8")) \
        if CACHE_PATH.exists() else {}


def _save(cache):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False),
                          encoding="utf-8")


def generate(deals, force=False):
    """
    Returns (result, meta).
      result: {"headline": str, "findings": [...], "flagged": [...]}
      meta:   {"source": "cache"|"api"|"unavailable", "model": str|None}
    """
    ctx = build_context(deals)
    key = hashlib.sha256(
        json.dumps(ctx, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]

    cache = _cache()
    if key in cache and not force:
        data = cache[key]
        ok, flagged = verify(data.get("findings", []), ctx)
        return ({"headline": data.get("headline", ""),
                 "findings": ok, "flagged": flagged},
                {"source": "cache", "model": data.get("_model"),
                 "context": ctx})

    client, model = _client_and_model()
    if client is None:
        return None, {"source": "unavailable", "model": None, "context": ctx}

    prompt = (
        "Here is the pre-computed summary of the acquisitions pipeline.\n\n"
        f"{json.dumps(ctx, indent=2, default=str)}\n\n"
        "Produce 3 to 5 findings. Return exactly this JSON shape:\n"
        f"{SCHEMA_OUT}"
    )
    resp = client.messages.create(
        model=model, max_tokens=2500, system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response_text(resp)
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"no JSON in response: {text[:200]}")

    data = json.loads(match.group())
    data["_model"] = model
    cache[key] = data
    _save(cache)

    ok, flagged = verify(data.get("findings", []), ctx)
    return ({"headline": data.get("headline", ""),
             "findings": ok, "flagged": flagged},
            {"source": "api", "model": model, "context": ctx,
             "tokens": resp.usage.input_tokens + resp.usage.output_tokens})


if __name__ == "__main__":
    import db

    conn = db.conectar()
    result, meta = generate(db.leer_deals(conn))
    if result is None:
        print("No API key — set ANTHROPIC_API_KEY in .env")
    else:
        print(f"source: {meta['source']}  model: {meta['model']}\n")
        print(f"HEADLINE: {result['headline']}\n")
        for f in result["findings"]:
            print(f"[{f['category']}] {f['title']}")
            print(f"  {f['body']}")
            print(f"  -> {f['action']}")
            print(f"  evidence: {f['evidence']}\n")
        for f in result["flagged"]:
            print(f"FLAGGED (unverified figures {f['unverified_figures']}): "
                  f"{f['title']}")