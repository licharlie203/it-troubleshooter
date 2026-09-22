# -*- coding: utf-8 -*-
"""
排查 Agent：把「故障排查」做成模型自主决策（Function Calling）
================================================================

这是这个项目和 ai-doc-qa 最大的不同，也是「AI Agent」三个字真正的落点。

ai-doc-qa 里的 Agent 只有「查文档」「数词」这类通用工具；
这里换成**面向故障排查的专业工具**，让模型扮演一个运维老手的角色：

    用户：「打印机打不出来了，显示脱机」
      ↓
    模型思考：这是个打印类故障 → 调用 search_similar(症状) 找相似历史案例
      ↓
    拿到 3 个相似案例（都含解决步骤）
      ↓
    模型综合：生成「最可能的原因 + 分步排查清单 + 每一步的依据」

核心思想（面试金句）：
    知识库（RAG）解决「有没有先例可查」；
    Agent（工具调用）解决「怎么把先例变成可执行的排查动作」；
    两者合起来，才是企业要的「故障排查助手」，而不是一个搜索引擎。

工具设计（对比 ai-doc-qa 的 search_document，这次更专业）：
    1. search_similar_cases  —— 按症状/关键词找相似历史案例（向量检索 + 兜底关键词）
    2. list_categories       —— 看看库里有哪些故障分类（帮助模型判断问题属于哪类）
    3. get_case_detail       —— 按标题/id 取某条案例的完整解决步骤
"""

import json
import requests

import db
import vector_search

# 通义千问的 OpenAI 兼容接口地址（和 ai-doc-qa 一样）
BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MAX_STEPS = 6  # 最多让模型调几轮工具


# ==================== 第一步：工具说明书（写给模型看的） ====================
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_similar_cases",
            "description": (
                "在故障知识库里检索和历史故障最相似的案例。"
                "用户描述一个故障现象时，先用这个工具找出相似的已解决案例，"
                "拿到它们的症状、原因、解决步骤作为排查依据。"
                "不要凭自己的记忆猜测解决方案，必须基于检索到的案例。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "用故障现象的关键词或短句描述，如『打印机脱机无法打印』",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "需要几个相似案例，默认 3，复杂可 5",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_categories",
            "description": "列出知识库里目前有哪些故障分类，帮助判断当前故障属于哪一类。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_case_detail",
            "description": "按案例标题或 ID 取某条案例的完整信息（症状、原因、全部解决步骤）。当检索结果里某条案例看起来很相关、需要看完整步骤时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title_or_id": {
                        "type": "string",
                        "description": "案例标题，或案例 ID（数字字符串）",
                    },
                },
                "required": ["title_or_id"],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "你是一名资深 IT 运维工程师，负责故障排查。你手里有一个故障知识库，记录了历史故障的症状、原因和解决步骤。\n"
    "工作规则：\n"
    "1. 用户描述故障现象后，必须先调用 search_similar_cases 工具查相似案例，再基于案例给出排查建议，禁止凭空编造。\n"
    "2. 如果一次没查到有用的，可以换个说法再查一次，最多试 3 次。\n"
    "3. 回答要结构化：先判断故障可能的分类和原因，再给出『分步排查清单』（按先易后难排序），每一步标注依据来自哪条案例。\n"
    "4. 如果库里确实没有相似案例，直接说明『知识库里暂无类似案例』，并给出通用排查思路（标注这是通用建议、非案例依据）。\n"
    "5. 排查步骤要具体、可执行，不要只说『检查网络』这种空话，要说『用 ipconfig 看是否拿到 IP』。\n"
    "6. 【多轮追问】如果用户给的信息不足以定位故障（例如没说是台式机还是笔记本、没给报错原文、"
    "没说有线还是无线、没说单个用户还是全员），**先别急着给方案**，直接用一两句问清最关键的 1-2 个信息，"
    "等用户补充后再检索案例给方案。追问要简短直白，一次最多问两点，不要罗列一堆问题。\n"
    "7. 【多轮补充】用户在后续对话里补充了信息时，要结合前文一起判断，接着往下排查，"
    "不要说『你之前没说过』这类话，也不要重复已经排查过的步骤。"
)


