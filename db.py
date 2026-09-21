# -*- coding: utf-8 -*-
"""
数据库层：故障案例的「仓库」（SQLite）
=====================================

这个文件只干一件事：负责把故障案例存起来、取出来、改、删。

为什么用 SQLite 而不是 JSON 文件？
    1. 零安装、零部署——SQLite 就是一个文件，Python 自带 sqlite3，开箱即用。
    2. 支持增删改查、按分类/标签筛选——案例多了也不怕，JSON 会越来越难维护。
    3. 这就是真实项目里「数据层」的样子：界面/接口/数据分开，各管各的。

表结构（cases 表，一条记录 = 一个故障案例）：
    id        自增主键
    title     故障标题，如「打印机脱机无法打印」
    category  分类，如「打印/网络/系统/账号」
    symptom   症状描述（用户看到什么现象）
    cause     根本原因（排查后定位到的）
    steps     解决步骤，用换行分隔（存的时候是字符串，取出来 split 成列表）
    tags      标签，用逗号分隔，方便检索和筛选
    created_at 录入时间

一个设计要点（面试能讲）：我们把「症状」「原因」「解决步骤」拆成**独立字段**，
而不是像 ai-doc-qa 那样塞一整段文本。好处是：
    - 检索时可以只针对「症状」字段做向量匹配，更准
    - 生成排查清单时能直接拿到结构化的「步骤」，不用让模型再从文本里抠
"""

import sqlite3
import os
from datetime import datetime

# 数据库文件就放在项目目录下，叫 it_troubleshooter.db
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "it_troubleshooter.db")


def get_conn():
    """拿到一个数据库连接。row_factory 让查询结果能用 row['字段名'] 取值。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """建表（如果还没有的话）。程序启动时调一次即可。"""
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                category TEXT DEFAULT '',
                symptom TEXT DEFAULT '',
                cause TEXT DEFAULT '',
                steps TEXT DEFAULT '',
                tags TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            )
            """
        )


def add_case(title, category="", symptom="", cause="", steps="", tags=""):
    """
    新增一条故障案例。
    参数 steps 可以是列表（['1.xxx', '2.xxx']）也可以是字符串，统一存成换行分隔的字符串。
    """
    if isinstance(steps, list):
        steps = "\n".join(steps)
    if isinstance(tags, list):
        tags = ",".join(tags)
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO cases (title, category, symptom, cause, steps, tags, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (title, category, symptom, cause, steps, tags, created_at),
        )
        return cur.lastrowid


def list_cases(category=None, keyword=None):
    """列出所有案例，可按分类或关键词（标题/标签）筛选。"""
    sql = "SELECT * FROM cases WHERE 1=1"
    params = []
    if category and category != "全部":
        sql += " AND category = ?"
        params.append(category)
    if keyword:
        sql += " AND (title LIKE ? OR tags LIKE ? OR symptom LIKE ?)"
        like = f"%{keyword}%"
        params.extend([like, like, like])
    sql += " ORDER BY id DESC"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_case(case_id):
    """按 id 取单条案例。"""
    with get_conn() as conn:
        r = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        return dict(r) if r else None


def update_case(case_id, **fields):
    """更新一条案例的某些字段。fields 里传什么就改什么。"""
    if not fields:
        return
    allowed = {"title", "category", "symptom", "cause", "steps", "tags"}
    sets = []
    params = []
    for k, v in fields.items():
        if k in allowed:
            if isinstance(v, list):
                v = "\n".join(v) if k == "steps" else ",".join(v)
            sets.append(f"{k} = ?")
            params.append(v)
    if not sets:
        return
    params.append(case_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE cases SET {', '.join(sets)} WHERE id = ?", params)


def delete_case(case_id):
    """删除一条案例。"""
    with get_conn() as conn:
        conn.execute("DELETE FROM cases WHERE id = ?", (case_id,))


def count_cases():
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM cases").fetchone()[0]


def all_categories():
    """返回所有出现过的分类，供界面下拉框用。"""
    with get_conn() as conn:
        rows = conn.execute("SELECT DISTINCT category FROM cases WHERE category != ''").fetchall()
    return [r[0] for r in rows]


def steps_to_list(steps_str):
    """把存库的换行字符串还原成步骤列表（过滤空行）。"""
    return [s.strip() for s in (steps_str or "").split("\n") if s.strip()]
