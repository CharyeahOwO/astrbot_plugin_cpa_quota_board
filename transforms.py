from __future__ import annotations

try:
    from .models import QuotaAccount, QuotaItem, QuotaProvider, QuotaReport
    from .utils import sanitize_text
except ImportError:
    from models import QuotaAccount, QuotaItem, QuotaProvider, QuotaReport
    from utils import sanitize_text


GEMINI_SERIES = [
    ("gemini-flash", "Gemini Flash 系列", ("gemini-3-flash-preview", "gemini-2.5-flash")),
    ("gemini-pro", "Gemini Pro 系列", ("gemini-3-pro-preview", "gemini-2.5-pro", "gemini-3.1-pro-preview")),
    (
        "gemini-flash-lite",
        "Gemini Flash Lite 系列",
        ("gemini-2.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.1-flash-lite-preview"),
    ),
]


def merge_gemini_series(report: QuotaReport) -> QuotaReport:
    providers: list[QuotaProvider] = []
    changed = False
    for provider in report.providers:
        if "gemini" not in provider.type.lower() and "gemini" not in provider.name.lower():
            providers.append(provider)
            continue
        accounts: list[QuotaAccount] = []
        for account in provider.accounts:
            items = merge_gemini_items(account.items)
            changed = changed or len(items) != len(account.items)
            accounts.append(
                QuotaAccount(
                    id=account.id,
                    name=account.name,
                    display_name=account.display_name,
                    status=account.status,
                    items=items,
                )
            )
        providers.append(QuotaProvider(name=provider.name, type=provider.type, accounts=accounts))
    if not changed:
        return report
    return QuotaReport(generated_at=report.generated_at, summary=report.summary, providers=providers, message=report.message)


def merge_gemini_items(items: list[QuotaItem]) -> list[QuotaItem]:
    buckets: dict[str, list[QuotaItem]] = {key: [] for key, _, _ in GEMINI_SERIES}
    consumed: set[int] = set()
    for index, item in enumerate(items):
        normalized = normalize_model_label(item.label)
        for key, _, models in GEMINI_SERIES:
            if normalized in models:
                buckets[key].append(item)
                consumed.add(index)
                break

    merged: list[QuotaItem] = []
    first_index: dict[str, int] = {}
    for key, label, _ in GEMINI_SERIES:
        group = buckets[key]
        if not group:
            continue
        group_ids = {id(item) for item in group}
        first_index[key] = min(index for index, item in enumerate(items) if id(item) in group_ids)
        merged.append(series_item(key, label, group))

    merged_by_index: list[tuple[int, QuotaItem]] = []
    for index, item in enumerate(items):
        if index not in consumed:
            merged_by_index.append((index, item))
    for item in merged:
        merged_by_index.append((first_index.get(item.id, len(items)), item))
    merged_by_index.sort(key=lambda pair: pair[0])
    return [item for _, item in merged_by_index]


def series_item(item_id: str, label: str, items: list[QuotaItem]) -> QuotaItem:
    source = next((item for item in items if item.percent is not None), items[0])
    return QuotaItem(
        id=item_id,
        label=label,
        percent=source.percent,
        reset_at=source.reset_at,
        status=source.status,
        raw_message=source.raw_message,
    )


def normalize_model_label(label: str) -> str:
    value = sanitize_text(label).strip().lower().replace("_", "-")
    return value.removeprefix("models/")
