import os

from dotenv import load_dotenv

# 环境变量只在这里读一次。业务代码不再散落 os.getenv，换密钥或地址时只改这一处。
load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL")

# 数据库配置 - mysql
DB_HOST = os.getenv("DB_HOST")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASS")
DB_PORT = os.getenv("DB_PORT")
DB_Name = os.getenv("DB_Name")

# RAG
RAG_BASE_URL = os.getenv("RAG_BASE_URL")
RAG_API_KEY = os.getenv("RAG_API_KEY")
EMBED_MODEL = os.getenv("EMBED_MODEL")
RERANK_MODEL = os.getenv("RERANK_MODEL")
EMBED_DIM = os.getenv("EMBED_DIM")
QDRANT_URL = os.getenv("QDRANT_URL")
