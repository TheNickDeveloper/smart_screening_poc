"""
server.py
MCP Server: CRM Smart Screening
- Tool 1: screen_customers    screen clients from structured conditions
- Tool 2: generate_leads      fetch profiles for talking points + product picks

Usage (started by an MCP client over stdio):
    python server.py
"""

import sqlite3
import json
from datetime import datetime
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

DB_PATH = "crm.db"

server = Server("crm-screener")


# ---------------------------------------------------------------------------
# Field whitelist: stops the LLM from inventing non-existent columns
# ---------------------------------------------------------------------------

CUSTOMER_FIELDS = {
    "customer_id", "name", "age", "gender", "nationality", "city",
    "rm_id", "rm_name", "branch", "segment", "risk_profile",
    "kyc_expiry_date", "onboard_date", "contact_phone", "contact_email",
    "is_active"
}

HOLDING_FIELDS = {
    "asset_class", "product_type", "product_name", "currency",
    "amount", "amount_usd", "start_date", "maturity_date", "status",
    "risk_level"
}

# Detail-table fields -> owning detail table
DETAIL_TABLE_MAP = {
    # Bond
    "issuer": "bond_details",
    "coupon_rate": "bond_details",
    "coupon_freq": "bond_details",
    "credit_rating": "bond_details",
    "next_coupon_date": "bond_details",
    # Stock
    "ticker": "stock_details",
    "market": "stock_details",
    "shares": "stock_details",
    "cost_price": "stock_details",
    "current_price": "stock_details",
    "sector": "stock_details",
    # Fund
    "fund_code": "fund_details",
    "fund_house": "fund_details",
    "fund_category": "fund_details",
    "nav": "fund_details",
    "units": "fund_details",
    "region": "fund_details",
    # FX
    "pair": "fx_details",
    "position_type": "fx_details",
    "forward_rate": "fx_details",
    # Time deposit
    "interest_rate": "deposit_details",
    "term_months": "deposit_details",
    "auto_renew": "deposit_details",
    # Structured product
    "underlying": "structured_details",
    "barrier_level": "structured_details",
    "strike_price": "structured_details",
    "capital_protected": "structured_details",
    # Insurance policy
    "policy_type": "insurance_details",
    "premium": "insurance_details",
    "insured_person": "insurance_details",
    "beneficiary": "insurance_details",
}

# Detail table -> its asset_class (used to constrain the EXISTS subquery)
DETAIL_ASSET_CLASS = {
    "bond_details": "BOND",
    "stock_details": "STOCK",
    "fund_details": "FUND",
    "fx_details": "FX",
    "deposit_details": "DEPOSIT",
    "structured_details": "STRUCTURED",
    "insurance_details": "INSURANCE",
}

OP_MAP = {
    "=": "=", "!=": "!=", ">": ">", "<": "<", ">=": ">=", "<=": "<="
}


# ---------------------------------------------------------------------------
# SQL building
# ---------------------------------------------------------------------------

def _render_condition(field: str, operator: str, value):
    """Turn a single condition into (SQL fragment, parameter list)."""
    if operator == "in":
        placeholders = ",".join(["?"] * len(value))
        return f"{field} IN ({placeholders})", list(value)

    if operator == "contains":
        return f"{field} LIKE ?", [f"%{value}%"]

    if operator == "between":
        return f"{field} BETWEEN ? AND ?", [value[0], value[1]]

    if operator in OP_MAP:
        return f"{field} {OP_MAP[operator]} ?", [value]

    raise ValueError(f"Unsupported operator: {operator}")


def _build_customer_condition(cond):
    """Customer master condition -> (sql_clause, params)."""
    field = cond["field"]
    if field not in CUSTOMER_FIELDS:
        raise ValueError(f"Unknown customer field: {field}")

    sql_field = f"c.{field}"
    clause, params = _render_condition(sql_field, cond["operator"], cond["value"])
    return clause, params


def _holding_predicate(cond, h_alias="h"):
    """One holding condition as a predicate over an arbitrary holdings alias."""
    field = cond["field"]
    operator = cond["operator"]
    value = cond["value"]

    if field in HOLDING_FIELDS:
        return _render_condition(f"{h_alias}.{field}", operator, value)

    if field in DETAIL_TABLE_MAP:
        detail_table = DETAIL_TABLE_MAP[field]
        asset_class = DETAIL_ASSET_CLASS[detail_table]
        alias = f"{h_alias}{detail_table[0]}"  # unique per (holdings alias, detail table)
        clause, params = _render_condition(f"{alias}.{field}", operator, value)
        sql = (
            f"EXISTS (SELECT 1 FROM holdings {h_alias} "
            f"JOIN {detail_table} {alias} ON {h_alias}.holding_id = {alias}.holding_id "
            f"WHERE {h_alias}.customer_id = c.customer_id "
            f"AND {h_alias}.asset_class = '{asset_class}' "
            f"AND {clause})"
        )
        return sql, params

    raise ValueError(f"Unknown holding field: {field}")


