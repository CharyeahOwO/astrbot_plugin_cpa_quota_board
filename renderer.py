from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

try:
    from .models import QuotaAccount, QuotaItem, QuotaProvider, QuotaReport, build_summary
    from .utils import cleanup_old_files, now_text, sanitize_text
except ImportError:
    from models import QuotaAccount, QuotaItem, QuotaProvider, QuotaReport, build_summary
    from utils import cleanup_old_files, now_text, sanitize_text


STATUS_COLORS = {
    "ok": (65, 214, 151),
    "warning": (245, 171, 61),
    "critical": (255, 92, 92),
    "unknown": (148, 163, 184),
    "error": (232, 94, 143),
}

STATUS_LABELS = {
    "ok": "正常",
    "warning": "警告",
    "critical": "危险",
    "unknown": "未知",
    "error": "错误",
}


class QuotaCardRenderer:
    def __init__(self, data_dir: Path, *, high_resolution: bool = True):
        self.cache_dir = data_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.scale = 2 if high_resolution else 1
        self.width = 740
        self.margin = 24
        self.fonts = self._load_fonts()

    def render_overview(self, report: QuotaReport) -> Path:
        return self._render(report, "overview", "全量模式")

    def render_compact(self, report: QuotaReport) -> Path:
        filtered = self._filter_report(report)
        return self._render(filtered, "compact", "简洁模式")

    def render_alert(self, report: QuotaReport, changes: list[dict[str, Any]] | None = None) -> Path:
        filtered = self._report_from_changes(report, changes or []) if changes else self._filter_report(report)
        return self._render(filtered, "alert", "额度告警")

    def render_test_alert(self) -> Path:
        item = QuotaItem(id="test", label="测试额度项", percent=4, reset_at="05/13 18:25", status="critical", raw_message="测试通知")
        account = QuotaAccount(id="test", name="test@example.com", display_name="test@example.com", status="critical", items=[item])
        provider = QuotaProvider(name="Codex", type="codex", accounts=[account])
        report = QuotaReport(generated_at=now_text(), summary=build_summary([provider]), providers=[provider], message="这是一条测试告警。")
        return self.render_alert(report)

    def _render(self, report: QuotaReport, kind: str, mode_text: str) -> Path:
        cleanup_old_files(self.cache_dir)
        height = self._measure_height(report)
        scale = self.scale
        image = Image.new("RGB", (self.width * scale, height * scale), (13, 18, 30))
        draw = ImageDraw.Draw(image)
        self._draw_background(draw, image.size)

        y = self.margin
        y = self._header(draw, report, mode_text, y, scale)
        y = self._summary(draw, report, y, scale)

        if not report.providers:
            y = self._empty_state(draw, report.message or "未发现可查询额度的账号", y, scale)
        else:
            for provider in report.providers:
                y = self._provider(draw, provider, y, scale)

        self._footer(draw, max(y + 4, height - 34), scale)
        if scale != 1:
            image = image.resize((self.width, height), Image.Resampling.LANCZOS)
        output = self.cache_dir / f"quota_{kind}_{uuid.uuid4().hex}.png"
        image.save(output, "PNG")
        return output

    def _header(self, draw: ImageDraw.ImageDraw, report: QuotaReport, mode_text: str, y: int, scale: int) -> int:
        x = self.margin * scale
        draw.text((x, y * scale), "CPA 额度看板", font=self.fonts["title"], fill=(245, 248, 255))
        self._tag(draw, mode_text, STATUS_COLORS["unknown"], x + 202 * scale, y * scale + 3 * scale, scale)
        draw.text((x, y * scale + 38 * scale), f"生成时间 {report.generated_at}", font=self.fonts["small"], fill=(148, 163, 184))
        return y + 66

    def _summary(self, draw: ImageDraw.ImageDraw, report: QuotaReport, y: int, scale: int) -> int:
        x = self.margin * scale
        gap = 8 * scale
        width = (self.width - self.margin * 2 - 8 * 4) // 5
        items = [
            ("总账号", report.summary.get("total_accounts", 0), (96, 165, 250)),
            ("正常", report.summary.get("ok", 0), STATUS_COLORS["ok"]),
            ("警告", report.summary.get("warning", 0), STATUS_COLORS["warning"]),
            ("危险", report.summary.get("critical", 0), STATUS_COLORS["critical"]),
            ("错误", report.summary.get("error", 0), STATUS_COLORS["error"]),
        ]
        for index, (label, value, color) in enumerate(items):
            left = x + index * ((width * scale) + gap)
            self._rounded(draw, (left, y * scale, left + width * scale, y * scale + 44 * scale), 12 * scale, (23, 31, 47), (42, 54, 76))
            draw.text((left + 12 * scale, y * scale + 7 * scale), str(value), font=self.fonts["metric"], fill=color)
            draw.text((left + 50 * scale, y * scale + 14 * scale), label, font=self.fonts["small"], fill=(198, 208, 222))
        return y + 58

    def _provider(self, draw: ImageDraw.ImageDraw, provider: QuotaProvider, y: int, scale: int) -> int:
        x = self.margin * scale
        w = (self.width - self.margin * 2) * scale
        h = self._provider_height(provider) * scale
        self._rounded(draw, (x, y * scale, x + w, y * scale + h), 16 * scale, (19, 27, 42), (43, 55, 76))
        title = f"{provider.name} · {len(provider.accounts)} 个账号"
        draw.text((x + 16 * scale, y * scale + 13 * scale), self._fit(title, 36), font=self.fonts["section"], fill=(232, 238, 248))
        y += 44
        if not provider.accounts:
            draw.text((x + 16 * scale, y * scale), "暂无账号", font=self.fonts["body"], fill=(148, 163, 184))
            return y + 34
        for account in provider.accounts:
            y = self._account(draw, account, y, scale)
        return y + 12

    def _account(self, draw: ImageDraw.ImageDraw, account: QuotaAccount, y: int, scale: int) -> int:
        x = (self.margin + 12) * scale
        w = (self.width - self.margin * 2 - 24) * scale
        h = self._account_height(account) * scale
        self._rounded(draw, (x, y * scale, x + w, y * scale + h), 12 * scale, (24, 34, 52), (49, 63, 86))
        name = self._fit(account.display_name, 42)
        draw.text((x + 14 * scale, y * scale + 10 * scale), name, font=self.fonts["account"], fill=(245, 248, 255))
        tag_w = self._tag_width(STATUS_LABELS.get(account.status, account.status), scale)
        self._tag(draw, STATUS_LABELS.get(account.status, account.status), STATUS_COLORS.get(account.status, STATUS_COLORS["unknown"]), x + w - tag_w - 14 * scale, y * scale + 9 * scale, scale)
        y += 42
        items = account.items or [QuotaItem(id="empty", label="暂无可展示额度项", percent=None, status="unknown", raw_message="未解析到额度数据")]
        for item in items:
            y = self._quota_item(draw, item, y, scale)
        return y + 10

    def _quota_item(self, draw: ImageDraw.ImageDraw, item: QuotaItem, y: int, scale: int) -> int:
        x = (self.margin + 26) * scale
        w = (self.width - self.margin * 2 - 52) * scale
        color = STATUS_COLORS.get(item.status, STATUS_COLORS["unknown"])
        label = self._fit(item.label, 34)
        percent = "--" if item.percent is None else f"{item.percent}%"
        draw.text((x, y * scale), label, font=self.fonts["body"], fill=(224, 231, 241))
        percent_w = self._text_width(draw, percent, self.fonts["body"])
        draw.text((x + w - percent_w, y * scale), percent, font=self.fonts["body"], fill=color)

        bar_y = y * scale + 25 * scale
        self._rounded(draw, (x, bar_y, x + w, bar_y + 8 * scale), 4 * scale, (48, 58, 76), None)
        if item.percent is not None:
            fill_w = max(4 * scale, math.floor(w * item.percent / 100))
            self._rounded(draw, (x, bar_y, x + fill_w, bar_y + 8 * scale), 4 * scale, color, None)

        meta_parts = []
        if item.reset_at:
            meta_parts.append(f"刷新 {item.reset_at}")
        elif item.status in {"unknown", "error"}:
            meta_parts.append("刷新时间未知")
        if item.raw_message and item.status in {"unknown", "error"}:
            meta_parts.append(sanitize_text(item.raw_message))
        if meta_parts:
            meta = self._fit(" · ".join(meta_parts), 58)
            draw.text((x, y * scale + 39 * scale), meta, font=self.fonts["tiny"], fill=(148, 163, 184))
            return y + 62
        return y + 48

    def _empty_state(self, draw: ImageDraw.ImageDraw, message: str, y: int, scale: int) -> int:
        x = self.margin * scale
        w = (self.width - self.margin * 2) * scale
        self._rounded(draw, (x, y * scale, x + w, y * scale + 96 * scale), 16 * scale, (24, 34, 52), (62, 74, 98))
        draw.text((x + 18 * scale, y * scale + 24 * scale), "暂无额度数据", font=self.fonts["section"], fill=(232, 238, 248))
        draw.text((x + 18 * scale, y * scale + 58 * scale), self._fit(sanitize_text(message), 58), font=self.fonts["body"], fill=(169, 181, 199))
        return y + 110

    def _footer(self, draw: ImageDraw.ImageDraw, y: int, scale: int) -> None:
        x = self.margin * scale
        draw.text((x, y * scale), "CPA Quota Board · 数据来自 CLIProxyAPI Management API", font=self.fonts["tiny"], fill=(111, 126, 148))

    def _draw_background(self, draw: ImageDraw.ImageDraw, size: tuple[int, int]) -> None:
        width, height = size
        for y in range(height):
            ratio = y / max(1, height)
            color = (13 + int(4 * ratio), 18 + int(6 * ratio), 30 + int(10 * ratio))
            draw.line((0, y, width, y), fill=color)
        draw.ellipse((width - 170, -120, width + 80, 130), fill=(28, 45, 78))

    def _rounded(self, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill: tuple[int, int, int], outline: tuple[int, int, int] | None) -> None:
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=1 if outline else 0)

    def _tag(self, draw: ImageDraw.ImageDraw, text: str, color: tuple[int, int, int], x: int, y: int, scale: int) -> None:
        w = self._tag_width(text, scale)
        bg = (max(0, color[0] // 5), max(0, color[1] // 5), max(0, color[2] // 5))
        self._rounded(draw, (x, y, x + w, y + 24 * scale), 12 * scale, bg, None)
        draw.text((x + 10 * scale, y + 4 * scale), text, font=self.fonts["tiny"], fill=color)

    def _tag_width(self, text: str, scale: int) -> int:
        return (len(text) * 13 + 22) * scale

    def _measure_height(self, report: QuotaReport) -> int:
        height = self.margin + 66 + 58 + 14 + 34
        if not report.providers:
            return height + 110
        for provider in report.providers:
            height += self._provider_height(provider) + 12
        return max(260, height)

    def _provider_height(self, provider: QuotaProvider) -> int:
        if not provider.accounts:
            return 84
        return 44 + sum(self._account_height(account) + 10 for account in provider.accounts) + 4

    def _account_height(self, account: QuotaAccount) -> int:
        items = account.items or [QuotaItem(id="empty", label="暂无可展示额度项", percent=None, status="unknown", raw_message="未解析到额度数据")]
        item_height = sum(62 if (item.raw_message and item.status in {"unknown", "error"}) or (not item.reset_at and item.status in {"unknown", "error"}) else 48 for item in items)
        return 42 + item_height + 10

    def _filter_report(self, report: QuotaReport) -> QuotaReport:
        providers: list[QuotaProvider] = []
        for provider in report.providers:
            accounts = []
            for account in provider.accounts:
                items = [item for item in account.items if item.status in {"warning", "critical", "unknown", "error"}]
                if items:
                    accounts.append(QuotaAccount(id=account.id, name=account.name, display_name=account.display_name, status=account.status, items=items))
            if accounts:
                providers.append(QuotaProvider(name=provider.name, type=provider.type, accounts=accounts))
        message = report.message or "当前所有额度正常"
        return QuotaReport(generated_at=report.generated_at, summary=build_summary(providers), providers=providers, message=message)

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

    def _fit(self, text: str, max_chars: int) -> str:
        clean = sanitize_text(text)
        if len(clean) <= max_chars:
            return clean
        return clean[: max(1, max_chars - 1)] + "…"

    def _text_width(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> int:
        box = draw.textbbox((0, 0), text, font=font)
        return int(box[2] - box[0])

    def _load_fonts(self) -> dict[str, ImageFont.FreeTypeFont | ImageFont.ImageFont]:
        font_path = self._find_font()

        def load(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
            if font_path:
                return ImageFont.truetype(str(font_path), size * self.scale)
            return ImageFont.load_default()

        return {
            "title": load(28),
            "section": load(20),
            "metric": load(22),
            "account": load(18),
            "body": load(17),
            "small": load(14),
            "tiny": load(13),
        }

    def _find_font(self) -> Path | None:
        candidates = [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/msyh.ttf",
            "C:/Windows/Fonts/simhei.ttf",
            "/System/Library/Fonts/PingFang.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf",
            "/usr/share/fonts/opentype/source-han-sans/SourceHanSansSC-Regular.otf",
        ]
        for candidate in candidates:
            path = Path(candidate)
            if path.exists():
                return path
        return None