# ==================== 第二步：真正的工具执行（Python 干） ====================
def _keyword_search(query, top_k=5):
    """兜底检索：纯关键词，SQL LIKE 匹配标题/症状/标签。向量不可用时也能用。"""
    # 把用户口语拆成关键词，逐个做 OR 匹配，比整句 LIKE 命中率高
    words = [w for w in query.replace("，", " ").replace(",", " ").split() if len(w) >= 2]
    if not words:
        words = [query]
    like_clauses = " OR ".join(["title LIKE ?" for _ in words] + ["symptom LIKE ?" for _ in words] + ["tags LIKE ?" for _ in words])
    params = []
    for w in words:
        params.append(f"%{w}%")
    for w in words:
        params.append(f"%{w}%")
    for w in words:
        params.append(f"%{w}%")
    sql = f"SELECT * FROM cases WHERE {like_clauses} LIMIT ?"
    params.append(top_k)
    conn = db.get_conn()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _tool_search_similar_cases(query, top_k=3, api_key=None, embed_model=None):
    query = (query or "").strip()
    if not query:
        return "（检索词为空，没查到）"

    # 优先语义检索（embedding），失败或结果太少再降级到关键词
    matches = []
    try:
        if api_key:
            case_ids, scores = vector_search.search(query, api_key, top_k=max(top_k, 5), model=embed_model or "text-embedding-v3")
            for cid in case_ids:
                c = db.get_case(cid)
                if c:
                    matches.append(c)
    except Exception:
        matches = []

    if not matches:
        matches = _keyword_search(query, top_k=top_k)

    results = []
    for c in matches[:top_k]:
        results.append(
            f"[案例 {c['id']}] {c['title']}（分类：{c['category']}）\n"
            f"  症状：{c['symptom']}\n"
            f"  原因：{c['cause']}\n"
            f"  步骤：{c['steps']}"
        )
    if not results:
        return "（知识库里没有匹配『%s』的案例）" % query
    return "\n\n".join(results)


def _tool_list_categories():
    cats = db.all_categories()
    if not cats:
        return "（知识库目前是空的，还没有任何案例）"
    return "当前故障分类：" + "、".join(cats)


def _tool_get_case_detail(title_or_id):
    title_or_id = (title_or_id or "").strip()
    case = None
    if title_or_id.isdigit():
        case = db.get_case(int(title_or_id))
    if not case:
        conn = db.get_conn()
        row = conn.execute("SELECT * FROM cases WHERE title = ? LIMIT 1", (title_or_id,)).fetchone()
        conn.close()
        case = dict(row) if row else None
    if not case:
        return "（没有找到标题或 ID 为『%s』的案例）" % title_or_id
    return (
        f"案例《{case['title']}》\n"
        f"  分类：{case['category']}\n"
        f"  症状：{case['symptom']}\n"
        f"  原因：{case['cause']}\n"
        f"  解决步骤：\n{case['steps']}"
    )


def dispatch_tool(name, args, api_key=None, embed_model=None):
    """工具分发：模型点菜，我们下厨。"""
    if name == "search_similar_cases":
        return _tool_search_similar_cases(args.get("query", ""), int(args.get("top_k") or 3), api_key, embed_model)
    if name == "list_categories":
        return _tool_list_categories()
    if name == "get_case_detail":
        return _tool_get_case_detail(args.get("title_or_id", ""))
    return f"（没有名为 {name} 的工具）"


# ==================== 第三步：Agent 主循环 ====================
def _chat(messages, api_key, model, tools=None):
    payload = {"model": model, "messages": messages}
    if tools:
        payload["tools"] = tools
    resp = requests.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"对话接口报错 {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def diagnose(question, api_key, model="qwen-plus", max_steps=MAX_STEPS,
             embed_model="text-embedding-v3", history=None):
    """
    排查主循环。返回 (answer, trace)。

    和 ai-doc-qa 的 run_agent 结构完全一样：
        while 模型想调工具 且 没超步数:
            发消息 → 模型回「直接答」就结束，回「调工具」就执行并把结果塞回去
    区别只在于：这里的工具是面向故障排查的专业工具。

    【多轮对话】history 参数（这个项目新增）
    ------------------------------------------------
    单轮 = 每次只把当前这一句发给模型，模型不知道上一轮聊了啥。
    多轮 = 把之前的对话也一起发过去，模型才有「记忆」。

    注意 history 里【只保留 user / assistant 的纯文本】，
    不要把上一轮的 tool_calls / tool 消息带进来——那些 tool_call_id 是本轮专有的，
    跨轮复用会直接报错（这是很多人做多轮 Agent 时踩的第一个坑）。

    history 格式：[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    # 把历史对话接在 system 之后、本轮问题之前 —— 这就是「记忆」
    for m in (history or []):
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})
    trace = []

    for step in range(1, max_steps + 1):
        data = _chat(messages, api_key, model, tools=TOOLS)
        msg = data["choices"][0]["message"]
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            answer = msg.get("content") or "（模型没有返回内容）"
            trace.append({"type": "final", "step": step, "content": answer})
            return answer, trace

        assistant_msg = {"role": "assistant", "content": msg.get("content") or ""}
        assistant_msg["tool_calls"] = tool_calls
        messages.append(assistant_msg)

        for call in tool_calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}

            trace.append({"type": "call", "step": step, "name": name, "args": args})
            result = dispatch_tool(name, args, api_key, embed_model)
            trace.append({"type": "result", "step": step, "name": name, "result": result})
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "name": name,
                "content": result,
            })

    messages.append({"role": "user", "content": "检索次数已达上限，请基于已有信息直接给出最终排查建议。"})
    data = _chat(messages, api_key, model)
    answer = data["choices"][0]["message"].get("content") or "（模型没有返回内容）"
    trace.append({"type": "final", "step": max_steps + 1, "content": answer})
    return answer, trace
