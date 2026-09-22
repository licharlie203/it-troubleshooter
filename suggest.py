# -*- coding: utf-8 -*-
"""
录入辅助：让模型自动补全「分类 / 标签」
==============================================

这个文件学的是一个非常实用、企业里天天用的技巧：
**让大模型输出结构化数据（JSON），而不是一段话。**

前面我们做的都是「问一句，模型回一段自然语言」。
但程序要的不是一段话，而是能直接写进数据库的字段：
    category = "打印"
    tags     = "打印机,脱机,spooler"

如果让模型自由发挥，它会回你「根据您的描述，我建议分类为：打印（ Printer ）……」
这种带解释的人话，程序根本没法用。

解决办法三步：
    1. prompt 里明确要求「只输出 JSON，不要其他任何文字」
    2. 调用接口时传 response_format={"type": "json_object"}（模型强制输出合法 JSON）
    3. 拿到结果用 json.loads() 解析 —— 并且做好「解析失败」的兜底

第 3 步最重要（面试能讲）：**永远不要假设模型一定按格式返回**。
网络超时、模型抽风、返回带 markdown 代码块 …… 都可能让 json.loads 失败。
所以必须 try/except，失败就返回空值让用户自己填，而不是让整个程序崩掉。

另外一个工程细节：
    我们把「库里已有的分类」传给模型，让它优先复用已有分类，
    否则它会每次都造一个新词（"打印" / "打印机" / "打印设备" 乱飞），
    分类字段就失去筛选意义了。这是很多人做不好分类功能的真正原因。
"""

import json
import re

import requests

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

SYSTEM_PROMPT = (
    "你是 IT 运维故障知识库的管理员，负责给故障记录打分类和标签。\n"
    "你必须只输出一个 JSON 对象，不要输出任何解释、不要 markdown 代码块、不要多余文字。"
)


def _build_user_prompt(title, symptom, cause, existing_categories):
    cat_hint = "、".join(existing_categories) if existing_categories else "（知识库目前还没有分类）"
    return f"""
知识库已有的分类：{cat_hint}

现在要归档这条故障：
    标题：{title}
    症状：{symptom}
    原因：{cause}

请返回如下结构的 JSON：
{{"category": "分类", "tags": "标签1,标签2,标签3", "reason": "一句话说明归类理由"}}

规则：
1. category 优先从「已有分类」里选一个最贴切的；确实都不合适才新建一个（简短名词，不要解释）。
2. tags 是 3 到 5 个逗号分隔的关键词，要覆盖用户检索时可能用的词：现象（如"脱机"）、设备/软件名（如"打印机"）、关键报错或组件（如"spooler"）。
3. reason 用一句话说明为什么这么归类，方便人工复核。
4. 只返回 JSON。
"""


def _clean_json_text(text):
    """
    模型有时候不老实，仍然会包一层 ```json ... ``` 代码块。
    这里把代码块标记剥掉，只留纯 JSON 文本。
    """
    text = (text or "").strip()
    # 去掉 ```json 开头 和 ``` 结尾
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def suggest_category_tags(title, symptom, cause="", api_key="", model="qwen-plus",
                          existing_categories=None):
    """
    让模型根据标题/症状/原因，建议分类和标签。

    返回字典：
        {"category": "打印", "tags": "打印机,脱机,spooler", "reason": "...", "ok": True}

    ok=False 表示失败了（调用异常或返回格式不对），调用方应让用户手填。
    """
    if not api_key or not (title or "").strip():
        return {"category": "", "tags": "", "reason": "", "ok": False}

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",
             "content": _build_user_prompt(title or "", symptom or "", cause or "",
                                           existing_categories or [])},
        ],
        # 关键：强制模型输出合法 JSON（通义 OpenAI 兼容模式支持）
        "response_format": {"type": "json_object"},
        "temperature": 0.2,  # 分类任务要稳定，不要模型发挥 creativity
    }

    try:
        resp = requests.post(
            f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        if resp.status_code != 200:
            return {"category": "", "tags": "", "reason": "",
                    "ok": False, "error": f"接口报错 {resp.status_code}"}

        content = resp.json()["choices"][0]["message"].get("content") or ""
        data = json.loads(_clean_json_text(content))

        category = str(data.get("category", "")).strip()
        tags = str(data.get("tags", "")).strip()
        reason = str(data.get("reason", "")).strip()

        # 再兜一层：tags 有可能是列表（有些模型会给数组），统一转成逗号字符串
        if isinstance(data.get("tags"), list):
            tags = ",".join(str(t) for t in data["tags"])

        return {"category": category, "tags": tags, "reason": reason, "ok": True}

    except json.JSONDecodeError:
        # 模型没返回合法 JSON —— 不能崩，让用户手填
        return {"category": "", "tags": "", "reason": "",
                "ok": False, "error": "模型返回的内容不是合法 JSON"}
    except Exception as e:
        return {"category": "", "tags": "", "reason": "", "ok": False, "error": str(e)}
