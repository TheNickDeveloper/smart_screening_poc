# Smart Screening from CRM — Conversational Client Short-Listing + Agentic Lead Generation on MCP

> An agentic system that reads RM natural-language intents, accumulates structured filters across turns, and generates prioritized, evidence-backed client short lists with next best actions — with full audit trail and RM feedback loop.
>
> This document breaks down how it works from a workflow perspective, and what value it delivers to the business.

---

## Application UIUX

![image](https://github.com/YOUR_USERNAME/crm-smart-screening/blob/main/application_uiux.png)

---

## 1. What Problem This POC Solves

Private banking RMs spend hours manually filtering client lists before every campaign. A typical task: "find clients with USD large-amount deposits maturing in late October." This means opening CRM, applying filters, exporting to Excel, cross-checking AUM, drafting talking points — 15+ minutes per campaign, repeated for every event.

The traditional "CRM filter + manual export" approach has three fatal flaws:

- **Rigid**: Filter forms force RMs to translate intent into dropdowns. If you want "clients holding tech stocks AND bonds," most CRMs can't express it without custom SQL.
- **No reasoning**: CRM returns raw rows. It doesn't tell you *why* these clients matter, or *what* to say to them.
- **No traceability**: When a manager asks "why is C015 on this list?", the answer is "because I filtered it." There's no audit trail of who queried what, when.

This POC uses a **Conversational Screening + Agentic Lead Generation** architecture to solve all three simultaneously.

---

## 2. End-to-End Workflow

The system operates in two phases: **conversational screening** and **agentic lead generation**.

### Phase 1: Conversational Screening (Per Turn)

```text
RM Natural Language → Intent + Condition Extraction → Filter Accumulation → SQL Generation → Short List
```

**Step 1 | RM Natural Language**

Input is a plain-language request in Chinese or English. Example: *"幫我找十月底美元大額定存到期的客人"*. No forms, no dropdowns, no SQL.

**Step 2 | Intent + Condition Extraction**

DeepSeek parses the message into structured JSON. It distinguishes intent (`filter` / `modify_filter` / `reset` / `generate_leads` / `chat`) and extracts conditions into `{entity, field, operator, value}` tuples. Fixed business rules (e.g., "large deposit" = ≥ USD 500K, "late October" = Oct 21–31) are baked into the system prompt so the LLM never has to guess.

**Step 3 | Filter Accumulation**

Conditions accumulate across turns in `session_state`. If the RM says "只要 UHNW 的" after the first query, the system appends `segment = UHNW`. If they say "改成 100 萬以上", the system replaces the amount threshold (same field → remove old, add new). The RM can add, modify, or reset at any point.

**Step 4 | SQL Generation (via MCP)**

The MCP Server converts accumulated conditions into SQL. Each holding condition becomes an `EXISTS` subquery, which correctly expresses "holds A AND holds B" across multiple holdings — something plain `JOIN` cannot do. The condition → SQL mapping is handled inside the MCP Server, so the LLM never writes SQL directly.

**Step 5 | Short List Returned**

The result includes client IDs, names, AUM (broken down by asset class), RM assignment, and contact info. It also writes an audit log entry — who queried what, when, and which clients were returned.

**Output**: For each turn, a prioritized, evidence-backed client short list, ready for RM action.

---

### Phase 2: Agentic Lead Generation (Per Campaign)

```text
RM Short List → Client Profile Fetch → Batched LLM Lead Gen → Talking Points + Product Picks
```

#### Orchestration: MCP Server as Tool Layer

The system is orchestrated with **[MCP (Model Context Protocol)](https://modelcontextprotocol.io)**, not a monolithic script. `server.py` exposes two tools over stdio:

```text
Streamlit (Client) ⟷ MCP Server ⟷ SQLite
```

| Tool | Responsibility |
| --- | --- |
| `screen_customers` | Convert structured conditions into SQL, execute, return short list |
| `generate_leads` | Fetch full client profiles (holdings + details) for lead generation |

How the phases map to tools:

| Phase | MCP Tool |
| --- | --- |
| Phase 1 — Conversational Screening | `screen_customers` |
| Phase 2 — Client Profile Fetch | `generate_leads` |
| Lead text generation | Done in Streamlit layer via DeepSeek |

Why MCP instead of a plain Python function:

- **Decoupled** — LLM ↔ database boundary is explicit. The MCP Server can be tested independently, replaced, or scaled without touching the frontend.
- **Standard protocol** — Any MCP-compatible client (Claude Desktop, custom agent) can reuse the same Server without code changes.
- **Field whitelisting** — The Server only accepts known fields, preventing LLM hallucination of non-existent columns.
- **Audit at source** — Every `screen_customers` call is logged inside the Server, so no caller can bypass the audit trail.
- **Extensible by design** — Add compliance checks, product matching, or data masking as new tools without rewriting existing ones.

The Server is started per `call_mcp` invocation via `stdio_client`, executes, returns, and shuts down. Simple, stateless, testable.

#### Step-by-step

**Step 6 | RM Opens Dashboard — Streamlit**

Streamlit loads session state and renders the chat interface. The sidebar shows accumulated conditions in real time, so the RM always sees what's being filtered.

**Step 7 | RM Refines Filters — Conversational**

The RM can add conditions ("只要風險評級 C1 或 C2 的"), modify them ("金額門檻改成 100 萬"), or reset ("重來"). Each turn re-runs the full query from scratch (not incremental refinement), which keeps semantics clean and predictable.

**Step 8 | RM Reviews Short List**

The list is displayed as a Markdown table: client ID, name, segment, risk profile, AUM, RM, contact. Sorted by AUM descending. The current filter JSON is shown above the table for transparency.

**Step 9 | RM Requests Lead Generation**

Typing "生成 leads" triggers Phase 2. The MCP Server's `generate_leads` tool fetches each client's full profile: AUM by asset class, all active holdings, and product-level details (bond ratings, stock sectors, fund categories, FX pairs, deposit terms).

**Step 10 | Batched Lead Generation**

Profiles are processed in batches of 5 to avoid token truncation. Each batch goes to DeepSeek with the campaign context and the recommendation logic (e.g., "C1/C2 clients → recommend bonds, savings insurance"). The output is a structured lead per client: talking points + recommended products + reasons.

**Step 11 | Audit Log Persists**

Both `screen_customers` and `generate_leads` write to `audit_log`: `queried_by`, `query_text`, `parsed_conditions`, `returned_ids`, `action_type`, `created_at`. This satisfies financial-services compliance — every list produced is traceable.

**Step 12 | Return to RM**

The generated leads are rendered in the chat interface, ready for the RM to copy or act on. The RM can continue refining filters or start a new campaign.

---

## 3. Complete Data Flow

### Phase 1 — Conversational Screening

```text
RM Natural Language
    ↓
[DeepSeek] Intent + condition extraction
    ↓
[session_state] Filter accumulation (add / modify / reset)
    ↓
[MCP] screen_customers → SQL generation (EXISTS subqueries)
    ↓
[SQLite] Query execution
    ↓
[audit_log] Write entry
    ↓
Short List returned
```

### Phase 2 — Agentic Lead Generation

```text
Short List (last_result_ids)
    ↓
[MCP] generate_leads → fetch client profiles + holdings + details
    ↓
[Batching] Split into groups of 5
    ↓
[DeepSeek] Generate talking points + product recommendations
    ↓
[audit_log] Write entry
    ↓
Leads returned
```

### Closed Loop

```text
audit_log → compliance review
session_state → next turn's condition accumulation
```

---

## 4. Business Value

### 1. Turns Natural Language into Structured CRM Filters

The RM no longer translates intent into dropdowns. "十月底美元大額定存到期" becomes five structured conditions in one shot. "同時持有科技股和債券" becomes two EXISTS subqueries. The system speaks the RM's language, not the database's.

**Business value**: Campaign prep time drops from 15+ minutes to under 60 seconds.

### 2. Accumulative Filtering, Not Form-Filling

Conditions accumulate across turns. The RM can start broad ("美元定存到期") and narrow progressively ("只要 UHNW" → "加風險評級 C1-C2" → "限定台北"). No need to restart the query. No need to remember what was filtered before — the sidebar always shows current conditions.

**Business value**: RM explores the client base fluidly, the way they'd think through a campaign.

### 3. Multi-Product Portfolios, Correctly Filtered

The system supports deposits, funds, bonds, stocks, FX, structured products, and insurance — with product-specific fields (bond credit rating, stock sector, fund region, FX pair). Cross-product conditions use `EXISTS`, so "holds tech stocks AND bonds" works correctly.

**Business value**: One query language for the entire portfolio. No more "this filter only works for deposits."

### 4. Every List Is Auditable

Every `screen_customers` call writes to `audit_log`: who queried, what was asked, what conditions were parsed, which clients were returned. The manager can reconstruct any list produced, months later.

**Business value**: Meets financial-services compliance. No "black box" list generation.

### 5. AI-Generated Talking Points Grounded in Client Profile

Lead generation isn't generic. Each client's full profile — AUM breakdown, existing holdings, risk rating, maturity dates — is fed to DeepSeek. The output references specific amounts and dates: *"您的美元9個月定存（100萬美元）將於10/26到期..."*. Recommendations respect risk profile: C1/C2 clients get bonds and savings insurance, not tech funds.

**Business value**: RMs walk into calls with a tailored script, not a blank page.

### 6. Batched Generation Avoids Truncation

Lead generation processes clients in batches of 5. Each batch has enough token headroom to produce full talking points without being cut off. If one batch fails, the others still succeed.

**Business value**: Reliable output even for large short lists.

### 7. Field Whitelisting Prevents Hallucination

The MCP Server only accepts known fields. If the LLM hallucinates a field like `account_balance` (which doesn't exist in the schema), the Server ignores it or errors out — it never silently returns wrong results.

**Business value**: In finance, a wrong field is more dangerous than no result. This design eliminates that risk at the boundary.

### 8. Zero Marginal Cost for RM Consumption

Filtering and lead generation require API calls, but once the short list and leads are produced, RMs can review them in the Streamlit UI without further cost. Estimated **~$0.01 per campaign** (5–10 clients, batched generation) with DeepSeek's pricing.

**Business value**: Scales across RMs with negligible marginal cost.

### 9. Extensible into a Full Campaign Platform

The current architecture scales horizontally:

- Add **real CRM connectors** (Salesforce, HubSpot, internal core banking)
- Add **role-based access control** (RM sees only own clients)
- Add **compliance checks** (suitability, KYC status) as additional MCP tools
- Add **export to CSV / Excel** for offline RM workflows
- Add **campaign scheduling** (auto-run screening weekly, push leads to RM)
- Add **multi-language support** for cross-border RMs

**Business value**: Evolves from POC into production-grade screening infrastructure without architectural rewrite.

### 10. Quantitative Quality Measurement Built In

The `audit_log` table enables metrics that can be computed automatically:

| Metric | What It Measures |
| --- | --- |
| Queries per RM per week | Adoption intensity |
| Average list size | Filter specificity |
| Filter refinement count | How much RMs iterate |
| Lead generation rate | How often RMs act on lists |
| Zero-result query rate | How often filters miss |

**Business value**: The team can track usage patterns, tune business rules, and prove ROI with numbers.

---

## 5. Tech Stack Summary

| Layer | Technology | Role |
| --- | --- | --- |
| Frontend | Streamlit | Chat UI: filters + short list + leads |
| Orchestration | MCP (stdio) | Tool protocol between LLM and database |
| LLM | DeepSeek (OpenAI-compatible) | Intent parsing + lead generation |
| Database | SQLite | CRM data: clients, holdings, product details |
| Audit | SQLite (`audit_log`) | Query traceability |
| Constraints | System prompts + field whitelist | Prevent hallucination, enforce rules |
| Data | Synthetic portfolio | POC input; production would ingest from CRM |

---

## 6. Comparison: This POC vs Traditional CRM Filtering

| Dimension | Traditional CRM | This POC |
| --- | --- | --- |
| **Query interface** | Filter forms, dropdowns | Natural language |
| **Cross-product filtering** | Siloed per product | Unified via EXISTS |
| **Filter refinement** | Restart from scratch | Accumulate across turns |
| **Lead generation** | Manual, or none | AI-generated with profile context |
| **Talking points** | RM writes from memory | AI-generated, profile-grounded |
| **Auditability** | "RM filtered it" | Every query logged with conditions |
| **Business rules** | Hardcoded in UI | Baked into LLM prompt |
| **Scalability** | Per-RM effort | One system serves all RMs |
| **Improvement** | Manual process redesign | Prompt iteration + business rule updates |

---

## 7. What Success Looks Like

If this POC moves to production, success would be measured by:

| Metric | Target |
| --- | --- |
| Campaign prep time per list | < 60 seconds (vs. 15+ minutes manual) |
| RM adoption of short lists | > 70% of queries result in action |
| Filter refinement success rate | < 5% zero-result queries |
| Lead generation quality (RM rating) | > 4/5 |
| Audit completeness | 100% of queries logged |
| Cross-sell lift from lead gen | +15% vs. baseline |

---

## 8. One-Sentence Summary

> The core value of this POC isn't "letting an LLM query the CRM" — it's **turning every RM's natural-language intent into a traceable, accumulative, multi-product short list, and turning every short list into profile-grounded, campaign-ready next best actions**. That's what private banking actually needs from AI.
>
> And it does so with an **explicit MCP tool boundary** — every step (intent parsing, condition accumulation, SQL generation, audit logging, profile fetching, lead generation) is a named responsibility, so the logic is inspectable, testable, and ready to grow into compliance checks, role-based access, or CRM integration.

---

**This document is designed to be read by business stakeholders, product owners, and compliance teams — not just engineers. The technical details live in the code; the business logic lives here.**