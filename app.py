# -*- coding: utf-8 -*-
"""
IT 运维故障知识库 + 排查助手
================================

你的第五个作品。定位比 ai-doc-qa 更高一级：
    不是「查一段文档」，而是「录入历史故障 → 遇到新故障时给出排查方案」。

三个页面（左侧导航切换）：
    1. 🔍 故障排查 —— 核心：描述故障，Agent 自动检索相似案例并给出分步排查清单
    2. ➕ 录入故障 —— 把解决过的新故障存进知识库（可持续积累）
    3. 📚 故障库   —— 浏览、搜索、筛选、编辑、删除已有案例

运行方式：
    streamlit run app.py
"""

import os
import streamlit as st

import db
import agent
import vector_search
import suggest


# ==================== 读取 API Key（和 ai-doc-qa 一样的逻辑） ====================
def get_api_key():
    try:
        if "API_KEY" in st.secrets:
            return st.secrets["API_KEY"]
    except Exception:
        pass
    env = os.environ.get("DASHSCOPE_API_KEY")
    if env:
        return env
    try:
        import config
        return config.API_KEY
    except Exception:
        return ""


def get_model():
    try:
        import config
        return config.MODEL
    except Exception:
        return "qwen-plus"


def render_trace(trace, idx=0):
    """把 Agent 的工具调用轨迹渲染成一个折叠面板（多轮时每轮一个，用 idx 区分）。"""
    if not trace:
        return
    with st.expander(f"🧠 Agent 排查过程（第 {idx} 轮）", expanded=False):
        for t in trace:
            if t["type"] == "call":
                args_str = ", ".join(f"{k}={v}" for k, v in t["args"].items()) or "无参数"
                st.markdown(f"**第 {t['step']} 步 · 调用工具：** `{t['name']}`（{args_str}）")
            elif t["type"] == "result":
                preview = str(t["result"])[:300] + ("…" if len(str(t["result"])) > 300 else "")
                st.caption("↳ 工具返回：" + preview)
            else:
                st.markdown(f"**第 {t['step']} 步 · 生成最终排查建议**")


# ==================== 页面设置 ====================
st.set_page_config(page_title="IT 运维故障知识库", page_icon="🛠️", layout="wide")
st.title("🛠️ IT 运维故障知识库 + 排查助手")
st.caption("把解决过的故障存下来，遇到新故障让 AI 帮你出排查方案")

db.init_db()

api_key = get_api_key()
if not api_key or not api_key.startswith("sk-"):
    st.error("未检测到 API Key，请先新建 config.py 配置（参考 ai-doc-qa 的 config.py）。")
    st.stop()

# 启动时确保向量索引存在（首次运行会构建，之后跳过）
embed_model = "text-embedding-v3"
if "index_built" not in st.session_state:
    try:
        cases = db.list_cases()
        if cases:
            # 只在向量库为空时全量构建，避免每次刷新都重算
            if vector_search.get_collection().count() == 0:
                with st.spinner("首次运行：正在构建语义检索索引..."):
                    vector_search.build_index(cases, api_key, model=embed_model)
    except Exception:
        pass  # 向量索引构建失败不阻塞使用，检索时会降级到关键词
    st.session_state["index_built"] = True

# 三个页面
page = st.sidebar.radio("功能", ["🔍 故障排查", "➕ 录入故障", "📚 故障库"])


