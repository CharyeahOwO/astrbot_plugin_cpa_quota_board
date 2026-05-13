from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from typing import Any

import aiohttp

try:
    from .models import AuthFile, QuotaAccount, QuotaItem, QuotaProvider, QuotaReport, account_status, build_summary, status_from_percent
    from .utils import format_reset_time, management_url, now_text, redact_obj, safe_percent, sanitize_text, sanitize_url
except ImportError:
    from models import AuthFile, QuotaAccount, QuotaItem, QuotaProvider, QuotaReport, account_status, build_summary, status_from_percent
    from utils import format_reset_time, management_url, now_text, redact_obj, safe_percent, sanitize_text, sanitize_url


PROVIDER_NAMES = {
    "antigravity": "Antigravity",
    "claude": "Claude",
    "gemini": "Gemini",
    "gemini-cli": "Gemini CLI",
    "geminicli": "Gemini CLI",
    "kimi": "Kimi",
    "vertex": "Vertex",
    "codex": "Codex",
    "openai": "Codex",
}

QUOTA_PROVIDERS = {"antigravity", "gemini", "gemini-cli", "codex"}


class CPAClientError(RuntimeError):
    pass


class CPAAuthError(CPAClientError):
    pass


class CPAEndpointMissing(CPAClientError):
    pass


