# 🛠️ IT 运维故障知识库 + 排查助手

把你解决过的故障存下来，遇到新故障时让 AI 帮你出分步排查方案。

> 第五个作品。相比 ai-doc-qa（查文档），这里多了三层关键能力：
> **结构化数据 + 可持续录入 + 专业排查 Agent**。

> 🌐 **已上线**：`https://it-troubleshooter-79990.app.workbuddy.host/`
> （FastAPI 接口版，带访问口令保护；公开探活 `/health`，完整接口文档 `/docs`）

## ✨ 它能做什么

- **录入故障**：把解决过的故障（症状/原因/解决步骤/标签）存进知识库，持续积累
- **AI 自动补全**：录故障时点「✨ 自动补全」，模型按 JSON 结构化输出，自动填好分类和标签
- **多轮对话排查**：描述不充分时 AI 会**先追问**（型号、报错原文、单机还是全员），补充后接着往下排查
- **排查问答**：描述一个故障现象，AI 自动检索相似历史案例，给出「分步排查清单」
- **故障库管理**：浏览、搜索、按分类筛选、编辑、删除案例
- **语义检索**：用 embedding 把「症状」向量化，「打印机打不出」也能匹配到「打印机脱机无法打印」
- **HTTP 接口**：`api.py` 把全部能力包成 REST 接口（含自动生成的接口文档），可供 OA / 企业微信机器人等外部系统调用

## 🚀 快速开始

```bash
pip install -r requirements.txt
python seed_data.py          # 首次运行：写入 19 条示范故障案例
streamlit run app.py         # ① 网页界面（自己日常用）
```

想让别的程序调用，就再起一个接口服务：

```bash
uvicorn api:app --reload --port 8000
# 接口文档（能直接点着测）：http://127.0.0.1:8000/docs
```

用 Docker 跑接口（Key 用环境变量传，不写进镜像）：

```bash
docker build -t it-troubleshooter .
docker run -p 8000:8000 -e DASHSCOPE_API_KEY=sk-xxx it-troubleshooter
```

**配置 API Key**：复制 `config.example.py` 为 `config.py`，填入你的通义千问 Key。

> 🔒 `config.py`、`it_troubleshooter.db`、`chroma_db/` 已被 `.gitignore` 排除，不会上传 GitHub。
>
> ✅ 自动化测试：`python test_ui.py`（界面交互，AppTest）、`python test_api.py`（HTTP 接口，需先起服务）。

## 🧠 它是怎么工作的

```
你描述故障（"打印机打不出来，显示脱机"）
      ↓
Agent 判断这是打印类故障 → 调用 search_similar_cases
      ↓
语义检索：把"症状"向量化，找出最相似的历史案例
      ↓
拿到案例的症状/原因/解决步骤
      ↓
模型综合 → 分步排查清单（每步标注依据来自哪条案例）
```

### 技术栈（承接 ai-doc-qa 的升级点）

| 层         | 技术                          | 作用                          |
| ---------- | --------------------------- | --------------------------- |
| 存储        | SQLite（`db.py`）             | 结构化存故障案例，支持增删改查、筛选       |
| 语义检索      | embedding + Chroma（`vector_search.py`） | 「症状」向量化，语义匹配（不靠字面）       |
| 排查 Agent  | Function Calling（`agent.py`） | 模型自主调用工具：查案例/看分类/取详情      |
| 录入补全      | 结构化 JSON 输出（`suggest.py`）  | 模型自动填分类/标签，省去手工填写           |
| 界面        | Streamlit（`app.py`）         | 多轮对话排查 / 录入 / 故障库 三个页面       |
| 接口        | FastAPI（`api.py`）           | REST 接口 + 自动文档，供外部系统调用        |
| 部署        | Docker（`Dockerfile`）        | 一条命令打包运行，Key 用环境变量注入         |

### 多轮对话排查是怎么做的（`app.py` + `agent.py`）

单轮排查的问题：每次都只把当前这句话发给模型，它不知道上一轮聊了啥，用户补充一句它就「失忆」。

多轮要做两件事，**缺一不可**：

**① 界面：把对话历史存住（`app.py`）**

```python
st.session_state["chat_history"] = []        # [{"role":"user"/"assistant","content":...,"trace":[...]}]

for m in st.session_state["chat_history"]:   # 每次 rerun 重放历史气泡
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

prompt = st.chat_input("描述故障现象，或回答 AI 的追问…")   # 底部输入框，回车即发送
```

**② Agent：把历史一起发给模型（`agent.py`）**

```python
messages = [{"role": "system", "content": SYSTEM_PROMPT}]
messages += history          # ← 就是「记忆」：把之前的对话接在 system 之后
messages.append({"role": "user", "content": question})
```

