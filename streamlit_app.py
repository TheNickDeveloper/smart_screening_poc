"""
streamlit_app.py
Streamlit 前端：CRM Smart Screening Chatbot
通过 st.session_state 维持对话，调用 MCP Server 完成筛选
"""

import asyncio
import json
import os
import uuid

import streamlit as st
from dotenv import load_dotenv

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from prompts import render_intent_prompt, render_leads_prompt

load_dotenv()

# ---------------------------------------------------------------------------
# 页面配置
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="CRM Smart Screening",
    page_icon="🏦",
    layout="wide",
)

st.title("🏦 CRM Smart Screening")
st.caption("私人银行客户筛选助手 · 自然语言对话 · 实时 Short List")

# ---------------------------------------------------------------------------
# Session State 初始化
# ---------------------------------------------------------------------------

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "conditions" not in st.session_state:
    st.session_state.conditions = []
if "last_result_ids" not in st.session_state:
    st.session_state.last_result_ids = []
if "last_result_data" not in st.session_state:
    st.session_state.last_result_data = []
if "campaign_context" not in st.session_state:
    st.session_state.campaign_context = "美元定存到期续存活动"


# ---------------------------------------------------------------------------
# MCP Server 调用（异步 → 同步封装）
# ---------------------------------------------------------------------------

