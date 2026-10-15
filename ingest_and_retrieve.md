## `/ingest`

接口内部的顺序是：

1. 校验元数据，插入 `research_docs`，状态写成 `parsing`。同一用户、同一 `fileHash` 已存在时直接返回，带 `duplicate: true`，不再切块。
2. `split_paragraphs` 切块。一块都没有就标 `failed`。
3. 先按 `doc_id` 删除 Qdrant 里的旧 point，再替换 MySQL 里的块。MySQL 的 `content` 存原文。
4. 状态改为 `embedding`。送去向量化的字符串是「股票名称｜小节标题」加换行再加原文。
5. 向量写入 Qdrant。point 的 id 用上一步 MySQL 生成的 `research_chunks.id`。
6. 成功后状态改为 `embedded`。


## `/retrieve`

这四项由请求里的 `mode` 和 `rerank` 决定，不是每次都全做：

- `keyword`：只做 MySQL 全文检索。
- `vector`：只做 Qdrant 向量检索。Qdrant 只返回 point id 和分数，正文再用这些 id 回 MySQL 查。
- `hybrid`：两路各取 20 条，用 RRF（Reciprocal Rank Fusion，按名次融合的算法，不使用原始分数）合并后再截断。
- `rerank` 为真时，上面的召回放宽到 20 条，重排后再取 `topK`。`minScore` 只和重排分比较。不传表示不设阈值；传 `0` 会丢掉重排分低于 `0` 的段。