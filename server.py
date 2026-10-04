"""
server.py
MCP Server：CRM Smart Screening
- Tool 1: screen_customers    根据结构化条件筛选客户
- Tool 2: generate_leads      为指定名单生成话术 + 产品推荐

用法（供 MCP client 以 stdio 启动）：
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
# 字段白名单：防止 LLM 幻觉出不存在的字段
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

# 各子表字段 → 对应明细表
DETAIL_TABLE_MAP = {
    # 债券
    "issuer": "bond_details",
    "coupon_rate": "bond_details",
    "coupon_freq": "bond_details",
    "credit_rating": "bond_details",
    "next_coupon_date": "bond_details",
    # 股票
    "ticker": "stock_details",
    "market": "stock_details",
    "shares": "stock_details",
    "cost_price": "stock_details",
    "current_price": "stock_details",
    "sector": "stock_details",
    # 基金
    "fund_code": "fund_details",
    "fund_house": "fund_details",
    "fund_category": "fund_details",
    "nav": "fund_details",
    "units": "fund_details",
    "region": "fund_details",
    # 外汇
    "pair": "fx_details",
    "position_type": "fx_details",
    "forward_rate": "fx_details",
    # 定存
    "interest_rate": "deposit_details",
    "term_months": "deposit_details",
    "auto_renew": "deposit_details",
    # 结构性产品
    "underlying": "structured_details",
    "barrier_level": "structured_details",
    "strike_price": "structured_details",
    "capital_protected": "structured_details",
    # 保单
    "policy_type": "insurance_details",
    "premium": "insurance_details",
    "insured_person": "insurance_details",
    "beneficiary": "insurance_details",
}

# 子表字段 → 该子表对应哪个 asset_class（用于 EXISTS 里过滤主表）
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
# SQL 构建
# ---------------------------------------------------------------------------

def _render_condition(field: str, operator: str, value):
    """把单个条件转成 (SQL 片段, 参数列表)"""
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
    """客户主档条件 → (sql_clause, params)"""
    field = cond["field"]
    if field not in CUSTOMER_FIELDS:
        raise ValueError(f"Unknown customer field: {field}")

    sql_field = f"c.{field}"
    clause, params = _render_condition(sql_field, cond["operator"], cond["value"])
    return clause, params


def _build_holding_condition(cond):
    """
    持仓条件 → EXISTS 子查询
    支持两种：
      - 主表字段 (asset_class, currency, amount_usd, maturity_date...)
      - 子表字段 (credit_rating, sector, fund_category...)
    """
    field = cond["field"]
    operator = cond["operator"]
    value = cond["value"]

    if field in HOLDING_FIELDS:
        # 纯主表条件
        sql_field = f"h.{field}"
        clause, params = _render_condition(sql_field, operator, value)
        sql = (
            f"EXISTS (SELECT 1 FROM holdings h "
            f"WHERE h.customer_id = c.customer_id AND {clause})"
        )
        return sql, params

    if field in DETAIL_TABLE_MAP:
        # 子表条件 → JOIN 明细表
        detail_table = DETAIL_TABLE_MAP[field]
        asset_class = DETAIL_ASSET_CLASS[detail_table]
        alias = detail_table[0]  # 用首字母当别名，例如 b, s, f
        sql_field = f"{alias}.{field}"
        clause, params = _render_condition(sql_field, operator, value)
        sql = (
            f"EXISTS (SELECT 1 FROM holdings h "
            f"JOIN {detail_table} {alias} ON h.holding_id = {alias}.holding_id "
            f"WHERE h.customer_id = c.customer_id "
            f"AND h.asset_class = '{asset_class}' "
            f"AND {clause})"
        )
        return sql, params

    raise ValueError(f"Unknown holding field: {field}")


def build_sql(conditions, logic="AND"):
    """
    conditions: [{"entity": "customer"/"holding", "field":..., "operator":..., "value":...}]
    返回 (sql, params)
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

    sql = f"""
    SELECT
        c.customer_id, c.name, c.segment, c.risk_profile,
        c.rm_id, c.rm_name, c.branch, c.contact_phone, c.contact_email,
        a.aum_usd, a.aum_deposit, a.aum_fund, a.aum_bond,
        a.aum_stock, a.aum_fx, a.aum_structured, a.aum_insurance
    FROM customers c
    JOIN customer_aum a ON a.customer_id = c.customer_id
    WHERE {where}
    ORDER BY a.aum_usd DESC
    """
    return sql, params


def execute_screen(conditions, logic="AND"):
    """执行筛选并返回结果列表"""
    sql, params = build_sql(conditions, logic)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows], sql, params
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 审计日志
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
# 客户画像（供 generate_leads 用）
# ---------------------------------------------------------------------------

def fetch_customer_profiles(customer_ids):
    """拉出客户的完整画像 + 持仓，供话术生成"""
    if not customer_ids:
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ",".join(["?"] * len(customer_ids))

        # 客户主档 + AUM
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
                # 附加明细
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
# MCP Tool 定义
# ---------------------------------------------------------------------------

@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="screen_customers",
            description=(
                "根据结构化条件筛选私人银行客户，返回 short list。"
                "conditions 为条件数组，每项含 entity(customer/holding)、field、operator、value。"
                "支持客户维度字段与持仓维度字段（含债券/股票/基金/外汇/定存/结构性/保单明细）。"
                "金额比较一律使用 amount_usd。多个持仓条件用 AND 时会以 EXISTS 独立判断"
                "（即『同时持有 A 和 B』语义）。"
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
                "为指定客户名单抓取完整画像（AUM、持仓明细、风险评级等），"
                "返回结构化的客户资料供上层 LLM 生成联系话术与产品推荐。"
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

        # 审计
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
# 启动
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())