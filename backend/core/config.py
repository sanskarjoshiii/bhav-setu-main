"""Loads every YAML in config/ plus .env into one frozen Settings object.

Usage:
    from core.config import settings
    settings.app.horizons            -> [1, 3, 7, 15]
    settings.mandis.mandis[0]["name"] -> "Lasalgaon"
    settings.env.database_url

Fails loudly at import time if a required environment variable is missing.
Rule 7: every tunable number lives in YAML, never hardcoded in Python.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

import yaml
from dotenv import load_dotenv
import os

from core.errors import ConfigError

# repo root = .../bhav-setu (this file is backend/core/config.py)
ROOT: Path = Path(__file__).resolve().parents[2]
CONFIG_DIR: Path = ROOT / "config"

# Required at import time. Everything else is required only by the phase that uses it,
# and is fetched through Env.require() so the failure names the missing variable.
REQUIRED_ENV: tuple[str, ...] = ("DATABASE_URL",)

CONFIG_FILES: tuple[str, ...] = (
    "app",
    "irrigation",
    "locations",
    "mandis",
    "crops",
    "cost_model",
    "model",
    "sources",
    "decision",
)


class ConfigNode(Mapping[str, Any]):
    """Read-only dict wrapper allowing both node.key and node["key"] access."""

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any]) -> None:
        object.__setattr__(self, "_data", dict(data))

    def __getattr__(self, name: str) -> Any:
        try:
            return _wrap(self._data[name])
        except KeyError as exc:
            raise ConfigError(f"config key not found: {name}") from exc

    def __getitem__(self, key: str) -> Any:
        try:
            return _wrap(self._data[key])
        except KeyError as exc:
            raise ConfigError(f"config key not found: {key}") from exc

    def __setattr__(self, name: str, value: Any) -> None:
        raise ConfigError("config is frozen; edit the YAML file instead")

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        return _wrap(self._data[key]) if key in self._data else default

    def to_dict(self) -> dict[str, Any]:
        return dict(self._data)

    def __repr__(self) -> str:
        return f"ConfigNode({sorted(self._data)})"


def _wrap(value: Any) -> Any:
    if isinstance(value, dict):
        return ConfigNode(value)
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    return value


def _load_yaml(name: str) -> ConfigNode:
    path = CONFIG_DIR / f"{name}.yaml"
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError(f"config file must be a YAML mapping: {path}")
    return ConfigNode(data)


@dataclass(frozen=True)
class Env:
    """Environment variables. Optional ones are '' until the phase that needs them."""

    database_url: str
    redis_url: str
    data_gov_in_api_key: str
    agmarknet_resource_id: str
    whatsapp_phone_number_id: str
    whatsapp_business_account_id: str
    whatsapp_access_token: str
    whatsapp_app_secret: str
    whatsapp_verify_token: str
    mongodb_url: str
    mongodb_db: str
    smtp_host: str
    smtp_port: str
    smtp_user: str
    smtp_password: str
    smtp_from: str
    app_base_url: str

    def require(self, field: str) -> str:
        """Return an optional env var, raising a clear error if it is still blank."""
        value = getattr(self, field, "")
        if not value:
            raise ConfigError(
                f"environment variable {field.upper()} is required for this step "
                f"but is empty in .env"
            )
        return str(value)


def _load_env() -> Env:
    load_dotenv(ROOT / ".env")
    missing = [name for name in REQUIRED_ENV if not os.getenv(name)]
    if missing:
        raise ConfigError(
            "missing required environment variable(s): "
            + ", ".join(missing)
            + f". Copy {ROOT / '.env.example'} to .env and fill them in."
        )
    return Env(
        database_url=os.environ["DATABASE_URL"],
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6380/0"),
        data_gov_in_api_key=os.getenv("DATA_GOV_IN_API_KEY", ""),
        agmarknet_resource_id=os.getenv("AGMARKNET_RESOURCE_ID", ""),
        whatsapp_phone_number_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID", ""),
        whatsapp_business_account_id=os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", ""),
        whatsapp_access_token=os.getenv("WHATSAPP_ACCESS_TOKEN", ""),
        whatsapp_app_secret=os.getenv("WHATSAPP_APP_SECRET", ""),
        whatsapp_verify_token=os.getenv("WHATSAPP_VERIFY_TOKEN", ""),
        mongodb_url=os.getenv("MONGODB_URL", "mongodb://bhav:bhav@localhost:27018/?authSource=admin"),
        mongodb_db=os.getenv("MONGODB_DB", "bhav_history"),
        smtp_host=os.getenv("SMTP_HOST", ""),
        smtp_port=os.getenv("SMTP_PORT", ""),
        smtp_user=os.getenv("SMTP_USER", ""),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        smtp_from=os.getenv("SMTP_FROM", ""),
        app_base_url=os.getenv("APP_BASE_URL", "http://localhost:3000"),
    )


@dataclass(frozen=True)
class Settings:
    root: Path
    env: Env
    app: ConfigNode
    irrigation: ConfigNode
    locations: ConfigNode
    mandis: ConfigNode
    crops: ConfigNode
    cost_model: ConfigNode
    model: ConfigNode
    sources: ConfigNode
    decision: ConfigNode

    def path(self, *parts: str) -> Path:
        """Resolve a repo-relative path from config (e.g. data/raw/mandi_history.csv)."""
        return self.root.joinpath(*parts)


def _build() -> Settings:
    loaded = {name: _load_yaml(name) for name in CONFIG_FILES}
    return Settings(root=ROOT, env=_load_env(), **loaded)


settings: Settings = _build()


def crop_specs() -> dict[str, dict[str, Any]]:
    """Every crop in config/crops.yaml, as plain dicts.

    One place that knows how to read that file, because three callers need it —
    scripts/init_db.py seeds `commodities` from it, ingestion/datagov.py builds
    the API's commodity filter from it, and economics/spoilage.py reads k_c.
    Three copies of the same loop is three chances to disagree about what counts
    as a crop.

    Keys starting with "_" are reserved for non-crop settings and are skipped,
    so a future constant added to that file cannot be seeded as a vegetable.
    """
    out: dict[str, dict[str, Any]] = {}
    for name, spec in settings.crops.to_dict().items():
        if str(name).startswith("_"):
            continue
        out[str(name)] = spec.to_dict() if hasattr(spec, "to_dict") else dict(spec)
    return out
