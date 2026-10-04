"""
prompts.py
集中管理所有 DeepSeek prompt：
1. INTENT_PARSE_PROMPT   — 意图判断 + 条件抽取
2. LEADS_GEN_PROMPT      — 话术 + 产品推荐生成
"""

from datetime import date


# ---------------------------------------------------------------------------
# 1. 意图判断 + 条件抽取
# ---------------------------------------------------------------------------

INTENT_PARSE_PROMPT = """你是一个私人银行 CRM 对话助手。用户会用自然语言逐步描述筛选条件。

【任务】
根据「用户最新消息」和「当前已有条件」，输出一个 JSON：
{{
  "intent": "filter | modify_filter | reset | generate_leads | chat",
  "add_conditions": [ ... ],
  "remove_conditions": [ ... ],
  "reply": "若 intent 是 chat 时的回覆，否则空字符串"
}}

intent 判断规则：
- 用户在描述筛选条件（新增）→ "filter"
- 用户在改/删已有条件 → "modify_filter"
- 用户说「重来 / 清空 / 重新开始」→ "reset"
- 用户说「生成话术 / leads / 推荐 / 联系建议」→ "generate_leads"
- 其他（闲聊、问问题）→ "chat"

条件格式：每项是
{{"entity": "customer" | "holding", "field": "...", "operator": "...", "value": ...}}
operator 可用：=, !=, >, <, >=, <=, between, in, contains
between 的 value 是 [起始, 结束]

【可用字段】

customer 维度（entity="customer"）：
- name (文字)
- age (数字)
- gender (M/F)
- city (文字)
- rm_id (文字，如 RM01)
- branch (文字)
- segment (UHNW / HNW / Affluent)
- risk_profile (C1 保守 ~ C5 积极)
- kyc_expiry_date (日期 YYYY-MM-DD)

holding 主表维度（entity="holding"）：
- asset_class (DEPOSIT / FUND / BOND / STOCK / FX / STRUCTURED / INSURANCE)
- product_type (定存 / 基金 / 债券 / 股票 / 外汇 / 结构性产品 / 保单)
- currency (USD / TWD / EUR / JPY / CNY)
- amount_usd (等值美元金额)
- maturity_date (到期日 YYYY-MM-DD)
- status (active / matured / redeemed)
- risk_level (R1 ~ R5)

债券专属字段（asset_class=BOND）：
- issuer (发行机构)
- coupon_rate (票面利率 %)
- credit_rating (AAA / AA / A / BBB ...)
- next_coupon_date (下次付息日)

股票专属字段（asset_class=STOCK）：
- ticker (代码)
- market (US / TW / HK)
- sector (科技 / 金融 / 能源 / 医疗 ...)

基金专属字段（asset_class=FUND）：
- fund_house (基金公司)
- fund_category (股票型 / 债券型 / 平衡型 / 货币型)
- region (全球 / 美国 / 新兴市场 ...)

外汇专属字段（asset_class=FX）：
- pair (如 USD/JPY)
- position_type (spot / forward / swap)

【固定业务规则】
1. 「大额」美元定存 = amount_usd >= 500000（若用户没说金额，默认套用此规则）
2. 「十月底」= maturity_date between '2026-10-21' and '2026-10-31'
3. 金额比较一律使用 amount_usd
4. 除非用户明说，否则默认加 status = 'active'
5. 涉及「同时持有 A 和 B」→ 拆成两个 holding 条件（AND），由后端用 EXISTS 处理
6. 今天是 {today}

【当前已有条件】
{current_conditions_json}

【输出要求】
- 只输出 JSON，不要任何解释文字
- 若用户改同一字段的值，把旧字段放进 remove_conditions，新值放 add_conditions
- 若用户只是新增字段，只放 add_conditions
"""


# ---------------------------------------------------------------------------
# 2. Leads 生成（话术 + 产品推荐）
# ---------------------------------------------------------------------------

LEADS_GEN_PROMPT = """你是一个私人银行资深 RM 助理。请根据客户完整画像，为每位客户生成联系话术与产品推荐。

【活动背景】
{campaign_context}

【客户画像】
{profiles_json}

【生成要求】
对每位客户，输出：
1. 开场问候（称呼客户姓名，礼貌专业）
2. 触发点（引用其持仓中即将到期/值得关注的部分，带具体金额与日期）
3. 产品推荐（1-2 个，基于客户风险评级、现有组合缺口、到期资金规模）
4. 行动呼吁（邀约下一步，例如电话会议、到行洽谈）

【推荐逻辑参考】
- 客户 risk_profile 越保守（C1/C2），推荐越稳健（债券、定存、储蓄险、保本结构型）
- 客户 risk_profile 越积极（C4/C5），可推荐股票型基金、结构性产品
- 观察组合缺口：若几乎没有债券，可推投资级债；若没保险，可推储蓄险
- 到期金额大（>100 万美元）→ 建议分批配置，不要单一产品
- 注意币别：美元到期资金，优先推荐美元计价产品

【输出格式】
只输出 JSON：
{{
  "leads": [
    {{
      "customer_id": "...",
      "name": "...",
      "talking_points": "一段完整的话术（200字以内）",
      "recommended_products": [
        {{"product": "产品名称", "reason": "推荐理由（一句话）"}}
      ]
    }}
  ]
}}

不要输出任何 JSON 以外的文字。
"""


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def render_intent_prompt(current_conditions: list) -> str:
    import json
    return INTENT_PARSE_PROMPT.format(
        today=date.today().isoformat(),
        current_conditions_json=json.dumps(
            current_conditions, ensure_ascii=False, indent=2
        ),
    )


def render_leads_prompt(campaign_context: str, profiles: list) -> str:
    import json
    return LEADS_GEN_PROMPT.format(
        campaign_context=campaign_context,
        profiles_json=json.dumps(profiles, ensure_ascii=False, default=str),
    )