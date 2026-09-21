# -*- coding: utf-8 -*-
"""
向量检索层：把「症状描述」用 embedding 做语义匹配
================================================

为什么需要它？
    关键词检索（LIKE %xxx%）太死板：用户说「打印机打不出」，
    就匹配不到库里的「打印机脱机无法打印」——字面不同但意思一样。

    语义检索（embedding）能解决这个问题：把文字转成向量，意思相近的向量也相近，
    所以「打不出」和「脱机无法打印」能匹配上。这就是 ai-doc-qa 里 Chroma 的用法，
    只不过这里我们检索的是「症状」字段，而不是一整篇文档。

实现方式：
    用通义的 text-embedding-v3 把每条案例的「症状」转成向量，存进 Chroma。
    录入新案例时同步更新向量库；查询时把用户描述也转向量，找最相近的几条。

    为了简单可靠，这里用 Chroma 的「按文档查询」能力（query_texts），
    让 Chroma 自己管 embedding（Chroma 默认用本地小模型，但我们指定用通义更准——
    这里先采用『自己算向量再查』的方式，和 ai-doc-qa 完全一致）。
"""

import os

import chromadb
import requests

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_db")
COLLECTION_NAME = "case_symptoms"


def get_collection():
    client = chromadb.PersistentClient(path=DB_DIR)
    return client.get_or_create_collection(name=COLLECTION_NAME)


def _embed(texts, api_key, model="text-embedding-v3", batch_size=10):
    all_vectors = []
    for i in range(0, len(texts), batch_size):
        part = texts[i:i + batch_size]
        resp = requests.post(
            f"{BASE_URL}/embeddings",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "input": part},
            timeout=60,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"向量接口报错 {resp.status_code}: {resp.text[:200]}")
        data = resp.json()["data"]
        data.sort(key=lambda x: x["index"])
        all_vectors.extend([d["embedding"] for d in data])
    return all_vectors


def build_index(cases, api_key, model="text-embedding-v3"):
    """
    把一批案例的症状写入向量库。cases 是 db.list_cases() 返回的字典列表。
    每个案例的 id 作为 Chroma 的 id，这样查到后能反查回数据库拿完整信息。
    """
    collection = get_collection()
    # 全量重建：删旧数据再写（简单可靠；案例多时再优化成增量）
    ids = collection.get()["ids"]
    if ids:
        collection.delete(ids=ids)

    if not cases:
        return collection

    symptoms = [c["symptom"] for c in cases]
    vectors = _embed(symptoms, api_key, model)
    collection.add(
        ids=[f"case_{c['id']}" for c in cases],
        embeddings=vectors,
        documents=symptoms,
    )
    return collection


def upsert_case(case, api_key, model="text-embedding-v3"):
    """新增或更新单条案例的症状向量（录入新故障时调用）。"""
    collection = get_collection()
    vec = _embed([case["symptom"]], api_key, model)[0]
    collection.upsert(
        ids=[f"case_{case['id']}"],
        embeddings=[vec],
        documents=[case["symptom"]],
    )


def remove_case(case_id):
    """删除某条案例的向量。"""
    collection = get_collection()
    collection.delete(ids=[f"case_{case_id}"])


def search(query, api_key, top_k=3, model="text-embedding-v3"):
    """
    语义检索：返回与 query 最相近的案例 id 列表 + 匹配度列表。
    失败时返回空列表（调用方降级到关键词检索）。
    """
    collection = get_collection()
    if collection.count() == 0:
        return [], []
    q_vec = _embed([query], api_key, model)[0]
    res = collection.query(
        query_embeddings=[q_vec],
        n_results=min(top_k, collection.count()),
        include=["distances"],
    )
    ids = res["ids"][0]
    scores = [round(1 / (1 + d), 4) for d in res["distances"][0]]
    # 把 "case_3" 还原成数字 3
    case_ids = [int(i.replace("case_", "")) for i in ids]
    return case_ids, scores