⚠️ **一个必踩的坑**：`history` 里只能放 `user` / `assistant` 的**纯文本**，
不能把上一轮的 `tool_calls` / `tool` 消息带进来——那些 `tool_call_id` 是每轮专有的，
跨轮复用会直接报错。这是做多轮 Agent 时最容易翻车的地方。

**③ 还要在 prompt 里教它「什么时候该追问」**

```
6. 【多轮追问】信息不足以定位故障时（没说型号/报错原文/单机还是全员），
   先别急着给方案，用一两句问清最关键的 1-2 个信息。
7. 【多轮补充】用户补充信息后，结合前文一起判断，不要重复已排查过的步骤。
```

实测效果：输入「有台打印机打不出来」→ AI 追问型号和具体表现；
补充「面板显示脱机，任务卡在队列里不动」→ 它带着前文直接检索案例 1/2，给出「先重启 Print Spooler」的分步清单。

### HTTP 接口（`api.py`）

| 方法     | 路径                | 作用                       |
| ------ | ----------------- | ------------------------ |
| GET    | `/health`         | 探活：案例数、分类、向量索引数          |
| GET    | `/cases`          | 列出案例（`category` / `keyword` 筛选） |
| GET    | `/cases/{id}`     | 取单条案例详情                  |
| POST   | `/cases`          | 新增案例（自动同步语义索引）            |
| PUT    | `/cases/{id}`     | 更新案例                     |
| DELETE | `/cases/{id}`     | 删除案例                     |
| POST   | `/diagnose`       | **核心**：描述故障 → 分步排查方案（支持传 `history` 做多轮） |
| POST   | `/suggest`        | 让模型推荐分类/标签                |

> 和 ai-doc-qa 那版的本质区别：ai-doc-qa 的状态（已上传的文档块）存在**内存**里，重启就没；
> 这个项目的数据在 **SQLite**，重启不丢、天然支持对外服务——这才是能上线的形态。

调用示例：

```bash
curl -X POST http://127.0.0.1:8000/diagnose \
  -H "Content-Type: application/json" \
  -d "{\"question\": \"打印机打不出来，显示脱机\"}"
```

> 🔒 **线上访问口令**：部署上线后，调用 `/diagnose`、`/cases` 等接口需在 URL 加 `?key=<口令>`，
> 防止陌生人刷 API Key（烧钱）。`/health`、`/docs` 保持公开。
> 口令在 `api.py` 顶部的 `ACCESS_TOKEN` 常量里，可自行修改。

### 「让模型吐 JSON」是怎么做的（`suggest.py`）

企业里天天要用的技巧：让大模型输出「程序能直接用」的结构化数据，而不是一段话。

```
1. prompt 明确要求「只输出 JSON，不要其他文字」
2. 调用时传 response_format={"type": "json_object"}，强制输出合法 JSON
3. json.loads() 解析 + try/except 兜底：解析失败就让用户手填，绝不崩
```

还有一个容易踩的坑：**必须把「库里已有的分类」传给模型**，
让它优先复用，否则它会每次造一个新词（"打印"/"打印机"/"打印设备" 乱飞），
分类字段就失去筛选意义了。

### 排查 Agent 的三个工具

| 工具                    | 作用                          |
| --------------------- | --------------------------- |
| `search_similar_cases` | 按症状语义检索相似历史案例              |
| `list_categories`     | 查看知识库有哪些故障分类               |
| `get_case_detail`     | 取某条案例的完整解决步骤              |

## 🆚 和 ai-doc-qa 的区别（面试要讲）

|          | ai-doc-qa        | it-troubleshooter            |
| -------- | ---------------- | ---------------------------- |
| 输入       | 任意文档            | 结构化故障案例（症状/原因/步骤）           |
| 数据       | 一次性上传           | 可持续录入、增删改查                  |
| 输出       | 找到相关片段          | **分步排查方案** + 相似案例            |
| Agent 角色 | 查文档工具           | 诊断工具（判断分类→查案例→生成步骤）        |
| 关键词      | RAG              | RAG + AI Agent + 结构化数据         |

## 🔮 后续优化方向

- [x] 结构化故障库 + SQLite 持久化
- [x] 症状语义检索（embedding + Chroma）
- [x] 排查 Agent（Function Calling）
- [x] 录入时用模型自动补全「分类/标签」（结构化 JSON 输出）
- [x] 多轮对话式排查（追问补充症状）
- [x] 包成 FastAPI 接口 + Docker 部署
- [ ] 接企业微信/钉钉机器人：群里问一句「打印机打不出」，机器人直接回排查清单
- [ ] 案例批量导入导出（CSV/Excel），方便把历史工单一次性灌进来
- [ ] 排查结果反馈闭环：用户标记「这条建议解决了/没解决」，反哺案例质量
- [ ] 加一个 Rerank 重排序（如 gte-rerank），把检索精度再提一档

## 📄 开源协议

MIT
