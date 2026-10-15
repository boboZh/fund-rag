# Python 研报检索服务

在 `fund-rag` 里手写两个接口：`POST /ingest` 和 `POST /retrieve`。算法以 `fund-backend/services/rag/` 为准，仓库根目录的 `plan-rag.md` 说明为什么这样切块、为什么用 RRF。这篇只写翻译顺序，以及写的时候会碰到的 Python。

你刚学完基础。每一段都先写不联网的纯函数，测试通过再写数据库和 HTTP。现有的 `app/api/chat.py` 是对话练习，本期不改，也不把检索接进对话。

## 本期做完是什么样

两个服务读写同一套数据：MySQL 的 `research_docs`、`research_chunks`，Qdrant 的 collection `research_chunks`。Python 不建第二套表，也不建第二个 collection。

- `POST /retrieve` 和 Node 的 `POST /api/rag/search` 打同一条问题、同一个 `userId`。返回的 `hits` 里，chunk id 的顺序一致。
- `POST /ingest` 接收已经按阅读顺序排好的段落，切块后写入 MySQL，再把向量写入 Qdrant。写完用上面的 `/retrieve` 能查到这些块。

PDF 怎么从坐标还原成段落，继续用 Node 的 `layout.js` 和 `readingOrder.js`。那两个文件是版面规则和模型整理，不是这次要练的 Python。同一份 PDF 若在 Python 里用另一套规则抽文本，后面的切块和分数都没法和 Node 对拍。

## 不做什么

- 不用 LangChain、LlamaIndex，也不上 SQLAlchemy。SQL 按 `store.js` 抄，方便逐行对照。
- 不解析登录 Cookie。请求体里带 `userId`。这是本机练习服务，不要暴露到公网。以后 Node 来调时，由 Node 填当前用户。
- 不重写实验台页面，不重写 `rag-eval.js` 的题目。
- 不和 Node 同时入库同一份研报。两个进程都会写 `research_chunks`，旧的 point id 会对不上。测 `/retrieve` 时用 Node 已经入库的数据；测 `/ingest` 时停掉 Node 的入库。
- 服务层不用 `async`。切块、检索、入库都是普通函数，测试不用 `pytest-asyncio`。`/ingest` 的路由写成 `def`，FastAPI 会放进线程池跑。`/retrieve` 的路由例外，要写成 `async def`，只用来盯浏览器有没有断开，检索本身仍在线程池里跑，见第 6 步。

## 目录

新文件按 Node 的文件拆开，方便左右对照。不要把文件命名为 `qdrant.py`，会和安装的 `qdrant-client` 包抢名字。

```
fund-rag/
  app/core/config.py                 补 MySQL、Qdrant、向量接口的配置
  app/schemas/retrieve_dto.py        /retrieve 的请求体
  app/schemas/ingest_dto.py          /ingest 的请求体
  app/services/chunk.py              对照 chunk.js
  app/services/retrieve.py           对照 retrieve.js
  app/services/embed.py              对照 embed.js
  app/services/rerank.py             对照 rerank.js
  app/services/store.py              对照 store.js
  app/services/qdrant_store.py       对照 qdrant.js
  app/services/ingest.py             对照 ingest.js 里切块之后的半段
  app/utils/cancel.py                浏览器断开后的取消开关，对照各文件里的 throwIfAborted
  app/api/retrieve.py
  app/api/ingest.py
  tests/services/test_chunk.py
  tests/services/test_retrieve.py
  tests/services/test_embed.py
  tests/utils/test_cancel.py
  tests/api/test_retrieve.py
  tests/api/test_ingest.py
```

`main.py` 只多挂这两个 router。`requirements.txt` 写上已在用的 `fastapi`、`uvicorn`、`pydantic`、`httpx`、`python-dotenv`、`pytest`，再加 `pymysql` 和 `qdrant-client`。

`app/core/config.py` 继续集中读环境变量，业务文件不再调用 `os.getenv`。需要这些名字，和 Node 对齐：

| 变量 | 作用 |
| --- | --- |
| `MYSQL_HOST` `MYSQL_PORT` `MYSQL_USER` `MYSQL_PASSWORD` `MYSQL_DATABASE` | 同一套业务库 |
| `QDRANT_URL` | 默认 `http://127.0.0.1:6333` |
| `RAG_API_KEY` `RAG_BASE_URL` | 硅基流动。和对话用的 `DEEPSEEK_API_KEY` 分开 |
| `EMBED_MODEL` `EMBED_DIM` | 默认 `BAAI/bge-m3`、`1024` |
| `RERANK_MODEL` | 默认 `BAAI/bge-reranker-v2-m3` |

## 书写顺序

前一段的测试通过，再写下一段。不要从 PDF 或 FastAPI 路由开始。

### 1. 切块

