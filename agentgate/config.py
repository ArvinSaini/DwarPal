"""Settings loaded from the environment (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from dotenv import dotenv_values


@dataclass
class Settings:
    db_path: str = "agentgate.db"
    merchant_id: str = "trail-and-turf"
    merchant_name: str = "Trail & Turf"
    base_url: str = "http://127.0.0.1:8000"
    merchant_token: str = "dev-merchant-token"
    razorpay_key_id: str | None = None
    razorpay_key_secret: str | None = None
    razorpay_webhook_secret: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_timeout_s: int = 60
    fake_outcomes: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, dotenv: str | None = ".env") -> "Settings":
        """Build settings. With ``env`` given, only that mapping is read (tests); otherwise .env then os.environ."""
        merged: dict[str, str] = {}
        if env is None:
            if dotenv and os.path.exists(dotenv):
                merged.update({k: v for k, v in dotenv_values(dotenv).items() if v is not None})
            merged.update(os.environ)
        else:
            merged.update(env)

        def get(key: str, default: str | None = None) -> str | None:
            value = merged.get(key)
            return value if value else default

        return cls(
            db_path=get("AGENTGATE_DB", "agentgate.db"),
            merchant_id=get("MERCHANT_ID", "trail-and-turf"),
            merchant_name=get("MERCHANT_NAME", "Trail & Turf"),
            base_url=get("AGENTGATE_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
            merchant_token=get("MERCHANT_TOKEN", "dev-merchant-token"),
            razorpay_key_id=get("RAZORPAY_KEY_ID"),
            razorpay_key_secret=get("RAZORPAY_KEY_SECRET"),
            razorpay_webhook_secret=get("RAZORPAY_WEBHOOK_SECRET"),
            llm_base_url=get("LLM_BASE_URL"),
            llm_api_key=get("LLM_API_KEY"),
            llm_model=get("LLM_MODEL"),
            llm_timeout_s=int(get("LLM_TIMEOUT_S", "60")),
            fake_outcomes=get("FAKE_OUTCOMES"),
        )

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)

    @property
    def razorpay_configured(self) -> bool:
        return bool(self.razorpay_key_id and self.razorpay_key_secret)
