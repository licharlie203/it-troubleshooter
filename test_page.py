# -*- coding: utf-8 -*-
"""
演示页 + 接口 自测（不需要浏览器）
====================================
static/index.html 是纯前端页面，改完没法像 Streamlit 那样用 AppTest 点。
这里的思路是两件事：

1. 页面用到的每个接口，都用**真实请求**跑一遍（/health、/cases、/diagnose 单轮+多轮、/suggest）
2. 把页面 JS 里 fetch 的地址抠出来，跟 api.py 里真实注册的路由对一遍
   —— 防止「页面调了个不存在的接口」这种上线才暴露的错

用法（先另开终端起服务）：
    python api.py                # 或 uvicorn api:app --port 8000
    python test_page.py          # 默认 http://127.0.0.1:8000
    python test_page.py http://127.0.0.1:8014
"""

import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
KEY = "itsk-iwo270br8k3f178p"
ROOT = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(ROOT, "static", "index.html")

results = []


def check(name, cond, extra=""):
    results.append(bool(cond))
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (("  → " + str(extra)) if extra else ""))


def get(path, key=True, raw=False):
    url = BASE + path + (("?key=" + KEY) if key else "")
    with urllib.request.urlopen(url, timeout=90) as r:
        body = r.read().decode("utf-8")
        return body if raw else json.loads(body)


def post(path, payload):
    req = urllib.request.Request(
        BASE + path + "?key=" + KEY,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    print("== 1. 演示页能被打开 ==")
    check("static/index.html 存在", os.path.exists(HTML_PATH))
    page = get("/", key=False, raw=True)
    check("GET / 返回页面（不带口令也能开）", "智能排查助手" in page)
    check("页面预填了演示口令", "itsk-iwo270br8k3f178p" in page)
    check("GET /static/index.html 可访问", "智能排查助手" in get("/static/index.html", key=False, raw=True))

    print("== 2. 页面公开、数据接口仍需口令 ==")
    try:
        get("/cases", key=False)
        check("/cases 无口令应被拦截", False)
    except urllib.error.HTTPError as e:
        check("/cases 无口令被拦截", e.code == 401, "HTTP " + str(e.code))

    print("== 3. 页面用到的接口逐个真跑 ==")
    h = get("/health", key=False)
    check("/health 返回案例数", h.get("cases", 0) > 0, f"{h.get('cases')} 条 / 向量 {h.get('vector_count')} 条")
    cases = get("/cases")
    check("/cases 带口令可读", cases["total"] > 0, f"{cases['total']} 条")
    one = get("/cases/%d" % cases["items"][0]["id"])
    check("/cases/{id} 返回单条", one.get("id") == cases["items"][0]["id"], one.get("title"))

    r1 = post("/diagnose", {"question": "打印机显示脱机打不出来", "history": []})
    check("/diagnose 单轮出方案", bool(r1["answer"].strip()))
    calls = [t for t in r1.get("trace", []) if t.get("type") == "call"]
    check("Agent 调用了检索工具", len(calls) > 0, ",".join(t["name"] for t in calls))

    r2 = post("/diagnose", {
        "question": "重启服务还是不行，是网络打印机",
        "history": [{"role": "user", "content": "打印机显示脱机打不出来"},
                    {"role": "assistant", "content": r1["answer"]}],
    })
    check("/diagnose 多轮能接着聊", bool(r2["answer"].strip()), r2["answer"].strip().split("\n")[0][:40])

    sg = post("/suggest", {"title": "共享打印机脱机", "symptom": "显示脱机，队列卡住"})
    check("/suggest 能补全分类标签", sg.get("ok") is True, f"{sg.get('category')} / {sg.get('tags')}")

    print("== 4. 页面 JS 里调的接口，服务端确实有 ==")
    sys.path.insert(0, ROOT)
    import api  # 拿到真实的 FastAPI app
    routes = set()
    for rt in api.app.routes:
        routes.add(getattr(rt, "path", ""))
    used = sorted(set(re.findall(r'withKey\("(/[^"]+)"\)', page)))
    check("页面里找到接口调用", len(used) > 0, " ".join(used))
    for p in used:
        check(f"{p} 已注册", p in routes)

    print("\n结果：%d/%d 通过 %s" % (sum(results), len(results), "✅" if all(results) else "❌"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