翻译 `chunk.js` 的 `splitParagraphs`。入参是段落列表和 `{ maxChars, overlapParagraphs }`，出参是块列表。函数不读文件、不访问数据库。

段落和块都用字典，键名与 Node 测试里的字段一致：`text`、`heading`、`kind`、`page`，以及 `chunkIndex`、`chunkType`、`pageStart`、`pageEnd`、`heading`、`paragraphCount`、`content`、`charCount`。服务内部不要用 Pydantic 模型传来传去，否则和 `chunk.js` 对不上。

规则以 `plan-rag.md` 的「切块」一节为准，数字也照抄：默认 `maxChars = 800`，`overlapParagraphs` 只能是 `0` 或 `1`。表格整块保留，首页 `summary` 单独成块，超长段只按 `。！？；` 拆。

`tests/services/test_chunk.py` 把 `chunk.test.js` 里的输入和期望原文抄过来。至少锁住这四条：

- 三个短段合并后仍是一整块，切点在段落之间。
- 新小节强制开新块。
- 超长段按句号拆，不从字中间截断。
- `overlapParagraphs = 1` 时，下一块以完整的上一段开头。

这一步只用函数、列表、字典和 `pytest`。断言内容原文，不要只断言块的数量。

### 2. 检索里的纯函数

仍放在 `retrieve.py`，先不写 `retrieve()` 本体。对照 `retrieve.js`：

- `stock_code_of`：问题里恰好一个六位代码才返回它。两个以上，或六位前后还挨着数字（如 `16002567`），返回 `None`。
- `keyword_text_of`：代码已经拿去过滤时，从全文查询里去掉代码，并去掉只包着代码的空括号。ngram 会把 `600256` 切成 `60`、`00`、`02`，留在全文里会把无关段落顶上来。
- `fuse_by_rank`：融合分是 `1 / (60 + 向量名次) + 1 / (60 + 关键词名次)`。某一路没有这条，那一路不加分。分数相同按 chunk id 升序，保证同一条问题重复检索名次不变。

常量与 Node 相同：`RRF_K = 60`，召回上限 `20`，最终 `topK` 默认 `5`、最大 `20`。名次从 `1` 开始。

`tests/services/test_retrieve.py` 先覆盖 `retrieve.test.js` 里不联网的用例：括号中的 `600256`、同时出现两家公司、更长数字、去掉代码后的关键词文本。RRF 用两手造的名次列表，手算融合分写进断言。

这里会用到的 Python：`re` 的 `(?<!\d)` 环视，和 JavaScript 里同一个写法。除法 `/` 得到小数，RRF 就用它。没有值时写 `None`，不要写 `null`。

### 3. 读 MySQL 和 Qdrant

只实现检索要读的查询，写入留到入库。

`store.py` 用 `pymysql`。每次查询打开连接、执行、关闭，用 `with`。返回值转成普通字典，键用 `id`、`content`、`stockCode` 这种，和 `retrieve.js` 拿到的行一致。MySQL 驱动会把全文分数给成 `Decimal`，在这里转成 `float`，后面的排序才和 Node 一样。

关键词 SQL 照抄 `store.js` 的 `searchChunksByKeyword`：`MATCH(content) AGAINST (? IN NATURAL LANGUAGE MODE)`，`user_id` 是必需条件。能确定股票时先查出 `doc_id` 再 `IN (...)`。`LIMIT` 用已经收成 `1` 到 `20` 的整数拼进 SQL，不用占位符，原因和 Node 注释里写的一样。

`qdrant_store.py` 只包搜索。过滤条件在 Qdrant 内部和向量搜索一起做：必须有 `user_id`；有股票代码再加 `stock_code`；有 `dateFrom` 再加 `report_date` 的范围。`report_date` 在 payload 里是 `20261005` 这种整数。先全局取 20 条再在 Python 里过滤是错的，正确公司的段落可能根本没进候选。

Qdrant 只返回 point id 和分数。正文用这些 id 回 MySQL 查。点的 id 就是 `research_chunks.id`。

### 4. 问题向量和重排

`embed.py`、`rerank.py` 用 `httpx` 直接请求硅基流动，不用 OpenAI 的 SDK。对话练习里的 SDK 留在 `llm_services.py`。这里要看清 URL、批次和维度。

- 问题原样送去 embedding，不加 `query:` 前缀。`bge-m3` 不使用那类指令。
- 返回维度必须等于 `EMBED_DIM`。不对就抛错，不能拿去搜索。
- 重排请求体与 `rerank.js` 相同：`model`、`query`、`documents`、`top_n` 为文档条数、`return_documents: false`。超时 60 秒，失败再试 2 次。
- 进程内复用一个 `httpx.Client`，不要每个问题新建一次。
- 函数多收一个可选参数 `cancel`，类型是 `threading.Event`，对应 Node 的 `signal`。不传时行为和现在一样。用法在第 6 步。

