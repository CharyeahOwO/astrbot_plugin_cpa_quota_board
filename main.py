from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

try:
    from .client import CPAClient
    from .formatter import format_alert_report, format_quota_report
    from .models import QuotaReport, build_summary
    from .renderer import QuotaCardRenderer
    from .state import QuotaStateStore
    from .utils import ConfigError, normalize_cpa_url, plugin_data_dir, sanitize_text
except ImportError:
    from client import CPAClient
    from formatter import format_alert_report, format_quota_report
    from models import QuotaReport, build_summary
    from renderer import QuotaCardRenderer
    from state import QuotaStateStore
    from utils import ConfigError, normalize_cpa_url, plugin_data_dir, sanitize_text


PLUGIN_NAME = "astrbot_plugin_cpa_quota_board"


@register(PLUGIN_NAME, "MuLingQwQ", "CPA 额度看板", "0.1.0")
class CPAQuotaBoardPlugin(Star):
    def __init__(self, context: Context, config: Any | None = None):
        super().__init__(context)
        self._config_obj = config
        self.config = self._load_config(config)
        self.data_dir = plugin_data_dir(PLUGIN_NAME)
        self.state = QuotaStateStore(self.data_dir)
        self.renderer = QuotaCardRenderer(
            self.data_dir,
            high_resolution=self._bool_config("render_high_resolution", True),
            font_path=str(self.config.get("font_path", "")),
        )
        self._poll_task: asyncio.Task[None] | None = None
        self._last_report = None
        self._last_fetch_at = 0.0
        self._last_poll_time = "从未巡检"
        self._cache_seconds = 10

    async def initialize(self):
        if self._bool_config("enable_quota_notify", False):
            self._poll_task = asyncio.create_task(self._poll_loop())
            logger.info("CPA 额度看板后台巡检已启动")

    @filter.command("额度")
    async def quota(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """查询 CPA 额度看板，可附加 provider 关键词筛选。"""
        args = self._parse_args(event)
        provider = args[0] if args else ""
        yield await self._quota_image_result(event, force=False, provider=provider)

    @filter.command("cpa额度")
    async def cpa_quota(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """查询 CPA 额度看板，兼容 /额度。"""
        args = self._parse_args(event)
        provider = args[0] if args else ""
        yield await self._quota_image_result(event, force=False, provider=provider)

    @filter.command("cpa")
    async def cpa(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """查询 CPA 额度看板，兼容 /额度。"""
        args = self._parse_args(event)
        provider = args[0] if args else ""
        yield await self._quota_image_result(event, force=False, provider=provider)

    @filter.command("quota")
    async def quota_en(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """查询 CPA 额度看板，英文兼容指令。"""
        args = self._parse_args(event)
        provider = args[0] if args else ""
        yield await self._quota_image_result(event, force=False, provider=provider)

    @filter.command("额度刷新")
    async def quota_refresh(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """跳过短缓存，重新拉取并渲染 CPA 额度。"""
        args = self._parse_args(event)
        provider = args[0] if args else ""
        yield await self._quota_image_result(event, force=True, provider=provider)

    @filter.command("额度摘要")
    async def quota_dashboard(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """只展示重点额度摘要，适合快速查看异常项。"""
        args = self._parse_args(event)
        provider = args[0] if args else ""
        yield await self._quota_dashboard_result(event, force=False, provider=provider)

    @filter.command("额度详情分页")
    async def quota_detail_pages(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """分页查看完整额度明细，支持页码和每页账号数。"""
        args = self._parse_args(event)
        provider = ""
        page = 0
        page_size = 1
        numbers: list[int] = []

        for arg in args:
            if arg.isdigit():
                numbers.append(int(arg))
            elif "=" in arg:
                k, v = arg.split("=", 1)
                if k in ("page", "p") and v.isdigit():
                    page = int(v)
                elif k in ("size", "s", "page_size") and v.isdigit():
                    page_size = int(v)
                elif k in ("provider", "prov"):
                    provider = v
            else:
                provider = arg

        if numbers:
            page = numbers[0]
        if len(numbers) >= 2:
            page_size = numbers[1]

        yield await self._quota_detail_pages_result(event, provider=provider, page=page, page_size=page_size)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("开启cpa预警")
    async def notify_enable(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """管理员指令：将当前会话加入 CPA 额度预警白名单。"""
        target = event.unified_msg_origin
        targets = self._notify_targets()
        if target not in targets:
            targets.append(target)
            self._set_config_value("notify_whitelist", sorted(targets))
        if not self._bool_config("enable_quota_notify", False):
            self._set_config_value("enable_quota_notify", True)
        self._ensure_poll_task()
        yield event.plain_result("已开启当前会话的 CPA 额度预警，并已写入配置白名单。")

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command("关闭cpa预警")
    async def notify_disable(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """管理员指令：将当前会话移出 CPA 额度预警白名单。"""
        target = event.unified_msg_origin
        targets = [item for item in self._notify_targets() if item != target]
        self._set_config_value("notify_whitelist", sorted(targets))
        yield event.plain_result("已关闭当前会话的 CPA 额度预警，并已从配置白名单移除。")

    @filter.command("额度测试通知")
    async def notify_test(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """向当前会话发送一条 CPA 额度预警测试图片。"""
        path = self.renderer.render_test_alert()
        yield event.chain_result(self._image_chain(path))

    @filter.command("额度状态")
    async def quota_status(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        """查看 CPA 额度预警开关、白名单数量和上次巡检时间。"""
        targets = self._notify_targets()
        enabled = "开启" if self._bool_config("enable_quota_notify", False) else "关闭"
        current = "已开启" if event.unified_msg_origin in targets else "未开启"
        text = (
            f"额度通知：{enabled}\n"
            f"当前会话：{current}\n"
            f"巡检间隔：{self._int_config('poll_interval_seconds', 300)} 秒\n"
            f"白名单会话数量：{len(targets)}\n"
            f"上次巡检时间：{self._last_poll_time}"
        )
        yield event.plain_result(text)

    async def terminate(self):
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    def _parse_args(self, event: AstrMessageEvent) -> list[str]:
        message = str(getattr(event, "message_str", "") or "")
        parts = message.strip().split()
        return parts[1:] if parts else []

    def _filter_report_by_provider(self, report: QuotaReport, provider_keyword: str) -> QuotaReport:
        if not provider_keyword:
            return report
        keyword = provider_keyword.lower()
        providers = []
        for p in report.providers:
            if keyword in p.name.lower() or keyword in p.type.lower():
                providers.append(p)
        return QuotaReport(
            generated_at=report.generated_at,
            summary=build_summary(providers),
            providers=providers,
            message=report.message
        )

    async def _handle_render_error(self, event: AstrMessageEvent, exc: Exception, title_suffix: str):
        if isinstance(exc, ConfigError):
            report = QuotaReport.empty(str(exc))
            title = "CPA 额度看板 - 配置错误"
        else:
            logger.error(f"CPA 额度看板{title_suffix}失败：%s", sanitize_text(exc))
            report = QuotaReport.empty(f"{title_suffix}失败：{sanitize_text(exc)}")
            title = f"CPA 额度看板 - {title_suffix}失败"

        if self._response_format() == "text":
            return event.plain_result(format_quota_report(report, compact=False, title=title))
        path = self.renderer.render_overview(report)
        return event.chain_result(self._image_chain(path))

    async def _quota_image_result(self, event: AstrMessageEvent, *, force: bool, provider: str = ""):
        try:
            report = await self._fetch_report(force=force)
            if provider:
                report = self._filter_report_by_provider(report, provider)
                if not report.providers:
                    report.message = f"未找到匹配 '{provider}' 的额度数据"

            if self._response_format() == "text":
                return event.plain_result(format_quota_report(report, compact=False))
            path = self.renderer.render_overview(report)
            return event.chain_result(self._image_chain(path))
        except Exception as exc:
            return await self._handle_render_error(event, exc, "查询")

    async def _quota_dashboard_result(self, event: AstrMessageEvent, *, force: bool, provider: str = ""):
        try:
            report = await self._fetch_report(force=force)
            if provider:
                report = self._filter_report_by_provider(report, provider)
                if not report.providers:
                    report.message = f"未找到匹配 '{provider}' 的额度数据"

            if self._response_format() == "text":
                return event.plain_result(format_quota_report(report, compact=True, title="CPA 额度看板 - 摘要"))

            path = self.renderer.render_dashboard(report)
            return event.chain_result(self._image_chain(path))
        except Exception as exc:
            return await self._handle_render_error(event, exc, "摘要")

    async def _quota_detail_pages_result(self, event: AstrMessageEvent, *, provider: str, page: int, page_size: int):
        try:
            report = await self._fetch_report(force=False)
            if provider:
                report = self._filter_report_by_provider(report, provider)
                if not report.providers:
                    report.message = f"未找到匹配 '{provider}' 的额度数据"

            if self._response_format() == "text":
                return event.plain_result(format_quota_report(report, compact=False, title=f"CPA 额度看板 - 详情" + (f" (第{page}页)" if page > 0 else "")))

            paths = self.renderer.render_detail_pages(report, page_size=page_size)
            if not paths:
                path = self.renderer.render_empty("暂无额度数据")
                return event.chain_result(self._image_chain(path))

            chain = MessageChain()
            if page > 0:
                page = max(1, min(page, len(paths)))
                chain = chain.file_image(str(paths[page - 1]))
            else:
                for p in paths:
                    chain = chain.file_image(str(p))
            return event.chain_result(chain)
        except Exception as exc:
            return await self._handle_render_error(event, exc, "详情分页")

    async def _fetch_report(self, *, force: bool):
        now = time.time()
        if not force and self._last_report is not None and now - self._last_fetch_at <= self._cache_seconds:
            return self._last_report
        client = self._client()
        report = await client.fetch_all_quotas()
        self._log_report_summary(report)
        self._last_report = report
        self._last_fetch_at = now
        return report

    async def _poll_loop(self) -> None:
        interval = max(30, self._int_config("poll_interval_seconds", 300))
        while True:
            try:
                report = await self._client().fetch_all_quotas()
                self._log_report_summary(report)
                self._last_poll_time = report.generated_at
                changes = await self.state.diff_and_save(report)
                targets = self._notify_targets()
                if changes and targets:
                    if self._response_format() == "text":
                        chain = MessageChain().message(format_alert_report(report, changes))
                    else:
                        path = self.renderer.render_alert(report, changes)
                        chain = self._image_chain(path)
                    for target in targets:
                        try:
                            await self.context.send_message(target, chain)
                        except Exception as exc:
                            logger.warning("CPA 额度看板主动通知失败：%s", sanitize_text(exc))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("CPA 额度看板后台巡检失败：%s", sanitize_text(exc))
            await asyncio.sleep(interval)

    def _log_report_summary(self, report: QuotaReport) -> None:
        lines = [f"providers={len(report.providers)} summary={report.summary}"]
        for provider in report.providers:
            lines.append(f"provider={sanitize_text(provider.name)} accounts={len(provider.accounts)}")
            for account in provider.accounts:
                lines.append(f"  account={sanitize_text(account.display_name)} status={account.status} items={len(account.items)}")
                for item in account.items:
                    lines.append(
                        "    item="
                        f"{sanitize_text(item.label)} percent={item.percent if item.percent is not None else '--'} "
                        f"status={item.status} reset_at={sanitize_text(item.reset_at)}"
                    )
        if report.message:
            lines.append(f"message={sanitize_text(report.message)}")
        logger.info("CPA 额度看板渲染数据摘要：\n%s", "\n".join(lines))

    def _client(self) -> CPAClient:
        cpa_url = normalize_cpa_url(str(self.config.get("cpa_url", "")))
        cpa_password = str(self.config.get("cpa_password", ""))
        if not cpa_password:
            raise ConfigError("请先配置 cpa_password / Management Key。")
        return CPAClient(
            cpa_url,
            cpa_password,
            verify_ssl=self._bool_config("verify_ssl", True),
            request_timeout=self._int_config("request_timeout", 30),
            warning_percent=self._int_config("warning_percent", 20),
            critical_percent=self._int_config("critical_percent", 5),
            max_accounts_per_provider=self._int_config("max_accounts_per_provider", 20),
        )

    def _image_chain(self, path: Path) -> MessageChain:
        return MessageChain().file_image(str(path))

    def _response_format(self) -> str:
        value = str(self.config.get("response_format", "image")).strip().lower()
        return "image" if value in {"image", "img", "图片"} else "text"

    def _notify_targets(self) -> list[str]:
        value = self.config.get("notify_whitelist", [])
        if isinstance(value, str):
            targets = [item.strip() for item in value.replace("\n", ",").split(",")]
        elif isinstance(value, list):
            targets = [str(item).strip() for item in value]
        else:
            targets = []
        return sorted({item for item in targets if item})

    def _set_config_value(self, key: str, value: Any) -> None:
        self.config[key] = value
        if self._config_obj is not None:
            try:
                self._config_obj[key] = value
            except Exception:
                pass
            save = getattr(self._config_obj, "save_config", None)
            if callable(save):
                try:
                    save()
                except Exception as exc:
                    logger.warning("CPA 额度看板保存配置失败：%s", sanitize_text(exc))

    def _ensure_poll_task(self) -> None:
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._poll_loop())
            logger.info("CPA 额度看板后台巡检已启动")

    def _load_config(self, config: Any | None = None) -> dict[str, Any]:
        if isinstance(config, dict):
            return {key: config.get(key, default) for key, default in DEFAULT_CONFIG.items()}
        if config is not None and hasattr(config, "get"):
            return {key: config.get(key, default) for key, default in DEFAULT_CONFIG.items()}
        try:
            context_config = self.context.get_config()
            if isinstance(context_config, dict):
                plugin_config = context_config.get(PLUGIN_NAME)
                if isinstance(plugin_config, dict):
                    return {key: plugin_config.get(key, default) for key, default in DEFAULT_CONFIG.items()}
            if hasattr(context_config, "get"):
                plugin_config = context_config.get(PLUGIN_NAME)
                if isinstance(plugin_config, dict):
                    return {key: plugin_config.get(key, default) for key, default in DEFAULT_CONFIG.items()}
        except Exception:
            pass
        return dict(DEFAULT_CONFIG)

    def _bool_config(self, key: str, default: bool) -> bool:
        value = self.config.get(key, default)
        if isinstance(value, str):
            return value.lower() in {"true", "1", "yes", "on"}
        return bool(value)

    def _int_config(self, key: str, default: int) -> int:
        try:
            return int(self.config.get(key, default))
        except (TypeError, ValueError):
            return default


DEFAULT_CONFIG = {
    "cpa_url": "",
    "cpa_password": "",
    "verify_ssl": True,
    "request_timeout": 30,
    "poll_interval_seconds": 300,
    "warning_percent": 20,
    "critical_percent": 5,
    "enable_quota_notify": False,
    "notify_whitelist": [],
    "response_format": "image",
    "render_high_resolution": True,
    "font_path": "",
    "max_accounts_per_provider": 20,
}
