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
    from .models import QuotaReport
    from .renderer import QuotaCardRenderer
    from .state import QuotaStateStore
    from .utils import ConfigError, normalize_cpa_url, plugin_data_dir, sanitize_text
except ImportError:
    from client import CPAClient
    from formatter import format_alert_report, format_quota_report
    from models import QuotaReport
    from renderer import QuotaCardRenderer
    from state import QuotaStateStore
    from utils import ConfigError, normalize_cpa_url, plugin_data_dir, sanitize_text


PLUGIN_NAME = "astrbot_plugin_cpa_quota_board"


@register(PLUGIN_NAME, "MuLingQwQ", "CPA 额度看板", "0.1.0")
class CPAQuotaBoardPlugin(Star):
    def __init__(self, context: Context, config: Any | None = None):
        super().__init__(context)
        self.config = self._load_config(config)
        self.data_dir = plugin_data_dir(PLUGIN_NAME)
        self.state = QuotaStateStore(self.data_dir)
        self.renderer = QuotaCardRenderer(self.data_dir, high_resolution=self._bool_config("render_high_resolution", True))
        self._poll_task: asyncio.Task[None] | None = None
        self._last_report = None
        self._last_fetch_at = 0.0
        self._last_poll_time = "从未巡检"
        self._cache_seconds = 10

    async def initialize(self):
        await self._sync_usage_statistics_option()
        if self._bool_config("enable_quota_notify", False):
            self._poll_task = asyncio.create_task(self._poll_loop())
            logger.info("CPA 额度看板后台巡检已启动")

    @filter.command("额度")
    async def quota(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        yield await self._quota_image_result(event, compact=self._bool_config("compact_mode_default", False), force=False)

    @filter.command("cpa额度")
    async def cpa_quota(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        yield await self._quota_image_result(event, compact=self._bool_config("compact_mode_default", False), force=False)

    @filter.command("quota")
    async def quota_en(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        yield await self._quota_image_result(event, compact=self._bool_config("compact_mode_default", False), force=False)

    @filter.command("额度简洁")
    async def quota_compact(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        yield await self._quota_image_result(event, compact=True, force=False)

    @filter.command("额度刷新")
    async def quota_refresh(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        yield await self._quota_image_result(event, compact=self._bool_config("compact_mode_default", False), force=True)

    @filter.command("额度通知开启")
    async def notify_enable(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        await self.state.add_notify_target(event.unified_msg_origin)
        yield event.plain_result("已开启当前会话的 CPA 额度通知。")

    @filter.command("额度通知关闭")
    async def notify_disable(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        await self.state.remove_notify_target(event.unified_msg_origin)
        yield event.plain_result("已关闭当前会话的 CPA 额度通知。")

    @filter.command("额度测试通知")
    async def notify_test(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        path = self.renderer.render_test_alert()
        yield event.chain_result(self._image_chain(path))

    @filter.command("额度状态")
    async def quota_status(self, event: AstrMessageEvent) -> AsyncGenerator[Any, None]:
        targets = await self.state.list_notify_targets()
        enabled = "开启" if self._bool_config("enable_quota_notify", False) else "关闭"
        current = "已开启" if event.unified_msg_origin in targets else "未开启"
        text = (
            f"额度通知：{enabled}\n"
            f"CLIProxyAPI 用量统计发布：{'开启' if self._bool_config('enable_usage_statistics', False) else '关闭'}\n"
            f"当前会话：{current}\n"
            f"巡检间隔：{self._int_config('poll_interval_seconds', 300)} 秒\n"
            f"通知目标数量：{len(targets)}\n"
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

    async def _quota_image_result(self, event: AstrMessageEvent, *, compact: bool, force: bool):
        try:
            report = await self._fetch_report(force=force)
            if self._response_format() == "text":
                return event.plain_result(format_quota_report(report, compact=compact))
            path = self.renderer.render_compact(report) if compact else self.renderer.render_overview(report)
            return event.chain_result(self._image_chain(path))
        except ConfigError as exc:
            report = QuotaReport.empty(str(exc))
            if self._response_format() == "text":
                return event.plain_result(format_quota_report(report, compact=False, title="CPA 额度看板 - 配置错误"))
            path = self.renderer.render_overview(report)
            return event.chain_result(self._image_chain(path))
        except Exception as exc:
            logger.error("CPA 额度看板查询失败：%s", sanitize_text(exc))
            report = QuotaReport.empty(f"查询失败：{sanitize_text(exc)}")
            if self._response_format() == "text":
                return event.plain_result(format_quota_report(report, compact=False, title="CPA 额度看板 - 查询失败"))
            path = self.renderer.render_overview(report)
            return event.chain_result(self._image_chain(path))

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
                targets = await self.state.list_notify_targets()
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

    async def _sync_usage_statistics_option(self) -> None:
        try:
            await self._client().set_usage_statistics_enabled(self._bool_config("enable_usage_statistics", False))
            logger.info("CPA 额度看板已同步 CLIProxyAPI 用量统计发布开关")
        except ConfigError:
            return
        except Exception as exc:
            logger.warning("CPA 额度看板同步用量统计发布开关失败：%s", sanitize_text(exc))

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
        value = str(self.config.get("response_format", "text")).strip().lower()
        return "image" if value in {"image", "img", "图片"} else "text"

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
    "enable_usage_statistics": False,
    "response_format": "text",
    "render_high_resolution": True,
    "max_accounts_per_provider": 20,
    "compact_mode_default": False,
}