def _build_holding_condition(cond):
    """
    Holding condition -> EXISTS subquery.
    Two kinds are supported:
      - holdings columns (asset_class, currency, amount_usd, maturity_date...)
      - detail-table columns (credit_rating, sector, fund_category...)
    """
    field = cond["field"]

    if field in HOLDING_FIELDS:
        # Holdings-table condition
        clause, params = _render_condition(f"h.{field}", cond["operator"], cond["value"])
        sql = (
            f"EXISTS (SELECT 1 FROM holdings h "
            f"WHERE h.customer_id = c.customer_id AND {clause})"
        )
        return sql, params

    if field in DETAIL_TABLE_MAP:
        # Detail-table condition -> JOIN the detail table
        return _holding_predicate(cond, "h")

    raise ValueError(f"Unknown holding field: {field}")


def _maturity_subquery(holding_conditions, h_alias, joiner):
    """MIN(maturity_date) over holdings matching the given holding conditions."""
    parts = [f"{h_alias}.maturity_date IS NOT NULL"]
    params = []

    # Only constrain status when the screen does not already do so, otherwise a
    # caller asking for matured holdings would get an empty subquery.
    if not any(c.get("field") == "status" for c in holding_conditions):
        parts.append(f"{h_alias}.status = 'active'")

    for cond in holding_conditions:
        clause, p = _holding_predicate(cond, h_alias)
        parts.append(f"({clause})")
        params.extend(p)

    combined = f" {joiner} ".join(parts)
    # The parentheses matter: AND binds tighter than OR, so without them only
    # the first part would stay correlated to the outer customer and the rest
    # would match holdings belonging to every customer.
    sql = (
        f"(SELECT MIN({h_alias}.maturity_date) FROM holdings {h_alias} "
        f"WHERE {h_alias}.customer_id = c.customer_id AND ({combined}))"
    )
    return sql, params


def _matched_maturity_expr(holding_conditions):
    """Column expression: the maturity date relevant to this screen.

    Result rows are customer-level, so the maturity date has to be resolved from
    the holdings that matched. Cross-product screens ("holds A AND holds B")
    cannot be satisfied by one holding, so COALESCE falls back to the earliest
    maturity among holdings matching any single condition.
    """
    if not holding_conditions:
        return _maturity_subquery([], "h2", "AND")

    and_sql, and_params = _maturity_subquery(holding_conditions, "h2", "AND")
    any_sql, any_params = _maturity_subquery(holding_conditions, "h3", "OR")
    return f"COALESCE({and_sql}, {any_sql})", and_params + any_params


def build_sql(conditions, logic="AND"):
    """
    conditions: [{"entity": "customer"/"holding", "field":..., "operator":..., "value":...}]
    Returns (sql, params).
    """
    clauses, params = [], []

    for cond in conditions:
        entity = cond.get("entity", "holding")
        if entity == "customer":
            clause, p = _build_customer_condition(cond)
        elif entity == "holding":
            clause, p = _build_holding_condition(cond)
        else:
            raise ValueError(f"Unknown entity: {entity}")
        clauses.append(clause)
        params.extend(p)

    where = f" {logic} ".join(clauses) if clauses else "1=1"

    holding_conditions = [
        c for c in conditions if c.get("entity", "holding") == "holding"
    ]
    maturity_expr, maturity_params = _matched_maturity_expr(holding_conditions)

    sql = f"""
    SELECT
        c.customer_id, c.name, c.segment, c.risk_profile,
        c.rm_id, c.rm_name, c.branch, c.contact_phone, c.contact_email,
        a.aum_usd, a.aum_deposit, a.aum_fund, a.aum_bond,
        a.aum_stock, a.aum_fx, a.aum_structured, a.aum_insurance,
        {maturity_expr} AS matched_maturity_date
    FROM customers c
    JOIN customer_aum a ON a.customer_id = c.customer_id
    WHERE {where}
    ORDER BY a.aum_usd DESC
    """
    # The subquery sits in the SELECT list, so its placeholders bind first.
    return sql, maturity_params + params


