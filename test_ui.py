# -*- coding: utf-8 -*-
"""
界面自动测试（用 Streamlit 官方的 AppTest，模拟真人点击 / 输入）
================================================================

用法：
    python test_ui.py

它模拟两组真实操作：
    A. 故障排查页 —— 多轮对话
       ① 输入一句模糊的故障描述，看 AI 是否追问（第 1 轮）
       ② 再补充关键信息，看它是否带着前文继续排查（第 2 轮）
       ③ 点「清空对话」，看历史是否清干净
    B. 录入故障页 —— 自动补全 + 保存
       ① 填标题/症状 → 点「✨ 自动补全」→ 分类标签是否被填上
       ② 点「保存到知识库」→ 是否入库、表单是否清空

为什么要有这个（面试能讲）：
    Streamlit 的 session_state 规则很严格，很多 bug
    （比如 StreamlitWidgetAlreadyInstantiatedError）只有「真点一下」才会暴露，
    代码看着对、HTTP 200、py_compile 全绿都测不出来。
    这就是团队里说的「界面冒烟测试」。

注意：会真的调用一次大模型，跑完约 1-3 分钟。测试写入的案例会自动清理。
"""

import sys
from streamlit.testing.v1 import AppTest


def _preview(text, n=160):
    return (text or "").replace("\n", " ")[:n]


def test_diagnose_chat():
    print("\n" + "=" * 60)
    print("A. 故障排查页（多轮对话）")
    print("=" * 60)

    at = AppTest.from_file("app.py", default_timeout=300)
    at.run()
    print("① 初次运行异常：", bool(at.exception))
    if at.exception:
        print(at.exception)
        return False

    if not at.chat_input:
        print("❌ 页面上没找到聊天输入框（chat_input）")
        return False

    # ---- 第 1 轮：信息不足，期望 AI 先追问 ----
    at.chat_input[0].set_value("有台打印机打不出来").run()
    print("② 第 1 轮发送后异常：", bool(at.exception))
    if at.exception:
        print(at.exception)
        return False

    h = at.session_state.get("chat_history") or []
    print("   对话条数（应为 2）：", len(h))
    if len(h) >= 2:
        print("   AI 回复：", _preview(h[1]["content"], 180))

    # ---- 第 2 轮：补充信息，期望它带着前文继续 ----
    at.chat_input[0].set_value("面板上显示脱机，打印任务卡在队列里不动").run()
    print("③ 第 2 轮发送后异常：", bool(at.exception))
    if at.exception:
        print(at.exception)
        return False

    h = at.session_state.get("chat_history") or []
    print("   对话条数（应为 4）：", len(h))
    if len(h) >= 4:
        print("   AI 回复：", _preview(h[3]["content"], 260))
        calls = [t for t in (h[3].get("trace") or []) if t["type"] == "call"]
        print("   第 2 轮调用工具次数：", len(calls), [c["name"] for c in calls])
    print("   页面上的气泡数量：", len(at.chat_message))

    # ---- 清空对话 ----
    for b in at.button:
        if "清空对话" in b.label:
            b.click().run()
            break
    print("④ 清空对话后异常：", bool(at.exception))
    print("   清空后对话条数（应为 0）：", len(at.session_state.get("chat_history") or []))
    return not at.exception


def test_add_case():
    print("\n" + "=" * 60)
    print("B. 录入故障页（自动补全 + 保存）")
    print("=" * 60)

    at = AppTest.from_file("app.py", default_timeout=300)
    at.run()

    at.radio[0].set_value("➕ 录入故障")
    at.run()
    print("① 切到录入页异常：", bool(at.exception))
    if at.exception:
        print(at.exception)
        return False

    at.session_state["f_title"] = "【自测】笔记本摄像头打不开"
    at.session_state["f_symptom"] = "开视频会议时摄像头黑屏，系统提示找不到摄像头设备"
    at.run()

    auto_btn = None
    for b in at.button:
        if "自动补全" in b.label:
            auto_btn = b
    if auto_btn is None:
        print("❌ 没找到自动补全按钮")
        return False

    auto_btn.click().run()
    print("② 点自动补全后异常：", bool(at.exception))
    if at.exception:
        print(at.exception)
        return False
    print("   补全后的分类：", repr(at.session_state.get("f_category")))
    print("   补全后的标签：", repr(at.session_state.get("f_tags")))
    if not at.session_state.get("f_category"):
        print("⚠️ 分类没被填上，检查一下")
        return False

    at.session_state["f_steps"] = "1. 设备管理器里确认摄像头是否被禁用\n2. 重装摄像头驱动"
    at.run()
    for b in at.button:
        if "保存" in b.label:
            b.click().run()
            break
    print("③ 点保存后异常：", bool(at.exception))
    if at.exception:
        print(at.exception)
        return False
    print("   保存提示：", at.session_state.get("flash_msg"))
    print("   表单是否已清空：", repr(at.session_state.get("f_title")))

    # ---- 清理测试数据 ----
    try:
        sys.path.insert(0, ".")
        import db
        import vector_search
        for c in db.list_cases():
            if c["title"].startswith("【自测】"):
                db.delete_case(c["id"])
                try:
                    vector_search.remove_case(c["id"])
                except Exception:
                    pass
                print("   已清理测试案例 #", c["id"])
    except Exception as e:
        print("   清理时出错（不影响结论）：", e)
    return not at.exception


if __name__ == "__main__":
    ok1 = test_diagnose_chat()
    ok2 = test_add_case()
    print("\n" + "=" * 60)
    if ok1 and ok2:
        print("✅ 两组界面测试全部通过，无 Streamlit 异常")
    else:
        print("⚠️ 有测试未通过，看上面的输出定位问题")
