import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path, override=True)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
MARKETDATA_API_TOKEN = os.environ.get("MARKETDATA_API_TOKEN", "")
EODHD_API_TOKEN = os.environ.get("EODHD_API_TOKEN", "")
FINANCEFLOW_API_TOKEN = os.environ.get("FINANCEFLOW_API_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Rate limits
MARKETDATA_RATE_LIMIT = 100   # requests per minute
EODHD_RATE_LIMIT = 20         # requests per second (paid plan)

# Batch sizes for DB inserts
DB_BATCH_SIZE = 1000