def execute_screen(conditions, logic="AND"):
    """Run the screen and return the result rows."""
    sql, params = build_sql(conditions, logic)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows], sql, params
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def write_audit(queried_by, query_text, parsed_conditions,
                returned_ids, action_type):
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """INSERT INTO audit_log
               (queried_by, query_text, parsed_conditions,
                returned_ids, result_count, action_type, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                queried_by,
                query_text,
                json.dumps(parsed_conditions, ensure_ascii=False),
                json.dumps(returned_ids, ensure_ascii=False),
                len(returned_ids),
                action_type,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Client profiles (used by generate_leads)
# ---------------------------------------------------------------------------

def fetch_customer_profiles(customer_ids):
    """Fetch full profiles plus holdings for talking-point generation."""
    if not customer_ids:
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join(["?"] * len(customer_ids))

        # Customer master + AUM
        customers = conn.execute(
            f"""SELECT * FROM customer_aum
                WHERE customer_id IN ({placeholders})""",
            customer_ids,
        ).fetchall()

        profiles = []
        for c in customers:
            cid = c["customer_id"]

            holdings = conn.execute(
                """SELECT holding_id, asset_class, product_type, product_name,
                          currency, amount, amount_usd, start_date,
                          maturity_date, status, risk_level
                   FROM holdings
                   WHERE customer_id = ? AND status = 'active'""",
                (cid,),
            ).fetchall()

            holding_list = []
            for h in holdings:
                h_dict = dict(h)
                # Attach detail rows
                hid = h["holding_id"]
                ac = h["asset_class"]

                if ac == "BOND":
                    d = conn.execute(
                        "SELECT * FROM bond_details WHERE holding_id=?",
                        (hid,)).fetchone()
                elif ac == "STOCK":
                    d = conn.execute(
                        "SELECT * FROM stock_details WHERE holding_id=?",
                        (hid,)).fetchone()
                elif ac == "FUND":
                    d = conn.execute(
                        "SELECT * FROM fund_details WHERE holding_id=?",
                        (hid,)).fetchone()
                elif ac == "FX":
                    d = conn.execute(
                        "SELECT * FROM fx_details WHERE holding_id=?",
                        (hid,)).fetchone()
                elif ac == "DEPOSIT":
                    d = conn.execute(
                        "SELECT * FROM deposit_details WHERE holding_id=?",
                        (hid,)).fetchone()
                elif ac == "STRUCTURED":
                    d = conn.execute(
                        "SELECT * FROM structured_details WHERE holding_id=?",
                        (hid,)).fetchone()
                elif ac == "INSURANCE":
                    d = conn.execute(
                        "SELECT * FROM insurance_details WHERE holding_id=?",
                        (hid,)).fetchone()
                else:
                    d = None

                if d:
                    h_dict["details"] = dict(d)
                holding_list.append(h_dict)

            profile = dict(c)
            profile["holdings"] = holding_list
            profiles.append(profile)

        return profiles
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# MCP tool definitions
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="screen_customers",
            description=(
                "Screen private-banking clients from structured conditions and return a short list. "
                "conditions is an array; each item has entity (customer/holding), field, operator, value. "
                "Both customer-level and holding-level fields are supported, including bond, stock, "
                "fund, FX, time-deposit, structured-product and insurance detail columns. "
                "Always compare amounts on amount_usd. Multiple holding conditions combined with AND "
                "are evaluated as independent EXISTS subqueries, i.e. the \"holds A and also holds B\" "
                "semantics. Each returned row also carries matched_maturity_date, the maturity "
                "date of the holding that satisfied the holding conditions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "conditions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "entity": {
                                    "type": "string",
                                    "enum": ["customer", "holding"],
                                },
                                "field": {"type": "string"},
                                "operator": {
                                    "type": "string",
                                    "enum": ["=", "!=", ">", "<", ">=", "<=",
                                             "between", "in", "contains"],
                                },
                                "value": {},
                            },
                            "required": ["entity", "field", "operator", "value"],
                        },
                    },
                    "logic": {
                        "type": "string",
                        "enum": ["AND", "OR"],
                        "default": "AND",
                    },
                    "queried_by": {"type": "string"},
                    "query_text": {"type": "string"},
                },
                "required": ["conditions"],
            },
        ),
        Tool(
            name="generate_leads",
            description=(
                "Fetch full profiles for the given client IDs (AUM, holding details, risk rating, "
                "etc.) and return structured client data so the calling LLM can write outreach "
                "talking points and product recommendations."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "queried_by": {"type": "string"},
                },
                "required": ["customer_ids"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "screen_customers":
        conditions = arguments["conditions"]
        logic = arguments.get("logic", "AND")
        queried_by = arguments.get("queried_by", "unknown")
        query_text = arguments.get("query_text", "")

        try:
            results, sql, params = execute_screen(conditions, logic)
        except Exception as e:
            return [TextContent(type="text", text=json.dumps(
                {"error": str(e)}, ensure_ascii=False))]

        # Audit
        write_audit(
            queried_by=queried_by,
            query_text=query_text,
            parsed_conditions={"conditions": conditions, "logic": logic},
            returned_ids=[r["customer_id"] for r in results],
            action_type="screen",
        )

        payload = {
            "count": len(results),
            "customers": results,
            "sql_debug": sql.strip(),
            "params_debug": params,
        }
        return [TextContent(type="text", text=json.dumps(
            payload, ensure_ascii=False, default=str))]

    elif name == "generate_leads":
        customer_ids = arguments["customer_ids"]
        queried_by = arguments.get("queried_by", "unknown")

        profiles = fetch_customer_profiles(customer_ids)

        write_audit(
            queried_by=queried_by,
            query_text="generate_leads",
            parsed_conditions={"customer_ids": customer_ids},
            returned_ids=customer_ids,
            action_type="generate_leads",
        )

        payload = {"count": len(profiles), "profiles": profiles}
        return [TextContent(type="text", text=json.dumps(
            payload, ensure_ascii=False, default=str))]

    raise ValueError(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())