`tests/services/test_embed.py` 用 `httpx.MockTransport` 返回假的向量和重排分数，断言维度不对时抛错、重排结果按 `index` 对回原段落。不要在单元测试里访问外网。

### 5. `POST /retrieve`

把上面几段接成 `retrieve()`，顺序与 `retrieve.js` 相同：

1. 校验 `mode` 只能是 `vector`、`keyword`、`hybrid`，`userId` 是正整数。
2. 解析股票代码和 `dateFrom`。请求里显式带了六位代码时，以它为准。
3. `vector`、`keyword` 各按自己的分取结果。`hybrid` 两路各先取 20 条，RRF 后再截断。
4. `rerank` 为真时，召回放宽到 20 条，重排后再取 `topK`。阈值只和重排分比较。不传 `minScore` 表示不设阈值；`0` 是合法阈值，低于 `0` 的段会被丢掉。

Pydantic 模型只放在 `app/schemas/retrieve_dto.py`，用来拒绝非法 JSON。路由里校验失败返回 `400`，消息与 Node 一致，例如「股票代码需要是 6 位数字」。服务函数仍接收普通参数。

请求：

```json
{
  "query": "广汇能源（600256）的目标价",
  "userId": 1,
  "topK": 5,
  "mode": "hybrid",
  "rerank": true,
  "minScore": null,
  "stockCode": "",
  "dateFrom": ""
}
```

响应字段与 Node 的 `retrieve()` 相同：`mode`、`topK`、`stockCode`、`keywordQuery`、`rerank`、`minScore`、`hits`、`ranked`。每条 hit 带上 `id`、`rank`、`score`、`vectorRank`、`keywordRank`、`rrfRank`、`rerankScore`、`content`，实验台要对的就是这些列。

`tests/api/test_retrieve.py` 用 `unittest.mock.patch` 换掉 `retrieve`，只断言路由把 body 交给了服务、非法 `mode` 返回 `400`。算法测试留在第 2 步，不在路由测试里连数据库。

手测：Node 已经入库的那个用户，同一条问题分别打 `POST /api/rag/search` 和 `POST /retrieve`。先比 `mode=keyword`，这一路不调用向量接口，id 顺序必须相同。再比 `hybrid` 且 `rerank=false`。

### 6. 浏览器中途断开就停

对照 `routes/rag.js` 的 `res.on("close")` 和各服务里的 `throwIfAborted`。用户刷新、关页或前端取消请求后，Node 不再调用向量和重排接口，也不再重试。打开重排时，一次检索最多会发出 6 次付费请求：向量、重排各 1 次，再各重试 2 次。用户已经走了，这些请求只花钱。

Python 没有 `AbortSignal`，线程也不能从外面强行停掉。做法是两半：

1. 路由那一半。`/retrieve` 写成 `async def`，用 `run_in_threadpool` 把同步的 `retrieve()` 放进线程池。同时开一个小任务，每 0.5 秒调用一次 `await request.is_disconnected()`。发现断开就 `cancel.set()`。检索结束后取消这个小任务。
2. 服务那一半。`retrieve()`、`embed`、`rerank` 都收 `cancel`。每一步开始前调用 `throw_if_cancelled(cancel)`，已经取消就抛 `Cancelled`。位置和 Node 的 `throwIfAborted` 一一对应：进入 `retrieve()`、拿到问题向量之后、关键词召回之前、重排之前、每一批向量请求之前、每次重试之前。
3. 重试之间的等待不用 `time.sleep`，改用 `cancel.wait(秒数)`。它在开关被拨动时立刻返回 `True`，这时抛 `Cancelled`，不再发下一次请求。这对应 Node 里 `sleep` 监听 `abort` 的那段。

`app/utils/cancel.py` 放四样东西：`Cancelled` 异常、`throw_if_cancelled`、`wait_or_cancel`，以及路由用的 `run_until_disconnect`。Node 每个文件各抄了一份 `throwIfAborted`，Python 放一处，各文件导入。

和 Node 的差别要知道：

- 已经发出去的那次 `httpx` 请求停不下来，要等它返回或到 60 秒超时。Node 能把正在进行的请求断掉。断开后最多再多花这一次请求，后面的步骤都不会再发。
- MySQL 查询同样停不下来。全文查询很快，不影响。
- 路由捕获 `Cancelled` 后返回一个空响应。浏览器已经收不到，状态码写成 `499`，日志里能区分出“用户走了”和“检索出错”，不要打成 `500` 的错误日志。

`/ingest` 不跟着浏览器断开取消。入库写到一半停下，MySQL 的块和 Qdrant 的向量会对不上。Node 的 `ingest.js` 也没有接 `signal`。

