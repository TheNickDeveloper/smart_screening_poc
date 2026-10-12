"""
app.py
Streamlit front end: CRM Smart Screening Chatbot.
Keeps conversation state in st.session_state and calls the MCP Server to screen.
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
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="CRM Smart Screening",
    page_icon="🏦",
    layout="wide",
)

st.title("🏦 CRM Smart Screening")
st.caption("Private-banking client screening assistant \u00b7 natural-language chat \u00b7 live short list")

# ---------------------------------------------------------------------------
# Session state initialisation
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
    st.session_state.campaign_context = "USD time deposit maturity renewal campaign"


# ---------------------------------------------------------------------------
# MCP Server calls (async wrapped for sync use)
# ---------------------------------------------------------------------------

async def _call_mcp_tool(tool_name: str, arguments: dict):
    """Start the MCP Server subprocess and call the given tool."""
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
    """Blocking wrapper around the MCP call (Streamlit has no async main loop)."""
    try:
        return asyncio.run(_call_mcp_tool(tool_name, arguments))
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# DeepSeek calls
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
        raise RuntimeError("DeepSeek returned empty content")
    if choice.finish_reason == "length":
        raise RuntimeError(
            f"Output truncated by max_tokens({max_tokens}); raise the limit or "
            f"reduce the number of clients per batch."
        )

    return json.loads(content)


# ---------------------------------------------------------------------------
# Conversation handling
# ---------------------------------------------------------------------------

def apply_condition_changes(add: list, remove: list):
    """Remove matching old fields first, then append the new conditions."""
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
    """Handle a user message and return the bot reply as Markdown."""
    # 1. Parse the intent with DeepSeek
    system_prompt = "You are a CRM assistant. Output JSON only."
    user_prompt = render_intent_prompt(st.session_state.conditions) + \
        f"\n\n[LATEST USER MESSAGE]\n{user_msg}"

    try:
        parsed = deepseek_json(system_prompt, user_prompt, max_tokens=2048)
    except Exception as e:
        return f"**[PARSE FAILED]** {e}"

    intent = parsed.get("intent", "chat")

    # 2. reset
    if intent == "reset":
        st.session_state.conditions = []
        st.session_state.last_result_ids = []
        st.session_state.last_result_data = []
        return "All screening conditions cleared. Tell me the new criteria."

    # 3. filter / modify_filter
    if intent in ("filter", "modify_filter"):
        apply_condition_changes(
            add=parsed.get("add_conditions", []),
            remove=parsed.get("remove_conditions", []),
        )

        if not st.session_state.conditions:
            return ("No screening conditions yet. Describe the clients you want, "
                    "e.g. \"USD large time deposits maturing at the end of October\".")

        result = call_mcp("screen_customers", {
            "conditions": st.session_state.conditions,
            "logic": "AND",
            "queried_by": "RM_STREAMLIT",
            "query_text": user_msg,
        })

        if "error" in result:
            return f"**[QUERY ERROR]** {result['error']}"

        customers = result.get("customers", [])
        st.session_state.last_result_ids = [c["customer_id"] for c in customers]
        st.session_state.last_result_data = customers

        # Format the result table
        lines = [
            f"**Current conditions** ({len(st.session_state.conditions)}):",
            f"`{json.dumps(st.session_state.conditions, ensure_ascii=False)}`",
            "",
            f"Found **{len(customers)}** clients:",
            "",
        ]

        if customers:
            lines.append(
                "| Client ID | Name | Segment | Risk | AUM(USD) | Maturity | RM | Contact |"
            )
            lines.append(
                "|--------|------|------|------|----------|----------|----|----------|"
            )
            for c in customers:
                aum = f"{c.get('aum_usd', 0):,.0f}"
                maturity = c.get("matched_maturity_date") or "-"
                lines.append(
                    f"| {c['customer_id']} | {c['name']} | {c.get('segment','')} "
                    f"| {c.get('risk_profile','')} | {aum} | {maturity} "
                    f"| {c.get('rm_id','')} | {c.get('contact_phone','')} |"
                )
            lines.append("")
            lines.append("---")
            lines.append("Next you can:")
            lines.append("- Add more conditions (e.g. UHNW only, risk C1-C2)")
            lines.append("- Type \"generate leads\" to produce talking points and product picks")
            lines.append("- Type \"start over\" to clear the conditions")
        else:
            lines.append("(no matching clients)")
            lines.append("")
            lines.append("Try relaxing the conditions, or type \"start over\" to clear them.")

        return "\n".join(lines)

    # 4. generate_leads (batched)
    if intent == "generate_leads":
        if not st.session_state.last_result_ids:
            return ("No short list yet. Describe the screening conditions first, "
                    "e.g. \"USD large time deposits maturing at the end of October\".")

        result = call_mcp("generate_leads", {
            "customer_ids": st.session_state.last_result_ids,
            "queried_by": "RM_STREAMLIT",
        })

        if "error" in result:
            return f"**[PROFILE FETCH ERROR]** {result['error']}"

        profiles = result.get("profiles", [])
        if not profiles:
            return "No clients to generate talking points for."

        lines = [f"**Generating outreach suggestions for {len(profiles)} clients** (batched):", ""]

        # Batch size: 5 clients per call
        BATCH_SIZE = 5
        total_batches = (len(profiles) + BATCH_SIZE - 1) // BATCH_SIZE

        for i in range(0, len(profiles), BATCH_SIZE):
            batch = profiles[i:i + BATCH_SIZE]
            batch_no = i // BATCH_SIZE + 1

            prompt = render_leads_prompt(st.session_state.campaign_context, batch)
            try:
                leads_result = deepseek_json(
                    system_prompt="You are a senior private-banking RM assistant. Output JSON only.",
                    user_prompt=prompt,
                    max_tokens=4096,
                )
            except Exception as e:
                lines.append(f"> \u26a0\ufe0f Batch {batch_no}/{total_batches} failed: {e}")
                lines.append("")
                continue

            for lead in leads_result.get("leads", []):
                lines.append(f"### {lead['customer_id']} {lead['name']}")
                lines.append(f"**Talking points**: {lead['talking_points']}")
                if lead.get("recommended_products"):
                    lines.append("**Recommended products**:")
                    for p in lead["recommended_products"]:
                        lines.append(f"- {p['product']}：{p['reason']}")
                lines.append("")

        return "\n".join(lines)

    # 5. chat
    return parsed.get("reply", "I can help you screen the client list. Describe your criteria.")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.header("\u2699\ufe0f Settings")
    st.session_state.campaign_context = st.text_input(
        "Campaign context",
        value=st.session_state.campaign_context,
    )

    st.divider()
    st.header("\U0001f4cb Current conditions")
    if st.session_state.conditions:
        for i, c in enumerate(st.session_state.conditions):
            st.code(
                f"{c.get('entity')}.{c.get('field')} "
                f"{c.get('operator')} {c.get('value')}"
            )
    else:
        st.caption("(none)")

    st.divider()
    if st.button("\U0001f5d1\ufe0f Clear conditions", use_container_width=True):
        st.session_state.conditions = []
        st.session_state.last_result_ids = []
        st.session_state.last_result_data = []
        st.rerun()

    st.divider()
    st.caption(f"Session: `{st.session_state.session_id[:8]}...`")


# ---------------------------------------------------------------------------
# Chat history
# ---------------------------------------------------------------------------

for msg in st.session_state.chat_history:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# ---------------------------------------------------------------------------
# User input
# ---------------------------------------------------------------------------

user_input = st.chat_input("Describe the clients you want, e.g. USD large time deposits maturing at the end of October")

if user_input:
    # Show the user message
    st.session_state.chat_history.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    # Process and show the bot reply
    with st.chat_message("assistant"):
        with st.spinner("Working..."):
            reply = process_message(user_input)
        st.markdown(reply)

    st.session_state.chat_history.append({"role": "assistant", "content": reply})

    # The sidebar is rendered before process_message() runs, so without a rerun
    # it would keep showing the previous turn's conditions. Rerunning replays the
    # chat from history (no extra API call) with the sidebar now up to date.
    st.rerun()