from __future__ import annotations

try:
    from .models import QuotaReport
    from .utils import sanitize_text
except ImportError:
    from models import QuotaReport
    from utils import sanitize_text


STATUS_TEXT = {
    "ok": "正常",
    "warning": "警告",
    "critical": "危险",
    "unknown": "未知",
    "error": "错误",
}


def format_quota_report(report: QuotaReport, *, compact: bool = False, title: str = "CPA 额度看板") -> str:
    lines = [title, f"生成时间：{report.generated_at}"]
    summary = report.summary
    lines.append(
        "汇总："
        f"账号 {summary.get('total_accounts', 0)}，"
        f"正常 {summary.get('ok', 0)}，"
        f"警告 {summary.get('warning', 0)}，"
        f"危险 {summary.get('critical', 0)}，"
        f"错误 {summary.get('error', 0)}"
    )

    providers = report.providers
    if compact:
        providers = _compact_providers(report)
        if not providers:
            lines.append(report.message or "当前所有额度正常")
            return "\n".join(lines)

    if not providers:
        lines.append(_friendly_empty_message(report.message))
        return "\n".join(lines)

    for provider in providers:
        lines.append("")
        lines.append(f"[{sanitize_text(provider.name)}] {len(provider.accounts)} 个账号")
        if not provider.accounts:
            lines.append("- 暂无账号")
            continue
        for account in provider.accounts:
            status = STATUS_TEXT.get(account.status, account.status)
            lines.append(f"- {sanitize_text(account.display_name)}：{status}")
            items = account.items
            if compact:
                items = [item for item in items if item.status in {"warning", "critical", "unknown", "error"}]
            if not items:
                lines.append("  - 暂无可展示额度项")
                continue
            for item in items:
                percent = "--" if item.percent is None else f"{item.percent}%"
                item_status = STATUS_TEXT.get(item.status, item.status)
                reset = f"，刷新：{sanitize_text(item.reset_at)}" if item.reset_at else ""
                message = _format_item_message(item.status, item.raw_message)
                lines.append(f"  - {sanitize_text(item.label)}：{percent}，{item_status}{reset}{message}")
    return "\n".join(lines)


def format_alert_report(report: QuotaReport, changes: list[dict[str, object]]) -> str:
    lines = ["CPA 额度告警", f"生成时间：{report.generated_at}"]
    if not changes:
        lines.append("本次没有需要通知的状态变化")
        return "\n".join(lines)
    for change in changes:
        provider = sanitize_text(change.get("provider_name", "未知 provider"))
        account = sanitize_text(change.get("account_name", "未知账号"))
        item = change.get("item")
        label = "额度"
        percent = "--"
        status = sanitize_text(change.get("to", "unknown"))
        raw_message = ""
        if isinstance(item, dict):
            label = sanitize_text(item.get("label", label))
            raw_percent = item.get("percent")
            percent = "--" if raw_percent is None else f"{raw_percent}%"
            status = sanitize_text(item.get("status", status))
            raw_message = sanitize_text(item.get("raw_message", ""))
        message = _format_item_message(status, raw_message)
        lines.append(f"- {provider} / {account} / {label}: {change.get('from')} -> {change.get('to')}，剩余 {percent}{message}")
    return "\n".join(lines)


def _compact_providers(report: QuotaReport):
    try:
        from .models import QuotaAccount, QuotaProvider
    except ImportError:
        from models import QuotaAccount, QuotaProvider

    providers = []
    for provider in report.providers:
        accounts = []
        for account in provider.accounts:
            items = [item for item in account.items if item.status in {"warning", "critical", "unknown", "error"}]
            if items:
                accounts.append(QuotaAccount(id=account.id, name=account.name, display_name=account.display_name, status=account.status, items=items))
        if accounts:
            providers.append(QuotaProvider(name=provider.name, type=provider.type, accounts=accounts))
    return providers


def _friendly_empty_message(message: str) -> str:
    if not message:
        return "未发现可查询额度的账号。请检查 cpa_url、Management Key、auth-files 是否有 OAuth 账号。"
    return sanitize_text(message)


def _format_item_message(status: str, raw_message: str) -> str:
    if status not in {"unknown", "error"} or not raw_message:
        return ""
    return f"，原因：{sanitize_text(raw_message)[:120]}"