class CPAClient:
    def __init__(
        self,
        cpa_url: str,
        cpa_password: str,
        *,
        verify_ssl: bool = True,
        request_timeout: int = 30,
        warning_percent: int = 20,
        critical_percent: int = 5,
        max_accounts_per_provider: int = 20,
    ):
        self.cpa_url = cpa_url
        self.cpa_password = cpa_password
        self.verify_ssl = verify_ssl
        self.request_timeout = max(1, int(request_timeout or 30))
        self.warning_percent = int(warning_percent)
        self.critical_percent = int(critical_percent)
        self.max_accounts_per_provider = max(1, int(max_accounts_per_provider or 20))

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.cpa_password}", "Content-Type": "application/json"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = management_url(self.cpa_url, path)
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=self._headers()) as session:
                async with session.request(method, url, ssl=self.verify_ssl, **kwargs) as response:
                    text = await response.text()
                    if response.status in {401, 403}:
                        raise CPAAuthError("Management Key 认证失败，请检查 cpa_password 和远程管理权限。")
                    if response.status == 404:
                        if path.strip("/") == "usage":
                            raise CPAEndpointMissing("当前 CLIProxyAPI 可能已移除 legacy usage endpoint，已跳过使用统计查询。")
                        raise CPAEndpointMissing(f"CLIProxyAPI 管理接口不存在：{sanitize_url(url)}")
                    if response.status >= 400:
                        raise CPAClientError(f"请求 {sanitize_url(url)} 失败：HTTP {response.status} {sanitize_text(text[:300])}")
                    if not text:
                        return {}
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError as exc:
                        raise CPAClientError(f"接口返回不是 JSON：{sanitize_url(url)}") from exc
        except aiohttp.ClientError as exc:
            raise CPAClientError(f"请求 {sanitize_url(url)} 失败：{sanitize_text(exc)}") from exc

    async def get_auth_files(self) -> list[AuthFile]:
        data = await self._request("GET", "auth-files")
        files = data.get("files", []) if isinstance(data, dict) else data if isinstance(data, list) else []
        if not isinstance(files, list):
            return []
        return [AuthFile.from_api(item) for item in files if isinstance(item, dict)]

    async def api_call(self, auth: AuthFile, method: str, url: str, *, headers: dict[str, str] | None = None, data: Any = None) -> Any:
        payload: dict[str, Any] = {
            "auth_index": auth.auth_index,
            "method": method.upper(),
            "url": url,
        }
        if headers:
            payload["header"] = headers
        if data is not None:
            payload["data"] = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        return self._unwrap_api_call_response(await self._request("POST", "api-call", json=payload))

    async def set_usage_statistics_enabled(self, enabled: bool) -> None:
        await self._request("PUT", "usage-statistics-enabled", json={"value": bool(enabled)})

    async def fetch_all_quotas(self) -> QuotaReport:
        try:
            auth_files = await self.get_auth_files()
        except CPAClientError as exc:
            return self._error_report(str(exc))

        active_auths = [auth for auth in auth_files if not auth.disabled]
        if not active_auths:
            if auth_files:
                providers = sorted({self._canonical_provider(auth.provider) for auth in auth_files})
                return QuotaReport.empty(f"API 返回了 {len(auth_files)} 个账号，但账号均已禁用或不可用：{', '.join(providers)}")
            return QuotaReport.empty("API 返回为空，未发现可查询额度的账号。")

        grouped: dict[str, list[AuthFile]] = defaultdict(list)
        for auth in active_auths:
            provider_type = self._canonical_provider(auth.provider)
            if len(grouped[provider_type]) < self.max_accounts_per_provider:
                grouped[provider_type].append(auth)

        providers: list[QuotaProvider] = []
        for provider_type, accounts in grouped.items():
            results = await asyncio.gather(*(self._fetch_account(provider_type, auth) for auth in accounts), return_exceptions=True)
            quota_accounts: list[QuotaAccount] = []
            for auth, result in zip(accounts, results):
                if isinstance(result, Exception):
                    quota_accounts.append(self._account_error(auth, sanitize_text(result)))
                else:
                    quota_accounts.append(result)
            providers.append(QuotaProvider(name=PROVIDER_NAMES.get(provider_type, provider_type), type=provider_type, accounts=quota_accounts))

        return QuotaReport(generated_at=now_text(), summary=build_summary(providers), providers=providers)

    async def _fetch_account(self, provider_type: str, auth: AuthFile) -> QuotaAccount:
        if auth.unavailable:
            return self._account_error(auth, auth.status_message or "账号当前不可用")
        if provider_type in {"gemini", "gemini-cli", "antigravity"}:
            items = await self._fetch_google_like(provider_type, auth)
        elif provider_type == "codex":
            items = await self._fetch_codex(auth)
        else:
            items = [QuotaItem(id="quota", label="额度", percent=None, status="unknown", raw_message=f"暂未支持 provider={provider_type} 的额度接口")]
        return QuotaAccount(id=auth.id, name=auth.name, display_name=auth.display_name, status=account_status(items), items=items)

    async def _fetch_google_like(self, provider_type: str, auth: AuthFile) -> list[QuotaItem]:
        urls = [
            "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota",
            "https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels",
        ]
        body = self._google_quota_body(auth)
        errors: list[str] = []
        for url in urls:
            try:
                data = await self.api_call(auth, "POST", url, headers={"Authorization": "Bearer $TOKEN$", "Content-Type": "application/json"}, data=body)
                items = self._parse_google_quota(data, provider_type)
                if items:
                    return items
            except CPAClientError as exc:
                errors.append(str(exc))
        return [QuotaItem(id="quota", label="Google OAuth 额度", percent=None, status="error", raw_message=sanitize_text("; ".join(errors) or "未返回可识别额度"))]

    async def _fetch_codex(self, auth: AuthFile) -> list[QuotaItem]:
        try:
            data = await self.api_call(auth, "GET", "https://chatgpt.com/backend-api/wham/usage", headers={"Authorization": "Bearer $TOKEN$"})
        except CPAClientError as exc:
            return [QuotaItem(id="codex", label="Codex 额度", percent=None, status="error", raw_message=sanitize_text(exc))]
        items = self._parse_codex_quota(data)
        if items:
            return items
        return [QuotaItem(id="codex", label="Codex 额度", percent=None, status="unknown", raw_message="未返回可识别的 Codex 额度窗口")]

    def _parse_google_quota(self, data: Any, provider_type: str) -> list[QuotaItem]:
        clean = redact_obj(data)
        candidates: list[dict[str, Any]] = []
        if isinstance(data, list):
            candidates.extend(item for item in data if isinstance(item, dict))
        elif isinstance(data, dict):
            candidates.extend(self._quota_candidates(data))
            if not candidates:
                candidates.append(data)
        items: list[QuotaItem] = []
        for index, entry in enumerate(candidates):
            percent = self._extract_percent(entry)
            if percent is None and not self._looks_like_quota(entry):
                continue
            item_id = str(entry.get("id") or entry.get("name") or entry.get("model") or f"quota-{index + 1}")
            label = str(entry.get("displayName") or entry.get("display_name") or entry.get("label") or entry.get("name") or entry.get("model") or self._default_google_label(provider_type))
            reset_at = format_reset_time(entry.get("resetAt") or entry.get("reset_at") or entry.get("resetTime") or entry.get("refreshTime"))
            status = status_from_percent(percent, self.warning_percent, self.critical_percent)
            items.append(QuotaItem(id=item_id, label=label, percent=percent, reset_at=reset_at, status=status, raw_message=""))
        if not items:
            if isinstance(clean, dict):
                message = sanitize_text(clean.get("message") or clean.get("error") or "接口成功但未解析到额度数据")
            else:
                message = "接口成功但未解析到额度数据"
            items.append(QuotaItem(id="quota", label=self._default_google_label(provider_type), percent=None, status="unknown", raw_message=message))
        return items

    def _parse_codex_quota(self, data: Any) -> list[QuotaItem]:
        if not isinstance(data, dict):
            return []
        windows = []
        for key, label in (("primary_window", "Codex 5h"), ("secondary_window", "Codex 7d"), ("primaryWindow", "Codex 5h"), ("secondaryWindow", "Codex 7d")):
            value = data.get(key)
            if isinstance(value, dict):
                windows.append((key, label, value))
        if not windows and any(key in data for key in ("remaining", "limit", "used", "percent")):
            windows.append(("codex", "Codex 额度", data))
        items: list[QuotaItem] = []
        for key, label, window in windows:
            percent = self._extract_percent(window)
            reset_at = format_reset_time(window.get("reset_at") or window.get("resetAt") or window.get("end_time") or window.get("ends_at"))
            items.append(QuotaItem(id=key.replace("_window", ""), label=label, percent=percent, reset_at=reset_at, status=status_from_percent(percent, self.warning_percent, self.critical_percent), raw_message=""))
        return items

    def _extract_percent(self, data: dict[str, Any]) -> int | None:
        for key in ("percent", "remaining_percent", "remainingPercent", "available_percent", "availablePercent", "usage_percentage"):
            percent = safe_percent(data.get(key))
            if percent is not None:
                return 100 - percent if key == "usage_percentage" else percent
        remaining = data.get("remaining") or data.get("remaining_tokens") or data.get("available")
        limit = data.get("limit") or data.get("total") or data.get("quota")
        used = data.get("used") or data.get("usage") or data.get("consumed")
        try:
            if remaining is not None and limit:
                return safe_percent(float(remaining) / float(limit))
            if used is not None and limit:
                return safe_percent(1 - (float(used) / float(limit)))
        except (TypeError, ValueError, ZeroDivisionError):
            return None
        return None

    def _looks_like_quota(self, data: dict[str, Any]) -> bool:
        keys = {str(key).lower() for key in data.keys()}
        return bool(keys & {"limit", "total", "remaining", "remainingtokens", "remaining_tokens", "remainingpercent", "remaining_percent", "available", "availablepercent", "available_percent", "used", "usage", "consumed", "resetat", "reset_at", "resettime", "percent", "usage_percentage"})

    def _quota_candidates(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        stack: list[Any] = [data]
        seen = 0
        while stack and seen < 200:
            seen += 1
            current = stack.pop()
            if isinstance(current, dict):
                if self._looks_like_quota(current):
                    candidates.append(current)
                for key, value in current.items():
                    normalized = str(key).lower()
                    if normalized in {"quotas", "quota", "model_quotas", "models", "availablemodels", "available_models", "ratelimits", "rate_limits", "limits"}:
                        if isinstance(value, dict):
                            for name, item in value.items():
                                if isinstance(item, dict):
                                    merged = dict(item)
                                    merged.setdefault("id", name)
                                    stack.append(merged)
                        elif isinstance(value, list):
                            stack.extend(item for item in value if isinstance(item, dict))
                    elif isinstance(value, (dict, list)):
                        stack.append(value)
            elif isinstance(current, list):
                stack.extend(item for item in current if isinstance(item, dict))
        unique: list[dict[str, Any]] = []
        signatures: set[str] = set()
        for item in candidates:
            signature = json.dumps(redact_obj(item), ensure_ascii=False, sort_keys=True, default=str)[:400]
            if signature not in signatures:
                signatures.add(signature)
                unique.append(item)
        return unique

    def _google_quota_body(self, auth: AuthFile) -> dict[str, Any]:
        project_id = self._find_project_id(auth)
        body: dict[str, Any] = {}
        if project_id:
            body["projectId"] = project_id
        return body

    def _find_project_id(self, auth: AuthFile) -> str:
        metadata = auth.raw.get("metadata", {}) if isinstance(auth.raw.get("metadata"), dict) else {}
        attributes = auth.raw.get("attributes", {}) if isinstance(auth.raw.get("attributes"), dict) else {}
        for container in (auth.raw, metadata, attributes):
            for key in ("project_id", "projectId", "project"):
                if container.get(key):
                    return str(container[key])
        match = re.search(r"gemini-[^-]+-(.+?)(?:\.json)?$", auth.name)
        return match.group(1) if match else ""

    def _account_error(self, auth: AuthFile, message: Any) -> QuotaAccount:
        item = QuotaItem(id="error", label="额度查询异常", percent=None, status="error", raw_message=sanitize_text(message))
        return QuotaAccount(id=auth.id, name=auth.name, display_name=auth.display_name, status="error", items=[item])

    def _error_report(self, message: str) -> QuotaReport:
        account = QuotaAccount(id="management", name="CLIProxyAPI", display_name="CLIProxyAPI", status="error", items=[QuotaItem(id="management", label="管理接口", percent=None, status="error", raw_message=message)])
        provider = QuotaProvider(name="CLIProxyAPI", type="management", accounts=[account])
        return QuotaReport(generated_at=now_text(), summary=build_summary([provider]), providers=[provider], message=message)

    def _canonical_provider(self, provider: str) -> str:
        value = (provider or "unknown").lower().replace("_", "-")
        if value in {"geminicli", "gemini-cli"}:
            return "gemini-cli"
        if value in {"openai", "codex-free", "codex-plus", "codex-pro", "codex-team"}:
            return "codex"
        if value in {"google", "google-oauth"}:
            return "gemini"
        if value in {"claude-code", "anthropic"}:
            return "claude"
        return value

    def _default_google_label(self, provider_type: str) -> str:
        return "Gemini CLI 额度" if provider_type == "gemini-cli" else "Google OAuth 额度"

    def _unwrap_api_call_response(self, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for key in ("body", "data", "response", "result", "output"):
            value = data.get(key)
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    return {"message": value}
            if isinstance(value, (dict, list)):
                return value
        return data
