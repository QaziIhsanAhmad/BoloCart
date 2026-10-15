import os


def _env(k, d=""):
    return os.getenv(k, d).strip()


class Settings:
    def __init__(self):
        # Cost guard: dry_run (log only) | test (allowlisted numbers only) | live
        self.SEND_MODE = _env("SEND_MODE", "dry_run")
        self.LIVE_APPROVED = _env("LIVE_APPROVED", "no") == "yes"
        self.TEST_RECIPIENTS = {p for p in _env("TEST_RECIPIENTS").replace(" ", "").split(",") if p}
        self.MAX_TEMPLATES_PER_DAY = int(_env("MAX_TEMPLATES_PER_DAY", "30"))
        self.NO_REPLY_HOURS = int(_env("NO_REPLY_HOURS", "24"))

        # WhatsApp Cloud API (client's own Meta account)
        self.WA_TOKEN = _env("WA_TOKEN")
        self.WA_PHONE_NUMBER_ID = _env("WA_PHONE_NUMBER_ID")
        self.WA_WABA_ID = _env("WA_WABA_ID")
        self.WA_APP_SECRET = _env("WA_APP_SECRET")
        self.WA_VERIFY_TOKEN = _env("WA_VERIFY_TOKEN")
        self.GRAPH_VERSION = _env("GRAPH_VERSION", "v25.0")
        self.TEMPLATE_NAME = _env("TEMPLATE_NAME", "cod_order_confirm")
        self.TEMPLATE_LANG = _env("TEMPLATE_LANG", "ur")

        # Shopify custom app
        self.SHOP_DOMAIN = _env("SHOP_DOMAIN")  # e.g. xxxx.myshopify.com
        self.SHOP_TOKEN = _env("SHOP_TOKEN")
        self.SHOP_API_SECRET = _env("SHOP_API_SECRET")  # signs webhooks
        self.SHOP_API_VERSION = _env("SHOP_API_VERSION", "2026-04")
        self.STORE_NAME = _env("STORE_NAME", "Demo Store")

        # Public URL (ngrok static domain) used to self-register webhooks
        self.PUBLIC_URL = _env("PUBLIC_URL").rstrip("/")

        # Local LLM
        self.OLLAMA_URL = _env("OLLAMA_URL", "http://ollama:11434").rstrip("/")
        self.OLLAMA_MODEL = _env("OLLAMA_MODEL", "qwen2.5:7b-instruct")
        self.KB_PATH = _env("KB_PATH", "kb/demo.md")

        self.DB_PATH = _env("DB_PATH", "data/agent.db")
        self.HEALTH_KEY = _env("HEALTH_KEY")  # optional key for detailed /healthz


S = Settings()
