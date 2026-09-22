# -*- coding: utf-8 -*-
"""
接口自测脚本：不开浏览器，命令行就能验证 API 是否正常。
================================================================
用法（先把服务跑起来）：
    终端 A:  uvicorn api:app --reload --port 8000
    终端 B:  python test_api.py

它依次验证：
    1. GET  /health     服务与数据是否就绪
    2. POST /cases      录入一条案例（并同步向量索引）
    3. GET  /cases      列表能查到刚录的
    4. POST /diagnose   单轮排查：能给出方案
    5. POST /diagnose   多轮排查：先模糊描述 → 再补充信息，验证「有记忆」
    6. DELETE /cases    清理测试数据
"""

import sys
import requests

BASE = "http://127.0.0.1:8000"
TEST_TITLE = "【自测】显示器花屏闪屏"


def line(t):
    print("\n" + "=" * 60)
    print(t)
    print("=" * 60)


def main():
    # ---------- 1. 探活 ----------
    line("1. GET /health")
    r = requests.get(f"{BASE}/health", timeout=15)
    print("状态码:", r.status_code)
    print(r.json())
    if r.status_code != 200:
        print("服务没起来或数据层异常，先解决这个再往下测。")
        return

    # ---------- 2. 录入 ----------
    line("2. POST /cases（录入 + 同步向量索引）")
    payload = {
        "title": TEST_TITLE,
        "category": "硬件",
        "symptom": "显示器画面出现彩色条纹、闪烁，鼠标移动时更明显",
        "cause": "视频线接触不良 或 显卡驱动异常",
        "steps": "1. 重新插拔 HDMI/DP 线，两端都要插紧\n2. 换一根视频线或换接口试\n3. 更新/回滚显卡驱动\n4. 换另一台显示器，判断是显示器还是主机问题",
        "tags": "显示器,花屏,闪屏,视频线,显卡驱动",
    }
    r = requests.post(f"{BASE}/cases", json=payload, timeout=30)
    print("状态码:", r.status_code, "| 返回:", r.json())
    new_id = r.json().get("id")
    if r.status_code not in (200, 201):
        print("录入失败，后面的排查测试会不准。")
        return

    # ---------- 3. 列表 ----------
    line("3. GET /cases?keyword=花屏")
    r = requests.get(f"{BASE}/cases", params={"keyword": "花屏"}, timeout=15)
    data = r.json()
    print("匹配条数:", data["total"])
    for c in data["items"]:
        print(f"  #{c['id']} [{c['category']}] {c['title']}")

    # ---------- 4. 单轮排查 ----------
    line("4. POST /diagnose 单轮：描述很具体（应直接给方案）")
    q1 = "我的显示器画面有彩色条纹还一直闪，鼠标一动就更明显，这是什么问题"
    r = requests.post(f"{BASE}/diagnose", json={"question": q1}, timeout=300)
    print("状态码:", r.status_code)
    if r.status_code == 200:
        d = r.json()
        print("--- 答复（前 700 字）---")
        print(d["answer"][:700])
        print("--- 调用的工具 ---")
        for t in d["trace"]:
            if t["type"] == "call":
                print("  调用:", t["name"], t["args"])
    else:
        print(r.text[:400])

    # ---------- 5. 多轮排查 ----------
    line("5. POST /diagnose 多轮：先模糊 → 再补充（验证「有记忆」）")
    q2 = "有台电脑显示不正常"
    r = requests.post(f"{BASE}/diagnose", json={"question": q2}, timeout=300)
    print("【第 1 轮】用户：", q2)
    if r.status_code == 200:
        d1 = r.json()
        print("AI 追问/答复：", d1["answer"][:400])
        history = [
            {"role": "user", "content": q2},
            {"role": "assistant", "content": d1["answer"]},
        ]
    else:
        print(r.text[:400])
        history = []

    if history:
        q3 = "是显示器上有彩色的条纹，还一闪一闪的，换了根线也没好"
        r = requests.post(f"{BASE}/diagnose",
                          json={"question": q3, "history": history}, timeout=300)
        print("\n【第 2 轮】用户：", q3)
        if r.status_code == 200:
            d2 = r.json()
            print("AI 答复（前 700 字）：")
            print(d2["answer"][:700])
            print("--- 调用的工具 ---")
            for t in d2["trace"]:
                if t["type"] == "call":
                    print("  调用:", t["name"], t["args"])
        else:
            print(r.text[:400])

    # ---------- 6. 清理 ----------
    line("6. DELETE /cases/{id} 清理测试数据")
    if new_id:
        r = requests.delete(f"{BASE}/cases/{new_id}", timeout=15)
        print("状态码:", r.status_code, "| 返回:", r.json())
    print("\n全部测完。")


if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.ConnectionError:
        print("连不上服务。先在另一个终端跑：uvicorn api:app --reload --port 8000")
        sys.exit(1)
