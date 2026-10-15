# 目标下的命令行必须用 Tab 缩进。
# 直接调用 .venv 里的可执行文件，新开的终端不用先 source activate。
# 在 fund-rag 目录执行：make install、make dev。

# fund-backend 占用 9000。入库接口用 9001，Node 通过 RAG_INGEST_URL 调用 /ingest。
dev:
	.venv/bin/uvicorn main:app --reload --port 9001

install:
	.venv/bin/pip install -r requirements.txt

test:
	.venv/bin/pytest 