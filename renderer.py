from __future__ import annotations

import math
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

try:
    from .models import QuotaAccount, QuotaItem, QuotaProvider, QuotaReport, build_summary
    from .transforms import merge_gemini_series
    from .utils import cleanup_old_files, now_text, sanitize_text
except ImportError:
    from models import QuotaAccount, QuotaItem, QuotaProvider, QuotaReport, build_summary
    from transforms import merge_gemini_series
    from utils import cleanup_old_files, now_text, sanitize_text


STATUS_COLORS = {
    "ok": (18, 160, 107),
    "warning": (217, 119, 6),
    "critical": (225, 29, 72),
    "unknown": (100, 116, 139),
    "error": (190, 18, 60),
}

STATUS_LABELS = {
    "ok": "正常",
    "warning": "警告",
    "critical": "危险",
    "unknown": "未知",
    "error": "错误",
}

TEXT = (17, 24, 39)
MUTED = (78, 89, 108)
SUBTLE = (100, 116, 139)
BORDER = (203, 213, 225)
SURFACE = (255, 255, 255)
SURFACE_ALT = (248, 250, 252)
PAGE_BG = (244, 247, 251)
SHADOW = (224, 231, 240)


class QuotaCardRenderer:
    def __init__(self, data_dir: Path, *, high_resolution: bool = True, font_path: str = ""):
        self.cache_dir = data_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.scale = 2 if high_resolution else 1
        self.width = 560
        self.margin = 14
        self.configured_font_path = font_path.strip()
        self.fonts = self._load_fonts()

    def render_overview(self, report: QuotaReport) -> Path:
        report = merge_gemini_series(report)
        accounts = [(provider, account) for provider in report.providers for account in provider.accounts]
        height = self.margin * 2 + 88
        if accounts:
            height += sum(self._overview_account_height(account) + 12 for _, account in accounts)
        else:
            height += 96
        image, draw = self._new_image(height)

        y = self._draw_header(draw, "CPA 额度看板", report.generated_at, report.summary, f"账号 {len(accounts)}")
        if not accounts:
            self._empty_block(draw, y, "暂无额度数据", report.message or "未发现可查询额度的账号")
        else:
            for provider, account in accounts:
                self._overview_account_card(draw, provider, account, y)
                y += self._overview_account_height(account) + 12

        return self._save(image, "quota_overview")

    def render_overview_pages(self, report: QuotaReport, page_size: int = 3) -> list[Path]:
        pages = self._paginate_report_items(report, max(1, page_size))
        if not pages:
            return [self.render_empty(report.message or "暂无额度数据")]
        total_pages = len(pages)
        return [self._render_full(page_report, "overview", f"全量 {index}/{total_pages}") for index, page_report in enumerate(pages, 1)]

    def render_compact(self, report: QuotaReport) -> Path:
        return self.render_mini_card(report)

    def render_mini_card(self, report: QuotaReport) -> Path:
        report = merge_gemini_series(report)
        rows = self._abnormal_rows(report)
        total = report.summary.get("total_accounts", 0)
        shown = rows[:3]
        more = max(0, len(rows) - len(shown))
        row_h = 82
        height = self.margin * 2 + 88 + (len(shown) * row_h if shown else 96) + (34 if more else 0)
        image, draw = self._new_image(height)

        y = self._draw_header(draw, "CPA 额度简报", report.generated_at, report.summary, f"异常 {len(rows)}/{total}")
        if not shown:
            self._empty_block(draw, y, "当前没有低额度或异常账号", report.message or "所有已查询额度均处于正常状态")
        else:
            for provider, account, item in shown:
                self._compact_row(draw, provider, account, item, y)
                y += row_h
            if more:
                draw.text(
                    (self.margin * self.scale, (y + 8) * self.scale),
                    f"还有 {more} 项未显示，请使用 /额度 查看全量图",
                    font=self.fonts["small"],
                    fill=SUBTLE,
                )

        return self._save(image, "quota_compact")

    def render_alert(self, report: QuotaReport, changes: list[dict[str, Any]] | None = None) -> Path:
        filtered = self._report_from_changes(report, changes or []) if changes else self._filter_abnormal_report(report)
        return self.render_mini_card(filtered)

    def render_test_alert(self) -> Path:
        item = QuotaItem(id="test", label="测试额度项", percent=4, reset_at="05/13 18:25", status="critical", raw_message="测试通知")
        account = QuotaAccount(id="test", name="test@example.com", display_name="test@example.com", status="critical", items=[item])
        provider = QuotaProvider(name="Codex", type="codex", accounts=[account])
        report = QuotaReport(generated_at=now_text(), summary=build_summary([provider]), providers=[provider], message="这是一条测试告警。")
        return self.render_alert(report)

    def render_dashboard(self, report: QuotaReport) -> Path:
        filtered = self._filter_abnormal_report(report)
        if not filtered.providers:
            return self.render_empty(report.message or "当前所有额度正常，未发现异常账号")
        return self.render_mini_card(filtered)

    def render_detail_page(self, report: QuotaReport, page: int, total_pages: int) -> Path:
        return self._render_full(report, "detail", f"详情 {page}/{total_pages}")

    def render_detail_pages(self, report: QuotaReport, page_size: int = 5) -> list[Path]:
        page_size = max(1, min(page_size, 20))
        pages = self._paginate_report(report, page_size)
        if not pages:
            return [self.render_empty("暂无额度数据")]
        total_pages = len(pages)
        return [self.render_detail_page(page_report, index, total_pages) for index, page_report in enumerate(pages, 1)]

    def render_empty(self, message: str) -> Path:
        report = QuotaReport(generated_at=now_text(), summary={}, providers=[], message=message)
        image, draw = self._new_image(230)
        y = self._draw_header(draw, "CPA 额度看板", report.generated_at, report.summary, "提示")
        self._empty_block(draw, y, "暂无额度数据", message)
        return self._save(image, "quota_empty")

    def _render_full(self, report: QuotaReport, kind: str, mode: str) -> Path:
        cleanup_old_files(self.cache_dir)
        height = self._measure_full_height(report)
        image, draw = self._new_image(height)
        y = self._draw_header(draw, "CPA 额度看板", report.generated_at, report.summary, mode)

        if not report.providers:
            self._empty_block(draw, y, "暂无额度数据", report.message or "未发现可查询额度的账号")
        else:
            for provider in report.providers:
                y = self._provider_block(draw, provider, y)

        self._footer(draw, height - 32)
        return self._save(image, f"quota_{kind}")

    def _draw_header(
        self,
        draw: ImageDraw.ImageDraw,
        title: str,
        generated_at: str,
        summary: dict[str, int],
        mode: str,
    ) -> int:
        s = self.scale
        x = self.margin * s
        y = self.margin * s
        draw.text((x, y), title, font=self.fonts["title"], fill=TEXT)
        mode_w = self._text_width(draw, mode, self.fonts["value"]) + 24 * s
        self._round(
            draw,
            (self.width * s - self.margin * s - mode_w, y + 3 * s, self.width * s - self.margin * s, y + 34 * s),
            16 * s,
            (226, 232, 240),
            None,
        )
        draw.text((self.width * s - self.margin * s - mode_w + 12 * s, y + 7 * s), mode, font=self.fonts["value"], fill=MUTED)
        summary_text = (
            f"{generated_at}  "
            f"账号 {summary.get('total_accounts', 0)}  "
            f"正常 {summary.get('ok', 0)}  "
            f"警告 {summary.get('warning', 0)}  "
            f"危险 {summary.get('critical', 0)}  "
            f"错误 {summary.get('error', 0)}"
        )
        draw.text((x, y + 46 * s), self._ellipsis(draw, summary_text, self.fonts["small"], (self.width - self.margin * 2) * s), font=self.fonts["small"], fill=MUTED)
        return self.margin + 86

    def _provider_block(self, draw: ImageDraw.ImageDraw, provider: QuotaProvider, y: int) -> int:
        s = self.scale
        x = self.margin * s
        w = (self.width - self.margin * 2) * s
        title = f"{provider.name} · {len(provider.accounts)} 个账号"
        draw.text((x, y * s), title, font=self.fonts["section"], fill=TEXT)
        draw.line((x, (y + 34) * s, x + w, (y + 34) * s), fill=BORDER, width=max(1, s))
        y += 44

        if not provider.accounts:
            self._empty_block(draw, y, "暂无账号", "")
            return y + 104

        for account in provider.accounts:
            y = self._account_card(draw, provider, account, y)
        return y + 14

    def _account_card(self, draw: ImageDraw.ImageDraw, provider: QuotaProvider, account: QuotaAccount, y: int) -> int:
        s = self.scale
        x = self.margin * s
        w = (self.width - self.margin * 2) * s
        items = account.items or [QuotaItem(id="empty", label="暂无可展示额度项", percent=None, status="unknown", raw_message="未解析到额度数据")]
        h = self._account_height(account)
        color = STATUS_COLORS.get(account.status, STATUS_COLORS["unknown"])

        self._card(draw, (x, y * s, x + w, (y + h) * s), 14 * s)
        self._round(draw, (x, y * s, x + 7 * s, (y + h) * s), 4 * s, color, None)

        name = self._ellipsis(draw, account.display_name, self.fonts["account"], w - 156 * s)
        draw.text((x + 18 * s, (y + 14) * s), name, font=self.fonts["account"], fill=TEXT)
        label = STATUS_LABELS.get(account.status, account.status)
        self._pill(draw, label, color, x + w - self._pill_width(label) - 14 * s, (y + 13) * s)
        y += 58

        for item in items:
            self._item_row(draw, item, x + 18 * s, y * s, w - 36 * s)
            y += self._item_height(item)
        return y + 14

    def _overview_account_card(self, draw: ImageDraw.ImageDraw, provider: QuotaProvider, account: QuotaAccount, y: int) -> None:
        s = self.scale
        x = self.margin * s
        w = (self.width - self.margin * 2) * s
        h = self._overview_account_height(account)
        color = STATUS_COLORS.get(account.status, STATUS_COLORS["unknown"])
        self._card(draw, (x, y * s, x + w, (y + h) * s), 12 * s)

        tag = self._provider_tag(provider)
        tag_w = self._text_width(draw, tag, self.fonts["tag"]) + 22 * s
        tag_color = self._provider_color(provider)
        tag_top = (y + 15) * s
        self._round(draw, (x + 14 * s, tag_top, x + 14 * s + tag_w, tag_top + 28 * s), 14 * s, self._soft(tag_color), None)
        draw.text((x + 25 * s, tag_top + 4 * s), tag, font=self.fonts["tag"], fill=tag_color)

        name_x = x + 24 * s + tag_w
        name = self._ellipsis(draw, account.name or account.display_name, self.fonts["account"], w - tag_w - 44 * s)
        draw.text((name_x, (y + 13) * s), name, font=self.fonts["account"], fill=TEXT)

        line_y = (y + 55) * s
        for dash_x in range(x + 14 * s, x + w - 14 * s, 8 * s):
            draw.line((dash_x, line_y, min(dash_x + 4 * s, x + w - 14 * s), line_y), fill=(226, 232, 240), width=max(1, s))
        item_y = y + 68
        for item in account.items or [QuotaItem(id="empty", label="暂无可展示额度项", percent=None, status="unknown", raw_message="未解析到额度数据")]:
            self._overview_item_row(draw, item, x + 16 * s, item_y * s, w - 32 * s)
            item_y += 46

    def _overview_item_row(self, draw: ImageDraw.ImageDraw, item: QuotaItem, x: int, y: int, w: int) -> None:
        s = self.scale
        color = STATUS_COLORS.get(item.status, STATUS_COLORS["unknown"])
        percent = "--" if item.percent is None else f"{item.percent}%"
        reset = sanitize_text(item.reset_at) if item.reset_at else ""
        reset_w = self._text_width(draw, reset, self.fonts["small"]) if reset else 0
        percent_w = self._text_width(draw, percent, self.fonts["value"])
        right_w = percent_w + (10 * s + reset_w if reset else 0)
        label = self._overview_item_label(item)
        draw.text((x, y), self._ellipsis(draw, label, self.fonts["body"], w - right_w - 16 * s), font=self.fonts["body"], fill=TEXT)
        percent_x = x + w - right_w
        draw.text((percent_x, y + 1 * s), percent, font=self.fonts["value"], fill=color)
        if reset:
            draw.text((percent_x + percent_w + 10 * s, y + 3 * s), reset, font=self.fonts["small"], fill=MUTED)

        bar_y = y + 29 * s
        self._round(draw, (x, bar_y, x + w, bar_y + 7 * s), 4 * s, (232, 236, 243), None)
        if item.percent is not None:
            fill_w = max(6 * s, math.floor(w * item.percent / 100))
            self._round(draw, (x, bar_y, x + fill_w, bar_y + 7 * s), 4 * s, color, None)

    def _overview_account_height(self, account: QuotaAccount) -> int:
        items = account.items or [QuotaItem(id="empty", label="暂无可展示额度项", percent=None, status="unknown", raw_message="未解析到额度数据")]
        return 70 + len(items) * 46 + 12

    def _provider_tag(self, provider: QuotaProvider) -> str:
        value = provider.type.lower()
        if "gemini" in value:
            return "GeminiCLI"
        if "codex" in value:
            return "Codex"
        return provider.name

    def _provider_color(self, provider: QuotaProvider) -> tuple[int, int, int]:
        value = provider.type.lower()
        if "gemini" in value:
            return (37, 99, 235)
        if "codex" in value:
            return (99, 102, 241)
        return (71, 85, 105)

    def _overview_item_label(self, item: QuotaItem) -> str:
        mapping = {
            "Codex 5h": "5 小时限额",
            "Codex 7d": "周限额",
            "Codex 5H": "5 小时限额",
            "Codex 7D": "周限额",
        }
        return mapping.get(item.label, item.label)

    def _item_row(self, draw: ImageDraw.ImageDraw, item: QuotaItem, x: int, y: int, w: int) -> None:
        s = self.scale
        color = STATUS_COLORS.get(item.status, STATUS_COLORS["unknown"])
        percent = "--" if item.percent is None else f"{item.percent}%"
        label_max = w - 118 * s
        label = self._ellipsis(draw, item.label, self.fonts["body"], label_max)
        draw.text((x, y), label, font=self.fonts["body"], fill=TEXT)
        percent_w = self._text_width(draw, percent, self.fonts["body"])
        draw.text((x + w - percent_w, y), percent, font=self.fonts["body"], fill=color)

        bar_y = y + 34 * s
        self._round(draw, (x, bar_y, x + w, bar_y + 9 * s), 5 * s, (226, 232, 240), None)
        if item.percent is not None:
            fill_w = max(6 * s, math.floor(w * item.percent / 100))
            self._round(draw, (x, bar_y, x + fill_w, bar_y + 9 * s), 5 * s, color, None)

        meta = self._item_meta(item)
        if meta:
            draw.text((x, y + 49 * s), self._ellipsis(draw, meta, self.fonts["small"], w), font=self.fonts["small"], fill=SUBTLE)

    def _compact_row(self, draw: ImageDraw.ImageDraw, provider: QuotaProvider, account: QuotaAccount, item: QuotaItem, y: int) -> None:
        s = self.scale
        x = self.margin * s
        w = (self.width - self.margin * 2) * s
        color = STATUS_COLORS.get(item.status, STATUS_COLORS["unknown"])
        self._card(draw, (x, y * s, x + w, (y + 68) * s), 14 * s)
        self._round(draw, (x, y * s, x + 7 * s, (y + 68) * s), 4 * s, color, None)

        left = f"{provider.name} / {account.display_name}"
        right = f"{item.label}  {'--' if item.percent is None else str(item.percent) + '%'}"
        right_w = self._text_width(draw, right, self.fonts["body"])
        draw.text((x + 18 * s, (y + 11) * s), self._ellipsis(draw, left, self.fonts["body"], w - right_w - 48 * s), font=self.fonts["body"], fill=TEXT)
        draw.text((x + w - right_w - 16 * s, (y + 11) * s), right, font=self.fonts["body"], fill=color)

        bar_x = x + 18 * s
        bar_y = (y + 48) * s
        bar_w = w - 36 * s
        self._round(draw, (bar_x, bar_y, bar_x + bar_w, bar_y + 9 * s), 5 * s, (226, 232, 240), None)
        if item.percent is not None:
            self._round(draw, (bar_x, bar_y, bar_x + max(7 * s, math.floor(bar_w * item.percent / 100)), bar_y + 9 * s), 5 * s, color, None)

    def _empty_block(self, draw: ImageDraw.ImageDraw, y: int, title: str, message: str) -> None:
        s = self.scale
        x = self.margin * s
        w = (self.width - self.margin * 2) * s
        self._card(draw, (x, y * s, x + w, (y + 88) * s), 14 * s)
        draw.text((x + 18 * s, (y + 16) * s), title, font=self.fonts["section"], fill=TEXT)
        if message:
            draw.text((x + 18 * s, (y + 52) * s), self._ellipsis(draw, sanitize_text(message), self.fonts["body"], w - 36 * s), font=self.fonts["body"], fill=MUTED)

    def _footer(self, draw: ImageDraw.ImageDraw, y: int) -> None:
        draw.text((self.margin * self.scale, y * self.scale), "CPA Quota Board · CLIProxyAPI Management API", font=self.fonts["small"], fill=SUBTLE)

    def _measure_full_height(self, report: QuotaReport) -> int:
        height = self.margin + 86 + 14 + 32
        if not report.providers:
            return max(230, height + 104)
        for provider in report.providers:
            height += 44
            if provider.accounts:
                height += sum(self._account_height(account) + 18 for account in provider.accounts)
            else:
                height += 104
            height += 12
        return max(240, height)

    def _account_height(self, account: QuotaAccount) -> int:
        items = account.items or [QuotaItem(id="empty", label="暂无可展示额度项", percent=None, status="unknown", raw_message="未解析到额度数据")]
        return 58 + sum(self._item_height(item) for item in items) + 14

    def _item_height(self, item: QuotaItem) -> int:
        return 74 if self._item_meta(item) else 58

    def _item_meta(self, item: QuotaItem) -> str:
        parts = []
        if item.reset_at:
            parts.append(f"刷新 {sanitize_text(item.reset_at)}")
        if item.raw_message and item.status in {"unknown", "error"}:
            parts.append(sanitize_text(item.raw_message))
        return " · ".join(parts)

    def _abnormal_rows(self, report: QuotaReport) -> list[tuple[QuotaProvider, QuotaAccount, QuotaItem]]:
        rows = []
        severity_order = {"error": 0, "critical": 1, "warning": 2, "unknown": 3, "ok": 4}
        for provider in report.providers:
            for account in provider.accounts:
                items = [item for item in account.items if item.status in {"error", "critical", "warning", "unknown"}]
                if not items and account.status in {"error", "critical", "warning", "unknown"}:
                    items = [QuotaItem(id="account", label="账号状态", percent=None, status=account.status)]
                for item in items:
                    rows.append((provider, account, item))
        rows.sort(key=lambda row: (severity_order.get(row[2].status, 5), row[0].name, row[1].display_name, row[2].label))
        return rows

    def _all_rows(self, report: QuotaReport) -> list[tuple[QuotaProvider, QuotaAccount, QuotaItem]]:
        rows = []
        severity_order = {"error": 0, "critical": 1, "warning": 2, "unknown": 3, "ok": 4}
        for provider in report.providers:
            for account in provider.accounts:
                items = account.items or [QuotaItem(id="account", label="账号状态", percent=None, status=account.status)]
                for item in items:
                    rows.append((provider, account, item))
        rows.sort(key=lambda row: (severity_order.get(row[2].status, 5), row[0].name, row[1].display_name, row[2].label))
        return rows

    def _filter_abnormal_report(self, report: QuotaReport) -> QuotaReport:
        providers: list[QuotaProvider] = []
        for provider in report.providers:
            accounts = []
            for account in provider.accounts:
                items = [item for item in account.items if item.status in {"warning", "critical", "unknown", "error"}]
                if items or account.status in {"warning", "critical", "unknown", "error"}:
                    accounts.append(QuotaAccount(id=account.id, name=account.name, display_name=account.display_name, status=account.status, items=items or account.items))
            if accounts:
                providers.append(QuotaProvider(name=provider.name, type=provider.type, accounts=accounts))
        return QuotaReport(generated_at=report.generated_at, summary=report.summary, providers=providers, message=report.message)

    def _paginate_report(self, report: QuotaReport, page_size: int) -> list[QuotaReport]:
        flat_accounts = [(provider, account) for provider in report.providers for account in provider.accounts]
        if not flat_accounts:
            return []

        pages = []
        for i in range(0, len(flat_accounts), page_size):
            provider_map: dict[tuple[str, str], QuotaProvider] = {}
            for provider, account in flat_accounts[i : i + page_size]:
                key = (provider.name, provider.type)
                provider_map.setdefault(key, QuotaProvider(name=provider.name, type=provider.type, accounts=[])).accounts.append(account)
            pages.append(QuotaReport(generated_at=report.generated_at, summary=report.summary, providers=list(provider_map.values()), message=report.message))
        return pages

    def _paginate_report_items(self, report: QuotaReport, page_size: int) -> list[QuotaReport]:
        pages: list[QuotaReport] = []
        for provider in report.providers:
            for account in provider.accounts:
                items = account.items or [QuotaItem(id="empty", label="暂无可展示额度项", percent=None, status="unknown", raw_message="未解析到额度数据")]
                for index in range(0, len(items), page_size):
                    page_account = QuotaAccount(
                        id=account.id,
                        name=account.name,
                        display_name=account.display_name,
                        status=account.status,
                        items=items[index : index + page_size],
                    )
                    page_provider = QuotaProvider(name=provider.name, type=provider.type, accounts=[page_account])
                    pages.append(
                        QuotaReport(
                            generated_at=report.generated_at,
                            summary=report.summary,
                            providers=[page_provider],
                            message=report.message,
                        )
                    )
        return pages

    def _report_from_changes(self, report: QuotaReport, changes: list[dict[str, Any]]) -> QuotaReport:
        keys = {change.get("key") for change in changes}
        providers: list[QuotaProvider] = []
        for provider in report.providers:
            accounts: list[QuotaAccount] = []
            for account in provider.accounts:
                items = [item for item in account.items if item.state_key(provider.type, account.id) in keys]
                if items:
                    accounts.append(QuotaAccount(id=account.id, name=account.name, display_name=account.display_name, status=account.status, items=items))
            if accounts:
                providers.append(QuotaProvider(name=provider.name, type=provider.type, accounts=accounts))
        return QuotaReport(generated_at=report.generated_at, summary=build_summary(providers), providers=providers, message="本次没有需要通知的状态变化")

    def _new_image(self, height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
        image = Image.new("RGB", (self.width * self.scale, height * self.scale), PAGE_BG)
        draw = ImageDraw.Draw(image)
        for y in range(image.height):
            ratio = y / max(1, image.height - 1)
            color = (
                int(248 - 8 * ratio),
                int(250 - 7 * ratio),
                int(252 - 3 * ratio),
            )
            draw.line((0, y, image.width, y), fill=color)
        return image, draw

    def _save(self, image: Image.Image, prefix: str) -> Path:
        if self.scale != 1:
            image = image.resize((self.width, image.height // self.scale), Image.Resampling.LANCZOS)
        output = self.cache_dir / f"{prefix}_{uuid.uuid4().hex}.jpg"
        image.save(output, "JPEG", quality=95, optimize=True)
        return output

    def _pill(self, draw: ImageDraw.ImageDraw, text: str, color: tuple[int, int, int], x: int, y: int) -> None:
        s = self.scale
        w = self._pill_width(text)
        self._round(draw, (x, y, x + w, y + 38 * s), 19 * s, self._soft(color), None)
        draw.text((x + 16 * s, y + 7 * s), text, font=self.fonts["body"], fill=color)

    def _pill_width(self, text: str) -> int:
        return max(78 * self.scale, (len(text) * 18 + 36) * self.scale)

    def _round(self, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill: tuple[int, int, int], outline: tuple[int, int, int] | None) -> None:
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=max(1, self.scale) if outline else 0)

    def _card(self, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int) -> None:
        s = self.scale
        left, top, right, bottom = box
        self._round(draw, (left, top + 2 * s, right, bottom + 2 * s), radius, SHADOW, None)
        self._round(draw, box, radius, SURFACE, BORDER)

    def _soft(self, color: tuple[int, int, int]) -> tuple[int, int, int]:
        return tuple(int(channel + (255 - channel) * 0.88) for channel in color)

    def _ellipsis(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int) -> str:
        clean = sanitize_text(text)
        if self._text_width(draw, clean, font) <= max_width:
            return clean
        suffix = "..."
        while clean and self._text_width(draw, clean + suffix, font) > max_width:
            clean = clean[:-1]
        return (clean or text[:1]) + suffix

    def _text_width(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> int:
        box = draw.textbbox((0, 0), text, font=font)
        return int(box[2] - box[0])

    def _load_fonts(self) -> dict[str, ImageFont.FreeTypeFont | ImageFont.ImageFont]:
        font_path = self._find_font()

        def load(size: int, weight: int = 400) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
            if font_path:
                font = ImageFont.truetype(str(font_path), size * self.scale)
                self._set_font_weight(font, weight)
                return font
            return ImageFont.load_default()

        return {
            "title": load(30, 620),
            "section": load(22, 560),
            "account": load(22, 520),
            "body": load(21, 460),
            "value": load(20, 500),
            "small": load(16, 400),
            "tag": load(15, 520),
        }

    def _set_font_weight(self, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, weight: int) -> None:
        try:
            axes = font.get_variation_axes()
            if axes:
                font.set_variation_by_axes([weight])
        except Exception:
            return

    def _find_font(self) -> Path | None:
        configured = self.configured_font_path or os.environ.get("CPA_QUOTA_FONT_PATH", "")
        if configured:
            path = Path(configured).expanduser()
            if path.exists() and path.is_file():
                return path
        packaged = self._font_from_justmytype()
        if packaged:
            return packaged

        candidates = [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/msyh.ttf",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/Deng.ttf",
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.otf",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otc",
            "/usr/share/fonts/opentype/noto/NotoSansCJKsc-VF.otf",
            "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf",
            "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf",
            "/usr/share/fonts/opentype/source-han-sans/SourceHanSansSC-Regular.otf",
            "/usr/share/fonts/adobe-source-han-sans/SourceHanSansSC-Regular.otf",
            "/usr/share/fonts/truetype/arphic/uming.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        for candidate in candidates:
            path = Path(candidate)
            if path.exists():
                return path
        fontconfig = self._font_from_fontconfig()
        if fontconfig:
            return fontconfig
        return self._scan_system_fonts()

    def _font_from_justmytype(self) -> Path | None:
        try:
            from justmytype import FontRegistry
        except Exception:
            return None
        try:
            registry = FontRegistry()
            for family in ("Noto Sans SC", "Noto Sans CJK SC", "Noto Sans"):
                font = registry.find_font(family=family, weight=400)
                if font and getattr(font, "path", None):
                    path = Path(str(font.path))
                    if path.exists() and path.is_file():
                        return path
        except Exception:
            return None
        return None

    def _font_from_fontconfig(self) -> Path | None:
        try:
            result = subprocess.run(
                ["fc-match", "-f", "%{file}", "Noto Sans CJK SC,Microsoft YaHei,SimHei,WenQuanYi Micro Hei,DejaVu Sans"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        path = Path(result.stdout.strip())
        return path if path.exists() and path.is_file() else None

    def _scan_system_fonts(self) -> Path | None:
        roots = [
            Path("/usr/share/fonts"),
            Path("/usr/local/share/fonts"),
            Path("/System/Library/Fonts"),
            Path("/Library/Fonts"),
        ]
        preferred = (
            "noto",
            "sourcehan",
            "source-han",
            "wqy",
            "wenquanyi",
            "pingfang",
            "simhei",
            "msyh",
            "dejavu",
        )
        matches: list[Path] = []
        for root in roots:
            if not root.exists():
                continue
            for suffix in ("*.ttf", "*.ttc", "*.otf", "*.otc"):
                matches.extend(root.rglob(suffix))
        for key in preferred:
            for path in matches:
                if key in path.name.lower() or key in str(path.parent).lower():
                    return path
        if matches:
            return matches[0]
        return None
