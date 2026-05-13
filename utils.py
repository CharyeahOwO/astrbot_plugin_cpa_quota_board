from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SENSITIVE_KEYS = re.compile(r"(password|token|secret|key|authorization|access_token|refresh_token)", re.I)


class ConfigError(ValueError):
    pass


def normalize_cpa_url(url: str) -> str:
    value = (url or "").strip().rstrip("/")
    if not value:
        raise ConfigError("请先配置 cpa_url")
    if "management.html" in value or "#/" in value or value.endswith("#"):
        raise ConfigError("cpa_url 应填写 CLIProxyAPI 根地址，例如 https://api.example.com，不要填写 management.html#/ 页面地址。")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("cpa_url 必须是 http 或 https 根地址，例如 https://api.example.com")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def management_url(cpa_url: str, path: str) -> str:
    return f"{normalize_cpa_url(cpa_url)}/v0/management/{path.lstrip('/')}"


def mask_secret(value: Any, keep: int = 4) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= keep * 2:
        return "***"
    return f"{text[:keep]}...{text[-keep:]}"


def sanitize_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"Bearer\s+[^\s,;]+", "Bearer ***", text, flags=re.I)
    text = re.sub(r"(access_token|refresh_token|token|password|api_key|api-key|key)=([^&\s]+)", r"\1=***", text, flags=re.I)
    text = re.sub(r"(sk-[A-Za-z0-9_-]{8,})", "sk-***", text)
    text = re.sub(r"(AIza[0-9A-Za-z_-]{8,})", "AIza***", text)
    return text


def sanitize_url(url: str) -> str:
    parsed = urlsplit(url)
    pairs = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        pairs.append((key, "***" if SENSITIVE_KEYS.search(key) else value))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(pairs), parsed.fragment))


def redact_obj(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("***" if SENSITIVE_KEYS.search(str(key)) else redact_obj(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_obj(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


def safe_percent(value: Any) -> int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 1:
        number *= 100
    return max(0, min(100, int(round(number))))


def format_reset_time(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        return datetime.fromtimestamp(seconds).strftime("%m/%d %H:%M")
    text = str(value)
    try:
        normalized = text.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).astimezone().strftime("%m/%d %H:%M")
    except ValueError:
        return text[:16]


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return default


def cleanup_old_files(directory: Path, max_age_seconds: int = 86400) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - max_age_seconds
    for path in directory.glob("*.png"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue


def plugin_data_dir(plugin_name: str) -> Path:
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        base = Path(get_astrbot_data_path())
    except Exception:
        base = Path.cwd() / "data"
    path = base / "plugin_data" / plugin_name
    path.mkdir(parents=True, exist_ok=True)
    return path
