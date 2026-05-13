from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


QuotaStatus = Literal["ok", "warning", "critical", "unknown", "error"]


@dataclass(slots=True)
class QuotaItem:
    id: str
    label: str
    percent: int | None
    reset_at: str = ""
    status: QuotaStatus = "unknown"
    raw_message: str = ""

    def state_key(self, provider_type: str, account_id: str) -> str:
        return f"{provider_type}:{account_id}:{self.id}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "percent": self.percent,
            "reset_at": self.reset_at,
            "status": self.status,
            "raw_message": self.raw_message,
        }


@dataclass(slots=True)
class QuotaAccount:
    id: str
    name: str
    display_name: str
    status: QuotaStatus = "unknown"
    items: list[QuotaItem] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "status": self.status,
            "items": [item.as_dict() for item in self.items],
        }


@dataclass(slots=True)
class QuotaProvider:
    name: str
    type: str
    accounts: list[QuotaAccount] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "accounts": [account.as_dict() for account in self.accounts],
        }


@dataclass(slots=True)
class QuotaReport:
    generated_at: str
    summary: dict[str, int]
    providers: list[QuotaProvider] = field(default_factory=list)
    message: str = ""

    @classmethod
    def empty(cls, message: str = "暂无可查询的 OAuth 账号") -> "QuotaReport":
        return cls(
            generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            summary={"total_accounts": 0, "ok": 0, "warning": 0, "critical": 0, "error": 0},
            providers=[],
            message=message,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "summary": self.summary,
            "providers": [provider.as_dict() for provider in self.providers],
            "message": self.message,
        }


@dataclass(slots=True)
class AuthFile:
    id: str
    auth_index: str
    name: str
    provider: str
    email: str = ""
    status: str = ""
    status_message: str = ""
    disabled: bool = False
    unavailable: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "AuthFile":
        name = str(data.get("name") or data.get("id") or "unknown")
        auth_index = str(data.get("auth_index") or data.get("auth-index") or data.get("id") or name)
        provider = str(data.get("provider") or data.get("type") or "unknown").lower()
        email = str(data.get("email") or data.get("account") or "")
        return cls(
            id=str(data.get("id") or auth_index or name),
            auth_index=auth_index,
            name=name,
            provider=provider,
            email=email,
            status=str(data.get("status") or ""),
            status_message=str(data.get("status_message") or data.get("status-message") or ""),
            disabled=bool(data.get("disabled", False)),
            unavailable=bool(data.get("unavailable", False)),
            raw=data,
        )

    @property
    def display_name(self) -> str:
        return self.email or self.name or self.id


def status_from_percent(percent: int | None, warning_percent: int, critical_percent: int) -> QuotaStatus:
    if percent is None:
        return "unknown"
    if percent <= critical_percent:
        return "critical"
    if percent <= warning_percent:
        return "warning"
    return "ok"


def account_status(items: list[QuotaItem]) -> QuotaStatus:
    order: dict[QuotaStatus, int] = {"error": 5, "critical": 4, "warning": 3, "unknown": 2, "ok": 1}
    if not items:
        return "unknown"
    return max((item.status for item in items), key=lambda status: order[status])


def build_summary(providers: list[QuotaProvider]) -> dict[str, int]:
    summary = {"total_accounts": 0, "ok": 0, "warning": 0, "critical": 0, "error": 0}
    for provider in providers:
        for account in provider.accounts:
            summary["total_accounts"] += 1
            if account.status in summary:
                summary[account.status] += 1
    return summary
