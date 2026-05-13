from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from models import QuotaAccount, QuotaItem, QuotaProvider, QuotaReport
from utils import cleanup_old_files, sanitize_text


STATUS_COLORS = {
    "ok": (64, 211, 146),
    "warning": (245, 166, 35),
    "critical": (255, 86, 86),
    "unknown": (145, 158, 171),
    "error": (220, 80, 135),
}


class QuotaCardRenderer:
    def __init__(self, data_dir: Path, *, high_resolution: bool = True):
        self.cache_dir = data_dir / "cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.scale = 2 if high_resolution else 1
        self.width = 960
        self.fonts = self._load_fonts()

    def render_overview(self, report: QuotaReport) -> Path:
        return self._render(report, "overview", "CPA 额度看板", "所有账号额度")

    def render_compact(self, report: QuotaReport) -> Path:
        filtered = self._filter_report(report)
        return self._render(filtered, "compact", "CPA 额度看板", "简洁模式")

    def render_alert(self, report: QuotaReport, changes: list[dict[str, Any]] | None = None) -> Path:
        filtered = self._report_from_changes(report, changes or []) if changes else self._filter_report(report)
        return self._render(filtered, "alert", "CPA 额度告警", "本次状态变化")

    def render_test_alert(self) -> Path:
        from models import QuotaAccount, QuotaItem, QuotaProvider, QuotaReport, build_summary
        from utils import now_text

        item = QuotaItem(id="test", label="测试额度项", percent=4, reset_at="05/13 18:25", status="critical", raw_message="测试通知")
        account = QuotaAccount(id="test", name="test@example.com", display_name="test@example.com", status="critical", items=[item])
        provider = QuotaProvider(name="Codex", type="codex", accounts=[account])
        report = QuotaReport(generated_at=now_text(), summary=build_summary([provider]), providers=[provider], message="这是一条测试告警。")
        return self.render_alert(report)

    def _render(self, report: QuotaReport, kind: str, title: str, subtitle: str) -> Path:
        cleanup_old_files(self.cache_dir)
        rows = self._measure_rows(report)
        height = max(520, 250 + rows * 128 + len(report.providers) * 48)
        scale = self.scale
        image = Image.new("RGB", (self.width * scale, height * scale), (13, 18, 30))
        draw = ImageDraw.Draw(image)
        self._draw_background(draw, image.size)

        def s(value: int) -> int:
            return value * scale

        draw.text((s(48), s(42)), title, font=self.fonts["title"], fill=(245, 248, 255))
        draw.text((s(50), s(92)), subtitle, font=self.fonts["small"], fill=(145, 158, 171))
        draw.text((s(620), s(50)), f"生成时间 {report.generated_at}", font=self.fonts["small"], fill=(145, 158, 171))

        self._summary(draw, report, s(48), s(132), scale)
        y = 220
        if report.message and not report.providers:
            self._empty(draw, report.message, y, scale)
        else:
            for provider in report.providers:
                y = self._provider(image, draw, provider, y, scale)

        footer_y = min(height - 54, y + 20)
        draw.text((s(48), s(footer_y)), "CPA Quota Board", font=self.fonts["small"], fill=(117, 132, 153))
        draw.text((s(665), s(footer_y)), "数据来自 CLIProxyAPI Management API", font=self.fonts["small"], fill=(117, 132, 153))

        if scale != 1:
            image = image.resize((self.width, height), Image.Resampling.LANCZOS)
        output = self.cache_dir / f"quota_{kind}_{uuid.uuid4().hex}.png"
        image.save(output, "PNG")
        return output

    def _summary(self, draw: ImageDraw.ImageDraw, report: QuotaReport, x: int, y: int, scale: int) -> None:
        items = [
            ("账号", report.summary.get("total_accounts", 0), (94, 129, 244)),
            ("正常", report.summary.get("ok", 0), STATUS_COLORS["ok"]),
            ("低额度", report.summary.get("warning", 0), STATUS_COLORS["warning"]),
            ("危险/耗尽", report.summary.get("critical", 0), STATUS_COLORS["critical"]),
            ("异常", report.summary.get("error", 0), STATUS_COLORS["error"]),
        ]
        card_w = 164 * scale
        for index, (label, value, color) in enumerate(items):
            left = x + index * (card_w + 12 * scale)
            self._rounded(draw, (left, y, left + card_w, y + 62 * scale), 20 * scale, (25, 34, 52), (45, 57, 80))
            draw.text((left + 18 * scale, y + 12 * scale), str(value), font=self.fonts["metric"], fill=color)
            draw.text((left + 72 * scale, y + 23 * scale), label, font=self.fonts["small"], fill=(183, 194, 210))

    def _provider(self, image: Image.Image, draw: ImageDraw.ImageDraw, provider: QuotaProvider, y: int, scale: int) -> int:
        x = 48 * scale
        draw.text((x, y * scale), provider.name, font=self.fonts["section"], fill=(231, 236, 246))
        y += 44
        for account in provider.accounts:
            y = self._account(image, draw, account, y, scale)
        return y + 18

    def _account(self, image: Image.Image, draw: ImageDraw.ImageDraw, account: QuotaAccount, y: int, scale: int) -> int:
        x = 48 * scale
        w = 864 * scale
        item_count = max(1, len(account.items))
        h = (78 + item_count * 54) * scale
        shadow = Image.new("RGBA", (w + 24 * scale, h + 24 * scale), (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        shadow_draw.rounded_rectangle((12 * scale, 12 * scale, w + 12 * scale, h + 12 * scale), radius=24 * scale, fill=(0, 0, 0, 90))
        shadow = shadow.filter(ImageFilter.GaussianBlur(10 * scale))
        image.paste(shadow, (x - 12 * scale, y * scale - 6 * scale), shadow)
        self._rounded(draw, (x, y * scale, x + w, y * scale + h), 24 * scale, (23, 31, 48), (47, 61, 86))
        draw.text((x + 24 * scale, y * scale + 20 * scale), account.display_name, font=self.fonts["account"], fill=(246, 248, 252))
        self._tag(draw, account.status, x + w - 130 * scale, y * scale + 20 * scale, scale)
        offset = y * scale + 68 * scale
        for item in account.items or [QuotaItem(id="empty", label="无额度项", percent=None, status="unknown")]:
            self._quota_item(draw, item, x + 24 * scale, offset, w - 48 * scale, scale)
            offset += 54 * scale
        return y + int(h / scale) + 18

    def _quota_item(self, draw: ImageDraw.ImageDraw, item: QuotaItem, x: int, y: int, width: int, scale: int) -> None:
        color = STATUS_COLORS.get(item.status, STATUS_COLORS["unknown"])
        draw.text((x, y), item.label[:34], font=self.fonts["body"], fill=(222, 228, 238))
        percent_text = "未知" if item.percent is None else f"{item.percent}%"
        draw.text((x + width - 88 * scale, y), percent_text, font=self.fonts["body"], fill=color)
        bar_x = x + 260 * scale
        bar_y = y + 8 * scale
        bar_w = width - 380 * scale
        self._rounded(draw, (bar_x, bar_y, bar_x + bar_w, bar_y + 12 * scale), 6 * scale, (48, 57, 76), None)
        if item.percent is not None:
            fill_w = max(6 * scale, math.floor(bar_w * item.percent / 100))
            self._rounded(draw, (bar_x, bar_y, bar_x + fill_w, bar_y + 12 * scale), 6 * scale, color, None)
        reset = f"reset {item.reset_at}" if item.reset_at else sanitize_text(item.raw_message)[:28]
        if reset:
            draw.text((bar_x, y + 23 * scale), reset, font=self.fonts["tiny"], fill=(139, 152, 171))

    def _tag(self, draw: ImageDraw.ImageDraw, status: str, x: int, y: int, scale: int) -> None:
        color = STATUS_COLORS.get(status, STATUS_COLORS["unknown"])
        text = {"ok": "OK", "warning": "LOW", "critical": "CRITICAL", "unknown": "UNKNOWN", "error": "ERROR"}.get(status, status.upper())
        self._rounded(draw, (x, y, x + 104 * scale, y + 30 * scale), 15 * scale, tuple(max(0, c // 4) for c in color), color)
        draw.text((x + 15 * scale, y + 6 * scale), text, font=self.fonts["tiny"], fill=color)

    def _empty(self, draw: ImageDraw.ImageDraw, message: str, y: int, scale: int) -> None:
        x = 48 * scale
        self._rounded(draw, (x, y * scale, x + 864 * scale, y * scale + 160 * scale), 24 * scale, (23, 31, 48), (47, 61, 86))
        draw.text((x + 30 * scale, y * scale + 58 * scale), sanitize_text(message), font=self.fonts["section"], fill=(183, 194, 210))

    def _draw_background(self, draw: ImageDraw.ImageDraw, size: tuple[int, int]) -> None:
        width, height = size
        for y in range(height):
            ratio = y / max(1, height)
            color = (13 + int(8 * ratio), 18 + int(10 * ratio), 30 + int(18 * ratio))
            draw.line((0, y, width, y), fill=color)
        draw.ellipse((-120, -140, 420, 360), fill=(30, 58, 105))
        draw.ellipse((width - 360, 70, width + 120, 560), fill=(61, 38, 99))

    def _rounded(self, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], radius: int, fill: tuple[int, int, int], outline: tuple[int, int, int] | None) -> None:
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=1 if outline else 0)

    def _measure_rows(self, report: QuotaReport) -> int:
        return sum(max(1, len(account.items)) for provider in report.providers for account in provider.accounts) or 1

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
        from models import build_summary

        return QuotaReport(generated_at=report.generated_at, summary=build_summary(providers), providers=providers, message=report.message or "当前没有低额度、危险、耗尽或异常账号。")

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
        from models import build_summary

        return QuotaReport(generated_at=report.generated_at, summary=build_summary(providers), providers=providers, message="本次没有需要通知的状态变化。")

    def _load_fonts(self) -> dict[str, ImageFont.FreeTypeFont | ImageFont.ImageFont]:
        font_path = self._find_font()

        def load(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
            if font_path:
                return ImageFont.truetype(str(font_path), size * self.scale)
            return ImageFont.load_default()

        return {
            "title": load(36),
            "section": load(24),
            "metric": load(26),
            "account": load(21),
            "body": load(18),
            "small": load(15),
            "tiny": load(13),
        }

    def _find_font(self) -> Path | None:
        candidates = [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/msyh.ttf",
            "/System/Library/Fonts/PingFang.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
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