async def _call_mcp_tool(tool_name: str, arguments: dict):
    """启动 MCP Server 子进程，调用指定 tool"""
    server_params = StdioServerParameters(
        command="python",
        args=["server.py"],
        env=None,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            return json.loads(result.content[0].text)


def call_mcp(tool_name: str, arguments: dict) -> dict:
    """同步封装 MCP 调用（Streamlit 不支持 async 主循环）"""
    try:
        return asyncio.run(_call_mcp_tool(tool_name, arguments))
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# DeepSeek 调用
# ---------------------------------------------------------------------------

def deepseek_json(system_prompt: str, user_prompt: str, max_tokens: int = 4096) -> dict:
    from openai import OpenAI

    client = OpenAI(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )
    resp = client.chat.completions.create(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
    )

    choice = resp.choices[0]
    content = choice.message.content

    if not content:
        raise RuntimeError("DeepSeek 返回空内容")
    if choice.finish_reason == "length":
        raise RuntimeError(
            f"输出被 max_tokens({max_tokens}) 截断，请调高或减少每批客户数量。"
        )

    return json.loads(content)


# ---------------------------------------------------------------------------
# 对话处理
# ---------------------------------------------------------------------------

def apply_condition_changes(add: list, remove: list):
    """先移除旧字段，再追加新条件"""
    if remove:
        remove_fields = {(r.get("entity"), r.get("field")) for r in remove}
        st.session_state.conditions = [
            c for c in st.session_state.conditions
            if (c.get("entity"), c.get("field")) not in remove_fields
        ]

    for cond in add or []:
        st.session_state.conditions = [
            c for c in st.session_state.conditions
            if not (c.get("entity") == cond.get("entity")
                    and c.get("field") == cond.get("field"))
        ]
        st.session_state.conditions.append(cond)


def process_message(user_msg: str) -> str:
    """处理用户消息，返回 Bot 回复（Markdown 文本）"""
    # 1. DeepSeek 解析意图
    system_prompt = "你是一个 CRM 助手，只输出 JSON。"
    user_prompt = render_intent_prompt(st.session_state.conditions) + \
        f"\n\n【用户最新消息】\n{user_msg}"

    try:
        parsed = deepseek_json(system_prompt, user_prompt, max_tokens=2048)
    except Exception as e:
        return f"**[解析失败]** {e}"

    intent = parsed.get("intent", "chat")

    # 2. reset
    if intent == "reset":
        st.session_state.conditions = []
        st.session_state.last_result_ids = []
        st.session_state.last_result_data = []
        return "已清空所有筛选条件。请告诉我新的筛选需求。"

    # 3. filter / modify_filter
    if intent in ("filter", "modify_filter"):
        apply_condition_changes(
            add=parsed.get("add_conditions", []),
            remove=parsed.get("remove_conditions", []),
        )

        if not st.session_state.conditions:
            return "目前没有筛选条件。请描述你想找的客户，例如「十月底美元大额定存到期的客人」。"

        result = call_mcp("screen_customers", {
            "conditions": st.session_state.conditions,
            "logic": "AND",
            "queried_by": "RM_STREAMLIT",
            "query_text": user_msg,
        })

        if "error" in result:
            return f"**[查询错误]** {result['error']}"

        customers = result.get("customers", [])
        st.session_state.last_result_ids = [c["customer_id"] for c in customers]
        st.session_state.last_result_data = customers

        # 格式化表格
        lines = [
            f"**当前条件**（{len(st.session_state.conditions)} 个）：",
            f"`{json.dumps(st.session_state.conditions, ensure_ascii=False)}`",
            "",
            f"找到 **{len(customers)}** 位客户：",
            "",
        ]

        if customers:
            lines.append("| 客户号 | 姓名 | 分层 | 风险 | AUM(USD) | RM | 联系方式 |")
            lines.append("|--------|------|------|------|----------|----|----------|")
            for c in customers:
                aum = f"{c.get('aum_usd', 0):,.0f}"
                lines.append(
                    f"| {c['customer_id']} | {c['name']} | {c.get('segment','')} "
                    f"| {c.get('risk_profile','')} | {aum} "
                    f"| {c.get('rm_id','')} | {c.get('contact_phone','')} |"
                )
            lines.append("")
            lines.append("---")
            lines.append("下一步你可以：")
            lines.append("- 继续加条件（例如：只要 UHNW、加风险评级 C1-C2）")
            lines.append("- 说「生成 leads」产出联系话术与产品推荐")
            lines.append("- 说「重来」清空条件")
        else:
            lines.append("（无符合条件的客户）")
            lines.append("")
            lines.append("要不要放宽条件再试？或说「重来」清空条件。")

        return "\n".join(lines)

    # 4. generate_leads（分批生成）
    if intent == "generate_leads":
        if not st.session_state.last_result_ids:
            return "还没有名单。请先描述筛选条件，例如「十月底美元大额定存到期的客人」。"

        result = call_mcp("generate_leads", {
            "customer_ids": st.session_state.last_result_ids,
            "queried_by": "RM_STREAMLIT",
        })

        if "error" in result:
            return f"**[画像拉取错误]** {result['error']}"

        profiles = result.get("profiles", [])
        if not profiles:
            return "没有可生成话术的客户。"

        lines = [f"**为 {len(profiles)} 位客户生成联系建议**（分批生成中）：", ""]

        # 分批：每批 5 位
        BATCH_SIZE = 5
        total_batches = (len(profiles) + BATCH_SIZE - 1) // BATCH_SIZE

        for i in range(0, len(profiles), BATCH_SIZE):
            batch = profiles[i:i + BATCH_SIZE]
            batch_no = i // BATCH_SIZE + 1

            prompt = render_leads_prompt(st.session_state.campaign_context, batch)
            try:
                leads_result = deepseek_json(
                    system_prompt="你是一个私人银行资深 RM 助理，只输出 JSON。",
                    user_prompt=prompt,
                    max_tokens=4096,
                )
            except Exception as e:
                lines.append(f"> ⚠️ 第 {batch_no}/{total_batches} 批生成失败：{e}")
                lines.append("")
                continue

            for lead in leads_result.get("leads", []):
                lines.append(f"### {lead['customer_id']} {lead['name']}")
                lines.append(f"**话术**：{lead['talking_points']}")
                if lead.get("recommended_products"):
                    lines.append("**推荐产品**：")
                    for p in lead["recommended_products"]:
                        lines.append(f"- {p['product']}：{p['reason']}")
                lines.append("")

        return "\n".join(lines)

    # 5. chat
    return parsed.get("reply", "我可以帮你筛选客户名单。请描述条件。")


# ---------------------------------------------------------------------------
# 侧边栏
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("⚙️ 设置")
    st.session_state.campaign_context = st.text_input(
        "活动背景",
        value=st.session_state.campaign_context,
    )

    st.divider()
    st.header("📋 当前条件")
    if st.session_state.conditions:
        for i, c in enumerate(st.session_state.conditions):
            st.code(
                f"{c.get('entity')}.{c.get('field')} "
                f"{c.get('operator')} {c.get('value')}"
            )
    else:
        st.caption("（无）")

    st.divider()
    if st.button("🗑️ 清空条件", use_container_width=True):
        st.session_state.conditions = []
        st.session_state.last_result_ids = []
        st.session_state.last_result_data = []
        st.rerun()

    st.divider()
    st.caption(f"Session: `{st.session_state.session_id[:8]}...`")


# ---------------------------------------------------------------------------
# 聊天历史显示
# ---------------------------------------------------------------------------

for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ---------------------------------------------------------------------------
# 用户输入
# ---------------------------------------------------------------------------

user_input = st.chat_input("描述你想筛选的客户，例如：十月底美元大额定存到期的客人")

if user_input:
    # 显示用户消息
    st.session_state.chat_history.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # 处理并显示 Bot 回复
    with st.chat_message("assistant"):
        with st.spinner("处理中..."):
            reply = process_message(user_input)
        st.markdown(reply)

    st.session_state.chat_history.append({"role": "assistant", "content": reply})