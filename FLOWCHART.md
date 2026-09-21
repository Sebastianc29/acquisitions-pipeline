# Case study flowchart

How the thinking went, not just how the bytes move. The decision points are
the part worth reading.

```mermaid
flowchart TB
    subgraph SOURCE["SOURCE"]
        GS["Google Sheets (live)<br/>5 tabs, 3 different column sets"]
        XL["MAKE A COPY .xlsx<br/>fallback"]
    end

    GS -->|"Sheets API,<br/>includeGridData"| READ
    XL -->|openpyxl| READ

    READ["<b>1 · INGEST</b><br/>raw values, nothing altered<br/>+ font-colour scan"]

    READ --> D1{"Why read formatting<br/>at all?"}
    D1 -->|"the team marks unconfirmed<br/>values by colouring text<br/>— a CSV export destroys it"| PROF

    PROF["<b>2 · PROFILE</b><br/>9 quality checks<br/><i>changes nothing</i>"]

    PROF --> D2{"Profile and clean<br/>in one pass?"}
    D2 -->|"No. Cleaning as you look<br/>means you can never say<br/>how dirty it was"| NORM

    NORM["<b>3 · NORMALISE</b><br/>13 statuses → 7<br/>types, headers, derived fields"]

    NORM --> D3{"A value looks wrong.<br/>Fix it?"}
    D3 -->|"$1,139/SF · 423 days on market<br/>→ flag, keep, explain"| NOTES
    D3 -->|"'2,400A' · '15-51'<br/>→ parse, keep the original"| NOTES

    NOTES["<b>4 · EXTRACT FROM NOTES</b><br/>regex first, LLM for ambiguity"]

    NOTES --> D4{"Two amounts in one note.<br/>Which is the price?"}
    D4 -->|"regex refuses to guess"| LLM
    LLM["LLM disambiguates<br/>‘countered at $3.25M,<br/>we responded at $3.05M’"]
    LLM --> VERIFY{"Does the quoted fragment<br/>exist in the note?"}
    VERIFY -->|no| DROP["discard + report"]
    VERIFY -->|yes| NULLS

    NULLS["<b>4.5 · CLASSIFY GAPS</b><br/>not_yet_known vs missing"]

    NULLS --> D5{"Fill the gaps with<br/>an average?"}
    D5 -->|"Never. A number without a source<br/>gets quoted to a client"| D6
    D6{"Then what explains<br/>each gap?"}
    D6 -->|"funnel stage · property type ·<br/>sale vs lease"| UNIFY

    UNIFY["<b>5 · UNIFY</b><br/>3 sheets → 1 dataset<br/>deterministic deal_id"]

    UNIFY --> D7{"Where does human<br/>work live?"}
    D7 -->|"NOT in the same table.<br/>A refresh would erase it"| DB

    DB[("SQLite<br/><b>deals</b> mirrors the Sheet<br/><b>deal_overrides</b> holds edits<br/><b>audit_log</b> holds everything")]

    DB --> V1["<b>Operational</b><br/>filter · edit · lock"]
    DB --> V2["<b>Weekly review</b><br/>grouped, readable, → PDF"]
    DB --> V3["<b>Analysis</b><br/>5 questions, 5 charts"]
    DB --> V4["<b>AI findings</b><br/>aggregates only, figures verified"]
    DB --> V5["<b>Usage & permissions</b><br/>scoped to the viewer"]

    V1 -.->|"assume / confirm"| DB
    DB -.->|"Refresh from Sheet"| READ

    classDef step fill:#2a78d6,stroke:#1a5aa8,color:#fff
    classDef decision fill:#eda100,stroke:#b87d00,color:#1a1a19
    classDef store fill:#1baf7a,stroke:#138a60,color:#fff
    classDef view fill:#f2f2ef,stroke:#c9c9c2,color:#0b0b0b
    classDef bad fill:#e34948,stroke:#b03635,color:#fff

    class READ,PROF,NORM,NOTES,NULLS,UNIFY,LLM step
    class D1,D2,D3,D4,D5,D6,D7,VERIFY decision
    class DB,GS,XL store
    class V1,V2,V3,V4,V5 view
    class DROP bad
```

---

## The five decisions that shaped everything

**Formatting is data.** The source marks unconfirmed values by colouring the
text. That signal dies in any CSV export, so the pipeline reads font colours
and turns them into a field — one that can be filtered, counted, and
confirmed with a named source. One cell in the sample carries it; the scan
is generic and would find two hundred.

**Profiling never cleans.** Step 2 measures and writes a report; step 3
changes things. Doing both at once means that when you finish you can no
longer say how dirty the data was — and the brief asks exactly that.

**Nothing is imputed.** No average fills a missing price. In a pipeline a
partner reads out loud, a number without a source gets repeated to a client.
What changed is that we now know which gaps matter: a Sourcing record with
no price is normal, a deal In Contract with no price is an alarm.

**The LLM interprets, it never invents.** For note extraction it must quote
the fragment it read, and the code checks that fragment appears in the
source text. For AI findings it receives only pre-computed aggregates, and
every figure it cites is matched against the numbers Python produced.
Anything that fails is withheld and shown as withheld.

**Human work lives in its own table.** `deals` mirrors the Sheet and is
rewritten on every refresh; `deal_overrides` holds assumptions and
confirmations and is never touched by the ETL. That separation is what makes
a scheduled morning refresh safe. Without it, the first automated run would
delete a week of the team's corrections — and nobody would trust the tool
again.
