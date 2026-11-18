import os

# Tests mock model calls; allow app import without a production secret in CI.
os.environ.setdefault("DEEPSEEK_API_KEY", "test-only-not-a-real-key")
