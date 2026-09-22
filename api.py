# -*- coding: utf-8 -*-
"""
FastAPI 接口层：把「故障知识库 + 排查 Agent」的能力暴露成 HTTP 接口
====================================================================

为什么要做这一层？（面试能讲）
------------------------------
Streamlit 页面（app.py）是「给人点的」，只能你本机自己用。
真实企业里，能力要给**别的程序**用：OA 系统、企业微信机器人、内部工单平台……
它们不会去点你的网页，它们只会**发一个 HTTP 请求**。

所以我们把业务逻辑包成接口：
    POST /diagnose  ← 工单系统把「用户报的故障」发过来，拿回排查方案
    POST /cases     ← 运维解决完问题后，把案例写回知识库（知识库自己会越长越大）

这就是「前端（Streamlit）」和「后端（FastAPI）」解耦：
    两个入口，同一套 db.py / agent.py / vector_search.py 逻辑，一份数据。

和 ai-doc-qa 那版的区别（升级点）：
    ai-doc-qa 的状态（已上传的 chunks）存在内存里，重启就没了，没法真正对外服务；
    这个项目的状态在 SQLite 里，天然支持多进程、重启不丢——这才是能上线的形态。

启动方式：
    uvicorn api:app --reload --port 8000
接口文档（自动生成，能直接点着测）：
    http://127.0.0.1:8000/docs
"""

import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import db
import agent
import suggest
import vector_search

EMBED_MODEL = "text-embedding-v3"


def get_api_key():
    """按优先级取 Key：环境变量 > config.py。"""
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


app = FastAPI(
    title="IT 运维故障知识库 API",
    description="结构化故障库 + 语义检索 + 排查 Agent，供其它系统调用",
    version="1.0.0",
)

# 允许跨域，方便以后做前端页面 / 企业微信机器人调用
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    db.init_db()


# ==================== 请求 / 响应模型（pydantic 会自动校验参数） ====================
class CaseIn(BaseModel):
    title: str = Field(..., description="故障标题", examples=["打印机脱机无法打印"])
    category: str = Field("", description="分类，如 打印/网络/系统/硬件")
    symptom: str = Field("", description="症状：用户看到什么现象")
    cause: str = Field("", description="根本原因")
    steps: str = Field("", description="解决步骤，每行一条")
    tags: str = Field("", description="标签，逗号分隔")
    sync_index: bool = Field(True, description="是否同步更新向量索引（外部批量导入时可关掉）")


class CaseUpdate(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    symptom: Optional[str] = None
    cause: Optional[str] = None
    steps: Optional[str] = None
    tags: Optional[str] = None


class ChatTurn(BaseModel):
    role: str = Field(..., description="user 或 assistant")
    content: str = Field(..., description="这一轮说的内容")


class DiagnoseIn(BaseModel):
    question: str = Field(..., description="故障现象描述", examples=["打印机打不出来，显示脱机"])
    history: List[ChatTurn] = Field(default=[], description="历史对话（多轮追问时传）")


class DiagnoseOut(BaseModel):
    answer: str
    trace: List[dict] = Field(default=[], description="Agent 的工具调用轨迹，方便调试与展示")


class SuggestIn(BaseModel):
    title: str = ""
    symptom: str = ""
    cause: str = ""


class SuggestOut(BaseModel):
    ok: bool
    category: str = ""
    tags: str = ""
    reason: str = ""
    error: str = ""


# ==================== 探活 ====================
@app.get("/health", summary="探活：确认服务和数据可用")
def health():
    try:
        return {
            "status": "ok",
            "cases": db.count_cases(),
            "categories": db.all_categories(),
            "vector_count": vector_search.get_collection().count(),
            "model": get_model(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"数据层异常：{e}")


# ==================== 故障案例：增删改查 ====================
@app.get("/cases", summary="列出案例（可按分类/关键词筛选）")
def list_cases(category: Optional[str] = None, keyword: Optional[str] = None):
    cases = db.list_cases(category=category, keyword=keyword)
    for c in cases:
        c["steps_list"] = db.steps_to_list(c["steps"])
    return {"total": len(cases), "items": cases}


@app.get("/cases/{case_id}", summary="取单条案例详情")
def get_case(case_id: int):
    c = db.get_case(case_id)
    if not c:
        raise HTTPException(status_code=404, detail=f"没有 id={case_id} 的案例")
    c["steps_list"] = db.steps_to_list(c["steps"])
    return c


@app.post("/cases", summary="新增案例（录入即入库，并同步语义索引）", status_code=201)
def create_case(body: CaseIn):
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="title 不能为空")
    cid = db.add_case(body.title, body.category, body.symptom,
                      body.cause, body.steps, body.tags)
    synced = False
    if body.sync_index:
        try:
            vector_search.upsert_case(db.get_case(cid), get_api_key(), model=EMBED_MODEL)
            synced = True
        except Exception:
            synced = False   # 索引失败不影响入库，检索时会降级到关键词
    return {"id": cid, "index_synced": synced, "message": f"已录入「{body.title}」"}


@app.put("/cases/{case_id}", summary="更新案例")
def update_case(case_id: int, body: CaseUpdate):
    if not db.get_case(case_id):
        raise HTTPException(status_code=404, detail=f"没有 id={case_id} 的案例")
    db.update_case(case_id, **body.model_dump(exclude_none=True))
    try:
        vector_search.upsert_case(db.get_case(case_id), get_api_key(), model=EMBED_MODEL)
    except Exception:
        pass
    return {"id": case_id, "message": "已更新"}


@app.delete("/cases/{case_id}", summary="删除案例")
def delete_case(case_id: int):
    if not db.get_case(case_id):
        raise HTTPException(status_code=404, detail=f"没有 id={case_id} 的案例")
    db.delete_case(case_id)
    try:
        vector_search.remove_case(case_id)
    except Exception:
        pass
    return {"id": case_id, "message": "已删除"}


# ==================== 核心：排查 ====================
@app.post("/diagnose", response_model=DiagnoseOut, summary="描述故障，返回分步排查方案")
def diagnose(body: DiagnoseIn):
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="question 不能为空")
    key = get_api_key()
    if not key:
        raise HTTPException(status_code=500, detail="服务端未配置 API Key（config.py 或 DASHSCOPE_API_KEY）")
    if db.count_cases() == 0:
        raise HTTPException(status_code=409, detail="知识库还没有案例，请先 POST /cases 录入")

    history = [{"role": t.role, "content": t.content} for t in body.history]
    try:
        answer, trace = agent.diagnose(
            body.question, key, model=get_model(),
            embed_model=EMBED_MODEL, history=history,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Agent 调用失败：{e}")
    return DiagnoseOut(answer=answer, trace=trace)


# ==================== 辅助：录入时自动补全分类/标签 ====================
@app.post("/suggest", response_model=SuggestOut, summary="让模型根据标题/症状推荐分类和标签")
def suggest_fields(body: SuggestIn):
    key = get_api_key()
    if not key:
        raise HTTPException(status_code=500, detail="服务端未配置 API Key")
    res = suggest.suggest_category_tags(
        body.title, body.symptom, body.cause, key,
        model=get_model(), existing_categories=db.all_categories(),
    )
    return SuggestOut(**res)