# ==================== 页面 1：故障排查（多轮对话） ====================
if page == "🔍 故障排查":
    # chat_history 存整个对话：[{"role":"user"/"assistant", "content":..., "trace":[...]}]
    # ⚠️ 它只是一个普通 list，不是 widget 的 key，所以可以随便改，不受 Streamlit 那条铁律限制
    if "chat_history" not in st.session_state:
        st.session_state["chat_history"] = []

    head_l, head_r = st.columns([4, 1.2])
    with head_l:
        st.subheader("🔍 描述故障，让 AI 帮你排查")
        st.caption("信息不够时它会**追问**；你补充后它接着往下排查。对话会留在页面上，随时能继续补充。")
    with head_r:
        if st.session_state["chat_history"]:
            if st.button("🗑️ 清空对话", use_container_width=True):
                st.session_state["chat_history"] = []
                st.rerun()

    # ---- 渲染历史对话（每次 rerun 重放一遍，所以刷新页面也不会丢） ----
    for i, m in enumerate(st.session_state["chat_history"]):
        with st.chat_message(m["role"]):
            st.markdown(m["content"])
            render_trace(m.get("trace"), idx=i // 2 + 1)

    # ---- 输入框：st.chat_input 固定在页面底部，回车即发送 ----
    prompt = st.chat_input("描述故障现象，或回答 AI 的追问…")
    if prompt:
        # ❶ 先取出历史（只保留 user/assistant 纯文本，不能带 tool_calls，跨轮会报错）
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in st.session_state["chat_history"]
        ]

        # ❷ 显示用户这一句
        st.session_state["chat_history"].append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # ❸ 调 Agent（把历史一起传进去，模型这才有「记忆」）
        with st.chat_message("assistant"):
            with st.spinner("正在检索相似案例并生成排查方案..."):
                try:
                    answer, trace = agent.diagnose(
                        prompt, api_key, model=get_model(),
                        embed_model=embed_model, history=history,
                    )
                except Exception as e:
                    answer = f"❌ 出错了：{e}"
                    trace = []
            st.markdown(answer)
            render_trace(trace, idx=len(st.session_state["chat_history"]) // 2 + 1)

        st.session_state["chat_history"].append(
            {"role": "assistant", "content": answer, "trace": trace}
        )

    st.divider()
    st.caption("提示：知识库案例越丰富，排查建议越准。用左侧「录入故障」把解决过的新故障存进来。")


# ==================== 页面 2：录入故障 ====================
elif page == "➕ 录入故障":
    st.subheader("➕ 录入一条已解决的故障")
    st.caption("填完标题和症状，点「✨ 自动补全」让 AI 帮你填分类和标签，省得手打")

    # 表单字段统一用 session_state 管理（这样代码里能改它们的值）
    FIELDS = ["f_title", "f_category", "f_symptom", "f_cause", "f_steps", "f_tags"]
    for k in FIELDS:
        if k not in st.session_state:
            st.session_state[k] = ""

    # ============================================================
    # 阶段一：处理上一次点击留下的任务
    # ⚠️ 必须放在创建输入框【之前】！
    #    Streamlit 规定：widget 一旦创建，就不能再改同名 session_state，
    #    否则报 StreamlitWidgetAlreadyInstantiatedError。
    #    所以「改值」的动作要赶在这次运行的渲染阶段之前做完。
    #    这就是为什么按钮里只放一个「标记」，真正的活儿在这里干。
    # ============================================================
    if st.session_state.get("_do_autofill"):
        st.session_state["_do_autofill"] = False   # 先清标记，防止重复执行
        title_v = st.session_state.get("f_title", "")
        symptom_v = st.session_state.get("f_symptom", "")
        cause_v = st.session_state.get("f_cause", "")
        if not title_v.strip() and not symptom_v.strip():
            st.session_state["autofill_msg"] = "⚠️ 先填「标题」或「症状」，AI 才知道归到哪类。"
        else:
            with st.spinner("AI 正在判断分类和生成标签..."):
                res = suggest.suggest_category_tags(
                    title_v, symptom_v, cause_v, api_key, model=get_model(),
                    existing_categories=db.all_categories(),
                )
            if res.get("ok"):
                # 此刻输入框还没创建，改 session_state 是合法的
                st.session_state["f_category"] = res["category"]
                st.session_state["f_tags"] = res["tags"]
                st.session_state["autofill_msg"] = (
                    f"✅ 已补全：分类「{res['category']}」｜标签「{res['tags']}」\n\n"
                    f"归类理由：{res['reason']}"
                )
            else:
                st.session_state["autofill_msg"] = (
                    f"⚠️ 自动补全失败，请手动填写。（{res.get('error', '未知原因')}）"
                )

    if st.session_state.get("_do_clear"):
        st.session_state["_do_clear"] = False
        for k in FIELDS:
            st.session_state[k] = ""

    # ============================================================
    # 阶段二：渲染表单（这里创建的 widget，之后就不能再改同名 key 了）
    # ============================================================
    st.text_input("故障标题 *", placeholder="如：打印机脱机无法打印", key="f_title")
    st.text_area("症状（用户看到什么现象）*",
                 placeholder="如：打印机显示脱机，任务卡住", key="f_symptom")
    st.text_area("根本原因", placeholder="如：Print Spooler 服务停止", key="f_cause")
    st.text_area("解决步骤（每行一条）*",
                 placeholder="1. 重启 Print Spooler 服务\n2. 取消卡住的打印任务\n3. 重装驱动",
                 key="f_steps")

    st.markdown("##### 分类与标签（可让 AI 补全）")
    st.text_input("分类", placeholder="如：打印 / 网络 / 系统 / 硬件 / 账号权限 / 办公软件",
                  key="f_category")
    st.text_input("标签（逗号分隔，方便检索）", placeholder="如：打印机,脱机,spooler", key="f_tags")

    col_auto, col_save, _ = st.columns([1.2, 1.2, 2.6])

    with col_auto:
        if st.button("✨ 自动补全", use_container_width=True):
            # 按钮里只打标记，然后 rerun；真正的活儿在下一轮渲染前干
            st.session_state["_do_autofill"] = True
            st.rerun()
    with col_save:
        if st.button("保存到知识库", type="primary", use_container_width=True):
            title_v = st.session_state.get("f_title", "").strip()
            symptom_v = st.session_state.get("f_symptom", "").strip()
            steps_v = st.session_state.get("f_steps", "").strip()
            if not title_v or not symptom_v or not steps_v:
                st.session_state["flash_msg"] = "⚠️ 标题、症状、解决步骤是必填项。"
            else:
                cid = db.add_case(
                    title_v,
                    st.session_state.get("f_category", "").strip(),
                    symptom_v,
                    st.session_state.get("f_cause", "").strip(),
                    steps_v,
                    st.session_state.get("f_tags", "").strip(),
                )
                # 同步更新向量索引，让新案例立即参与语义检索
                try:
                    case = db.get_case(cid)
                    vector_search.upsert_case(case, api_key, model=embed_model)
                except Exception:
                    pass
                st.session_state["flash_msg"] = f"✅ 已录入「{title_v}」。"
                st.session_state["_do_clear"] = True   # 标记清空，同样放到下一轮开头执行
            st.rerun()

    # 提示信息（跨 rerun 显示一次就清掉）
    if st.session_state.get("autofill_msg"):
        st.info(st.session_state["autofill_msg"])
        st.session_state["autofill_msg"] = ""
    if st.session_state.get("flash_msg"):
        msg = st.session_state["flash_msg"]
        st.session_state["flash_msg"] = ""
        if msg.startswith("⚠️"):
            st.warning(msg)
        else:
            st.success(msg)

    st.divider()
    st.caption("小技巧：把『症状』写得具体一点（写了什么提示、什么现象），AI 排查时匹配会更准。")


# ==================== 页面 3：故障库 ====================
else:
    st.subheader("📚 故障库")

    col1, col2 = st.columns([1, 2])
    with col1:
        cats = ["全部"] + db.all_categories()
        sel_cat = st.selectbox("按分类筛选", cats)
    with col2:
        keyword = st.text_input("搜索（标题/标签/症状）")

    cases = db.list_cases(category=sel_cat, keyword=keyword.strip() or None)
    st.caption(f"共 {len(cases)} 条案例")

    if not cases:
        st.info("暂无案例。去「录入故障」页添加，或先运行 seed_data.py 导入示范数据。")
    else:
        for c in cases:
            with st.expander(f"#{c['id']} · {c['title']}（{c['category'] or '未分类'}）"):
                st.markdown(f"**症状**：{c['symptom']}")
                st.markdown(f"**原因**：{c['cause'] or '—'}")
                st.markdown("**解决步骤**：")
                for s in db.steps_to_list(c["steps"]):
                    st.markdown(f"- {s}")
                st.caption(f"标签：{c['tags'] or '—'} ｜ 录入：{c['created_at']}")

                # 编辑 / 删除
                edit_col, del_col = st.columns(2)
                with edit_col:
                    if st.button("✏️ 编辑", key=f"edit_{c['id']}"):
                        st.session_state["editing_id"] = c["id"]
                with del_col:
                    if st.button("🗑️ 删除", key=f"del_{c['id']}"):
                        db.delete_case(c["id"])
                        try:
                            vector_search.remove_case(c["id"])
                        except Exception:
                            pass
                        st.rerun()

    # 编辑弹窗式处理（用简单表单）
    if "editing_id" in st.session_state:
        cid = st.session_state["editing_id"]
        c = db.get_case(cid)
        if c:
            with st.form(f"edit_form_{cid}"):
                st.markdown(f"### 编辑案例 #{cid}")
                n_title = st.text_input("标题", value=c["title"])
                n_cat = st.text_input("分类", value=c["category"])
                n_symptom = st.text_area("症状", value=c["symptom"])
                n_cause = st.text_area("原因", value=c["cause"])
                n_steps = st.text_area("解决步骤（每行一条）", value=c["steps"])
                n_tags = st.text_input("标签（逗号分隔）", value=c["tags"])
                c1, c2 = st.columns(2)
                with c1:
                    save = st.form_submit_button("保存修改", type="primary")
                with c2:
                    cancel = st.form_submit_button("取消")
            if save:
                db.update_case(cid, title=n_title, category=n_cat, symptom=n_symptom,
                               cause=n_cause, steps=n_steps, tags=n_tags)
                try:
                    vector_search.upsert_case(db.get_case(cid), api_key, model=embed_model)
                except Exception:
                    pass
                del st.session_state["editing_id"]
                st.rerun()
            if cancel:
                del st.session_state["editing_id"]
                st.rerun()


st.divider()
st.caption(
    "原理：故障案例结构化存入 SQLite → 排查时 Agent 检索相似案例（Function Calling）→ 综合生成分步排查清单。"
    "相比 ai-doc-qa 的『查文档』，这里多了『结构化数据 + 可持续录入 + 专业排查工具』三层。"
)