`tests/utils/test_cancel.py` 用普通 `def` 写，里面用 `asyncio.run(...)` 跑路由那一半，不需要 `pytest-asyncio`：

- 造一个假的 `request`，`is_disconnected` 第一次就返回 `True`。传进去的函数里调用 `cancel.wait(2)`，断言它在 2 秒之前就返回 `True`。
- 假 `request` 一直返回 `False` 时，函数正常跑完，返回值原样交回，`cancel` 没有被拨动。

`tests/services/test_embed.py` 加两条，对应 `embed.test.js` 的「已取消的请求不再打到接口」：

- `cancel` 先 `set()` 再调用，`MockTransport` 一次都没被调用，抛 `Cancelled`。
- 第一次请求失败、等待重试期间拨动开关，接口只被调用 1 次，不是 3 次。

手测：把重排打开，在 `embed.py` 的重试等待里临时加长到 5 秒，用 `curl` 发 `/retrieve`，然后按 `Ctrl+C`。服务日志里应看到 `499`，硅基流动那边不再收到后面的请求。

### 7. `POST /ingest`

只翻译 `ingest.js` 里「段落已经有了」之后的半段。入参是元数据和段落列表，出参是文档 id 和状态。

请求是 JSON，不是 PDF：

```json
{
  "userId": 1,
  "fileHash": "32位小写md5",
  "fileName": "广汇能源（600256）.pdf",
  "stockCode": "600256",
  "stockName": "广汇能源",
  "title": "公司深度报告",
  "org": "开源证券",
  "reportDate": "2026-10-05",
  "maxChars": 800,
  "overlapParagraphs": 1,
  "paragraphs": [
    { "text": "目标价上调到25元。", "heading": "估值", "kind": "text", "page": 7 }
  ]
}
```

`fileHash` 用来和 Node 的 `uniq_user_file` 对齐。同一用户、同一哈希已存在时，返回已有文档和 `duplicate: true`，不要再切一遍。

执行顺序：

1. 校验元数据。代码 6 位，日期 `YYYY-MM-DD`，`maxChars` 收在 `400` 到 `1200`，缺省 `800`。`overlapParagraphs` 不是 `0` 就是 `1`。
2. 插入 `research_docs`，状态改为 `parsing`。
3. `split_paragraphs`。没有切出块就标 `failed`，写上原因。
4. 按 `doc_id` 删掉 Qdrant 里的旧 point，再换 MySQL 里的块。顺序不能反：重切会插入新的自增 id，旧向量还会指着已经删掉的 id。
5. 状态改为 `embedding`。送去向量化的文本是「股票名称｜小节标题」加换行再加原文。MySQL 的 `content` 保持原文，全文检索和展示都用它。
6. 每批 16 条。返回条数或维度不对，整份标 `failed`，不 upsert。
7. point id 用 `research_chunks.id`，payload 只放 `qdrant.js` 里的短字段：`user_id`、`doc_id`、`stock_code`、`stock_name`、`title`、`org`、`report_date`、`page_start`、`heading`、`chunk_type`。
8. 成功后状态为 `embedded`，并写下 `embed_model`。

先做成同步：函数跑完再返回最终状态。一份短研报足够看清切块和向量。确认和 Node 对得上之后，再考虑后台任务。不要在第一次就开线程。

失败时把 `error_msg` 截到 500 字写入 `failed`。更新状态本身又失败时只打日志，不要把这个异常再抛成另一个未处理错误。

`tests/api/test_ingest.py` 同样只测路由校验：缺标题、股票代码不是 6 位，返回 `400`。切块测试已经在第 1 步。写入 MySQL 和 Qdrant 的对拍用一条手测：拿 Node 某份研报的 `layout.json` 里的 `paragraphs` 调用 `/ingest`，再拿 `/retrieve` 查一个该段里才有的句子，`hits` 里应出现这块。

## 接口一览

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/ingest` | 段落列表切块，写入 MySQL 和 Qdrant |
| POST | `/retrieve` | 向量、关键词或混合检索，可选重排 |

Node 上的预览、文档列表、重切、删除仍走原来的页面。Python 本期不复制这套管理接口。

## 和 Node 对拍时看什么

同一条问题、同一个用户：

| 模式 | 必须一致的 | 可以不同的 |
| --- | --- | --- |
| `keyword` | chunk id 顺序 | 无 |
| `hybrid`，关闭重排 | chunk id 顺序 | 无。两路都是名次，RRF 不看原始分 |
| 打开重排 | 进入重排的那 20 个 id 集合 | 重排分的微小小数差。差到名次变了，先查送给重排的原文是不是同一段 |

id 对不上时，按这个顺序查：`user_id` 过滤丢了、股票代码被 ngram 留在了全文查询里、point id 没用 MySQL 自增 id、向量文本多加或少加了「公司｜小节」前缀。
