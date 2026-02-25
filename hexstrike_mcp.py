#!/usr/bin/env python3
"""
HexStrike AI MCP 客户端

职责：
- 作为 MCP 侧适配层，将 AI 客户端请求转发到 HexStrike API 服务。
- 暴露统一的 MCP 工具 接口，供 Claude/Cursor/Copilot 等客户端调用。

v6.0 关键增强：
- 与服务端统一的终端配色与输出风格
- 更稳健的连接重试、异常处理与恢复信息透出
- FastMCP 深度集成，支持大规模安全工具编排
"""

import sys
import os
import argparse
import logging
from typing import Dict, Any, Optional, Set
import requests
import time
from datetime import datetime

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    # 兼容不同 FastMCP 版本的导入路径
    from fastmcp import FastMCP

class HexStrikeColors:
    """与服务端 ModernVisualEngine 对齐的 ANSI 颜色常量。"""

    # 基础颜色（兼容旧逻辑）
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'

    # 核心主题颜色
    MATRIX_GREEN = '\033[38;5;46m'
    NEON_BLUE = '\033[38;5;51m'
    ELECTRIC_PURPLE = '\033[38;5;129m'
    CYBER_ORANGE = '\033[38;5;208m'
    HACKER_RED = '\033[38;5;196m'
    TERMINAL_GRAY = '\033[38;5;240m'
    BRIGHT_WHITE = '\033[97m'
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'

    # 扩展红色系强调色
    BLOOD_RED = '\033[38;5;124m'
    CRIMSON = '\033[38;5;160m'
    DARK_RED = '\033[38;5;88m'
    FIRE_RED = '\033[38;5;202m'
    ROSE_RED = '\033[38;5;167m'
    BURGUNDY = '\033[38;5;52m'
    SCARLET = '\033[38;5;197m'
    RUBY = '\033[38;5;161m'

    # 高亮背景色
    HIGHLIGHT_RED = '\033[48;5;196m\033[38;5;15m'  # 说明：Red background, white text
    HIGHLIGHT_YELLOW = '\033[48;5;226m\033[38;5;16m'  # 说明：Yellow background, black text
    HIGHLIGHT_GREEN = '\033[48;5;46m\033[38;5;16m'  # 说明：Green background, black text
    HIGHLIGHT_BLUE = '\033[48;5;51m\033[38;5;16m'  # 说明：Blue background, black text
    HIGHLIGHT_PURPLE = '\033[48;5;129m\033[38;5;15m'  # 说明：Purple background, white text

    # 状态语义颜色
    SUCCESS = '\033[38;5;46m'  # 说明：Bright green
    WARNING = '\033[38;5;208m'  # 说明：Orange
    ERROR = '\033[38;5;196m'  # 说明：Bright red
    CRITICAL = '\033[48;5;196m\033[38;5;15m\033[1m'  # 说明：Red background, white bold text
    INFO = '\033[38;5;51m'  # 说明：Cyan
    DEBUG = '\033[38;5;240m'  # 说明：Gray

    # 漏洞等级颜色
    VULN_CRITICAL = '\033[48;5;124m\033[38;5;15m\033[1m'  # 说明：Dark red background
    VULN_HIGH = '\033[38;5;196m\033[1m'  # 说明：Bright red bold
    VULN_MEDIUM = '\033[38;5;208m\033[1m'  # 说明：Orange bold
    VULN_LOW = '\033[38;5;226m'  # 说明：Yellow
    VULN_INFO = '\033[38;5;51m'  # 说明：Cyan

    # 工具执行状态颜色
    TOOL_RUNNING = '\033[38;5;46m\033[5m'  # 说明：Blinking green
    TOOL_SUCCESS = '\033[38;5;46m\033[1m'  # 说明：Bold green
    TOOL_FAILED = '\033[38;5;196m\033[1m'  # 说明：Bold red
    TOOL_TIMEOUT = '\033[38;5;208m\033[1m'  # 说明：Bold orange
    TOOL_RECOVERY = '\033[38;5;129m\033[1m'  # 说明：Bold purple

# 向后兼容别名
Colors = HexStrikeColors

def localize_output_text(message: str) -> str:
    """将 MCP 终端日志中的常见英文短语尽量转换为中文。"""
    # 仅做可读性增强，不保证完整翻译；未知词保持原样。
    replacements = [
        ("Attempting to connect to HexStrike AI API at", "正在连接 HexStrike AI API："),
        ("Successfully connected to HexStrike AI API Server at", "已成功连接 HexStrike AI API 服务："),
        ("Server health status", "服务健康状态"),
        ("Server version", "服务版本"),
        ("Connection refused to", "连接被拒绝："),
        ("Connection test failed", "连接测试失败："),
        ("Connection attempt", "连接尝试"),
        ("Request failed", "请求失败"),
        ("Unexpected error", "未预期错误"),
        ("Starting", "开始"),
        ("completed", "完成"),
        ("failed", "失败"),
        ("scan", "扫描"),
        ("analysis", "分析"),
    ]
    text = str(message)
    for src, dst in replacements:
        text = text.replace(src, dst)
    return text

class ColoredFormatter(logging.Formatter):
    """日志格式化器：按日志级别附加颜色并统一中文化输出。"""

    COLORS = {
        'DEBUG': HexStrikeColors.DEBUG,
        'INFO': HexStrikeColors.SUCCESS,
        'WARNING': HexStrikeColors.WARNING,
        'ERROR': HexStrikeColors.ERROR,
        'CRITICAL': HexStrikeColors.CRITICAL
    }

    EMOJIS = {
        'DEBUG': '',
        'INFO': '',
        'WARNING': '',
        'ERROR': '',
        'CRITICAL': ''
    }

    def format(self, record):
        emoji = self.EMOJIS.get(record.levelname, '')
        color = self.COLORS.get(record.levelname, HexStrikeColors.BRIGHT_WHITE)
        translated = localize_output_text(record.msg)

        # 先中文化，再统一附加 ANSI 颜色，方便终端快速分级识别。
        record.msg = f"{color}{emoji} {translated}{HexStrikeColors.RESET}"
        return super().format(record)

# 初始化日志系统
logging.basicConfig(
    level=logging.INFO,
    format="[ HexStrike MCP] %(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stderr)
    ]
)

# 为根日志处理器注入彩色 formatter
for handler in logging.getLogger().handlers:
    handler.setFormatter(ColoredFormatter(
        "[ HexStrike MCP] %(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_HEXSTRIKE_SERVER = "http://127.0.0.1:8888"  # 默认 HexStrike API 地址
DEFAULT_REQUEST_TIMEOUT = 300  # API 请求默认超时（秒）
MAX_RETRIES = 3  # 连接重试次数上限
DEFAULT_ENABLE_TOOLS = os.environ.get("HEXSTRIKE_ENABLE_TOOLS", "")
DEFAULT_DISABLE_TOOLS = os.environ.get("HEXSTRIKE_DISABLE_TOOLS", "")

def normalize_tool_name(name: str) -> str:
    """规范化工具名，便于统一匹配。"""
    return (name or "").strip().lower().replace("-", "_").replace(" ", "_")

def parse_tool_list(raw: str) -> Set[str]:
    """解析逗号分隔工具列表。"""
    if not raw:
        return set()
    return {
        normalize_tool_name(part)
        for part in raw.split(",")
        if normalize_tool_name(part)
    }

class MCPToolSwitch:
    """MCP 工具注册开关控制器。"""

    def __init__(self, enable_tools: str = "", disable_tools: str = ""):
        self.enabled_set = parse_tool_list(enable_tools)
        self.disabled_set = parse_tool_list(disable_tools)

    def is_enabled(self, tool_name: str) -> bool:
        normalized = normalize_tool_name(tool_name)
        if self.enabled_set and normalized not in self.enabled_set:
            return False
        if normalized in self.disabled_set:
            return False
        return True

    def has_filters(self) -> bool:
        return bool(self.enabled_set or self.disabled_set)

class HexStrikeClient:
    """HexStrike API 客户端，封装连接重试与请求容错。"""

    def __init__(self, server_url: str, timeout: int = DEFAULT_REQUEST_TIMEOUT):
        """
        初始化 HexStrike API 客户端。

        参数:
            server_url: HexStrike API 服务地址
            timeout: 请求超时（秒）
        """
        self.server_url = server_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

        # 启动时先做轻量健康检查，避免后续工具首次调用时才暴露连接问题
        connected = False
        for i in range(MAX_RETRIES):
            try:
                logger.info(f" Attempting to connect to HexStrike AI API at {server_url} (attempt {i+1}/{MAX_RETRIES})")
                # 先测 /健康，验证服务可达与 JSON 响应可解析
                try:
                    test_response = self.session.get(f"{self.server_url}/health", timeout=5)
                    test_response.raise_for_status()
                    health_check = test_response.json()
                    connected = True
                    logger.info(f" Successfully connected to HexStrike AI API Server at {server_url}")
                    logger.info(f" Server health status: {health_check.get('status', 'unknown')}")
                    logger.info(f" Server version: {health_check.get('version', 'unknown')}")
                    break
                except requests.exceptions.ConnectionError:
                    logger.warning(f" Connection refused to {server_url}. Make sure the HexStrike AI server is running.")
                    time.sleep(2)  # 重试前短暂等待，降低瞬时抖动影响
                except Exception as e:
                    logger.warning(f"  Connection test failed: {str(e)}")
                    time.sleep(2)
            except Exception as e:
                logger.warning(f" Connection attempt {i+1} failed: {str(e)}")
                time.sleep(2)

        if not connected:
            error_msg = f"Failed to establish connection to HexStrike AI API Server at {server_url} after {MAX_RETRIES} attempts"
            logger.error(error_msg)
            # 允许 MCP 继续启动，便于排障；但后续工具请求大概率会失败

    def safe_get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        发送 获取 请求并统一处理异常。

        参数:
            endpoint: API 路径（不含前导 `/`）
            params: 可选查询参数

        返回:
            结构化响应字典
        """
        if params is None:
            params = {}

        url = f"{self.server_url}/{endpoint}"

        try:
            logger.debug(f" GET {url} with params: {params}")
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f" Request failed: {str(e)}")
            return {"error": f"Request failed: {str(e)}", "success": False}
        except Exception as e:
            logger.error(f" Unexpected error: {str(e)}")
            return {"error": f"Unexpected error: {str(e)}", "success": False}

    def safe_post(self, endpoint: str, json_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        发送 POST 请求并统一处理异常。

        参数:
            endpoint: API 路径（不含前导 `/`）
            json_data: 请求体 JSON

        返回:
            结构化响应字典
        """
        url = f"{self.server_url}/{endpoint}"

        try:
            logger.debug(f" POST {url} with data: {json_data}")
            response = self.session.post(url, json=json_data, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f" Request failed: {str(e)}")
            return {"error": f"Request failed: {str(e)}", "success": False}
        except Exception as e:
            logger.error(f" Unexpected error: {str(e)}")
            return {"error": f"Unexpected error: {str(e)}", "success": False}

    def execute_command(self, command: str, use_cache: bool = True) -> Dict[str, Any]:
        """
        调用后端通用命令执行接口。

        参数:
            command: 要执行的命令
            use_cache: 是否启用缓存

        返回:
            命令执行结果
        """
        return self.safe_post("api/command", {"command": command, "use_cache": use_cache})

    def check_health(self) -> Dict[str, Any]:
        """
        查询 HexStrike API 健康状态。

        返回:
            健康检查结果
        """
        return self.safe_get("health")

def setup_mcp_server(hexstrike_client: HexStrikeClient, tool_switch: Optional[MCPToolSwitch] = None) -> FastMCP:
    """
    注册 MCP 服务端 及全部工具函数。

    参数:
        hexstrike_client: 已初始化的 HexStrikeClient

    返回:
        配置完成的 FastMCP 实例
    """
    mcp = FastMCP("hexstrike-ai-mcp")
    tool_switch = tool_switch or MCPToolSwitch()
    enabled_tools = []
    disabled_tools = []
    registered_tools = set()
    duplicate_skipped_tools = []

    # 拦截 @mcp.tool() 注册流程，实现按名称启停工具。
    original_tool_decorator = mcp.tool

    def _conditional_tool(*decorator_args, **decorator_kwargs):
        base_decorator = original_tool_decorator(*decorator_args, **decorator_kwargs)

        def _register(func):
            declared_name = decorator_kwargs.get("name")
            tool_name = str(declared_name).strip() if declared_name else func.__name__
            normalized_name = normalize_tool_name(tool_name)

            if tool_switch.is_enabled(normalized_name):
                if normalized_name in registered_tools:
                    duplicate_skipped_tools.append(normalized_name)
                    logger.warning(f" 检测到重复 MCP 工具名，已跳过重复注册: {normalized_name}")
                    return func

                registered_tools.add(normalized_name)
                enabled_tools.append(normalized_name)
                return base_decorator(func)

            disabled_tools.append(normalized_name)
            return func

        return _register

    mcp.tool = _conditional_tool  # type: ignore[assignment]

    # ============================================================================
    # 核心网络扫描工具
    # ============================================================================

    @mcp.tool()
    def nmap_scan(target: str, scan_type: str = "-sV", ports: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行增强版 Nmap 扫描，并输出实时日志。

        参数:
            target: 目标 IP 或域名
            scan_type: 扫描类型（如 `-sV`、`-sC`）
            ports: 端口列表或范围（逗号分隔）
            additional_args: 额外 Nmap 参数

        返回:
            扫描结果与遥测信息
        """
        data = {
            "target": target,
            "scan_type": scan_type,
            "ports": ports,
            "additional_args": additional_args
        }
        logger.info(f"{HexStrikeColors.FIRE_RED} Initiating Nmap scan: {target}{HexStrikeColors.RESET}")

        # 默认开启恢复机制，提升工具链在异常场景下的可用性
        data["use_recovery"] = True
        result = hexstrike_client.safe_post("api/tools/nmap", data)

        if result.get("success"):
            logger.info(f"{HexStrikeColors.SUCCESS} Nmap scan completed successfully for {target}{HexStrikeColors.RESET}")

            # 输出恢复策略应用情况，方便追踪自动重试效果
            if result.get("recovery_info", {}).get("recovery_applied"):
                recovery_info = result["recovery_info"]
                attempts = recovery_info.get("attempts_made", 1)
                logger.info(f"{HexStrikeColors.HIGHLIGHT_YELLOW} Recovery applied: {attempts} attempts made {HexStrikeColors.RESET}")
        else:
            logger.error(f"{HexStrikeColors.ERROR} Nmap scan failed for {target}{HexStrikeColors.RESET}")

            # 高风险失败可提示人工介入
            if result.get("human_escalation"):
                logger.error(f"{HexStrikeColors.CRITICAL} HUMAN ESCALATION REQUIRED {HexStrikeColors.RESET}")

        return result

    @mcp.tool()
    def gobuster_scan(url: str, mode: str = "dir", wordlist: str = "/usr/share/wordlists/dirb/common.txt", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Gobuster 扫描目录/DNS/虚拟主机并记录增强日志。

        参数:
            url: 目标 URL
            mode: 扫描模式（`dir`/`dns`/`fuzz`/`vhost`）
            wordlist: 字典路径
            additional_args: 额外参数

        返回:
            扫描结果与遥测信息
        """
        data = {
            "url": url,
            "mode": mode,
            "wordlist": wordlist,
            "additional_args": additional_args
        }
        logger.info(f"{HexStrikeColors.CRIMSON} Starting Gobuster {mode} scan: {url}{HexStrikeColors.RESET}")

        # 默认开启恢复机制，降低外部工具失败率
        data["use_recovery"] = True
        result = hexstrike_client.safe_post("api/tools/gobuster", data)

        if result.get("success"):
            logger.info(f"{HexStrikeColors.SUCCESS} Gobuster scan completed for {url}{HexStrikeColors.RESET}")

            # 输出恢复信息，便于后续调参
            if result.get("recovery_info", {}).get("recovery_applied"):
                recovery_info = result["recovery_info"]
                attempts = recovery_info.get("attempts_made", 1)
                logger.info(f"{HexStrikeColors.HIGHLIGHT_YELLOW} Recovery applied: {attempts} attempts made {HexStrikeColors.RESET}")
        else:
            logger.error(f"{HexStrikeColors.ERROR} Gobuster scan failed for {url}{HexStrikeColors.RESET}")

            # 某些失败场景后端会返回替代工具建议
            if result.get("alternative_tool_suggested"):
                alt_tool = result["alternative_tool_suggested"]
                logger.info(f"{HexStrikeColors.HIGHLIGHT_BLUE} Alternative tool suggested: {alt_tool} {HexStrikeColors.RESET}")

        return result

    @mcp.tool()
    def nuclei_scan(target: str, severity: str = "", tags: str = "", template: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Nuclei 漏洞扫描，支持按严重级别/标签过滤。

        参数:
            target: 目标 URL 或 IP
            severity: 严重级别过滤（严重/高/中/低/info）
            tags: 标签过滤（如 `cve,rce,lfi`）
            template: 自定义模板路径
            additional_args: 额外参数

        返回:
            漏洞发现结果与遥测信息
        """
        data = {
            "target": target,
            "severity": severity,
            "tags": tags,
            "template": template,
            "additional_args": additional_args
        }
        logger.info(f"{HexStrikeColors.BLOOD_RED} Starting Nuclei vulnerability scan: {target}{HexStrikeColors.RESET}")

        # 默认开启恢复机制，提升扫描稳定性
        data["use_recovery"] = True
        result = hexstrike_client.safe_post("api/tools/nuclei", data)

        if result.get("success"):
            logger.info(f"{HexStrikeColors.SUCCESS} Nuclei scan completed for {target}{HexStrikeColors.RESET}")

            # 对高危结果做突出告警，便于在日志中快速定位
            if result.get("stdout") and "CRITICAL" in result["stdout"]:
                logger.warning(f"{HexStrikeColors.CRITICAL} CRITICAL vulnerabilities detected! {HexStrikeColors.RESET}")
            elif result.get("stdout") and "HIGH" in result["stdout"]:
                logger.warning(f"{HexStrikeColors.FIRE_RED} HIGH severity vulnerabilities found! {HexStrikeColors.RESET}")

            # 输出恢复信息，便于追踪重试策略命中情况
            if result.get("recovery_info", {}).get("recovery_applied"):
                recovery_info = result["recovery_info"]
                attempts = recovery_info.get("attempts_made", 1)
                logger.info(f"{HexStrikeColors.HIGHLIGHT_YELLOW} Recovery applied: {attempts} attempts made {HexStrikeColors.RESET}")
        else:
            logger.error(f"{HexStrikeColors.ERROR} Nuclei scan failed for {target}{HexStrikeColors.RESET}")

        return result

    # ============================================================================
    # 云安全工具
    # ============================================================================

    @mcp.tool()
    def prowler_scan(provider: str = "aws", profile: str = "default", region: str = "", checks: str = "", output_dir: str = "/tmp/prowler_output", output_format: str = "json", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Prowler 用于 综合 云 安全 assessment.

        参数:
            provider: 云 provider (aws, azure, gcp)
            profile: AWS profile 到 use
            region: Specific region 到 扫描
            checks: Specific checks 到 run
            output_dir: 目录 到 save 结果
            output_format: 输出 format (JSON, csv, html)
            additional_args: 附加 Prowler arguments

        返回:
            云 安全 assessment 结果
        """
        data = {
            "provider": provider,
            "profile": profile,
            "region": region,
            "checks": checks,
            "output_dir": output_dir,
            "output_format": output_format,
            "additional_args": additional_args
        }
        logger.info(f"  Starting Prowler {provider} security assessment")
        result = hexstrike_client.safe_post("api/tools/prowler", data)
        if result.get("success"):
            logger.info(f" Prowler assessment completed")
        else:
            logger.error(f" Prowler assessment failed")
        return result

    @mcp.tool()
    def trivy_scan(scan_type: str = "image", target: str = "", output_format: str = "json", severity: str = "", output_file: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Trivy 用于 容器 与 filesystem 漏洞 扫描.

        参数:
            scan_type: 类型 的 扫描 (image, fs, repo, config)
            target: 目标 到 扫描 (image name, 目录, repository)
            output_format: 输出 format (JSON, table, sarif)
            severity: Severity filter (UNKNOWN,低,中,高,严重)
            output_file: 文件 到 save 结果
            additional_args: 附加 Trivy arguments

        返回:
            漏洞 扫描 结果
        """
        data = {
            "scan_type": scan_type,
            "target": target,
            "output_format": output_format,
            "severity": severity,
            "output_file": output_file,
            "additional_args": additional_args
        }
        logger.info(f" Starting Trivy {scan_type} scan: {target}")
        result = hexstrike_client.safe_post("api/tools/trivy", data)
        if result.get("success"):
            logger.info(f" Trivy scan completed for {target}")
        else:
            logger.error(f" Trivy scan failed for {target}")
        return result

    # ============================================================================
    # 增强 云 与 容器 安全 工具 (v6.0)
    # ============================================================================

    @mcp.tool()
    def scout_suite_assessment(provider: str = "aws", profile: str = "default",
                              report_dir: str = "/tmp/scout-suite", services: str = "",
                              exceptions: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Scout Suite 用于 multi-云 安全 assessment.

        参数:
            provider: 云 provider (aws, azure, gcp, aliyun, oci)
            profile: AWS profile 到 use
            report_dir: 目录 到 save reports
            services: Specific services 到 assess
            exceptions: Exceptions 文件 path
            additional_args: 附加 Scout Suite arguments

        返回:
            Multi-云 安全 assessment 结果
        """
        data = {
            "provider": provider,
            "profile": profile,
            "report_dir": report_dir,
            "services": services,
            "exceptions": exceptions,
            "additional_args": additional_args
        }
        logger.info(f"  Starting Scout Suite {provider} assessment")
        result = hexstrike_client.safe_post("api/tools/scout-suite", data)
        if result.get("success"):
            logger.info(f" Scout Suite assessment completed")
        else:
            logger.error(f" Scout Suite assessment failed")
        return result

    @mcp.tool()
    def cloudmapper_analysis(action: str = "collect", account: str = "",
                            config: str = "config.json", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 CloudMapper 用于 AWS 网络 visualization 与 安全 分析.

        参数:
            action: Action 到 perform (收集, prepare, webserver, find_admins, etc.)
            account: AWS account 到 分析
            config: 配置 文件 path
            additional_args: 附加 CloudMapper arguments

        返回:
            AWS 网络 visualization 与 安全 分析 结果
        """
        data = {
            "action": action,
            "account": account,
            "config": config,
            "additional_args": additional_args
        }
        logger.info(f"  Starting CloudMapper {action}")
        result = hexstrike_client.safe_post("api/tools/cloudmapper", data)
        if result.get("success"):
            logger.info(f" CloudMapper {action} completed")
        else:
            logger.error(f" CloudMapper {action} failed")
        return result

    @mcp.tool()
    def pacu_exploitation(session_name: str = "hexstrike_session", modules: str = "",
                         data_services: str = "", regions: str = "",
                         additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Pacu 用于 AWS exploitation 框架.

        参数:
            session_name: 说明：Pacu session name
            modules: Comma-separated 列出 的 modules 到 run
            data_services: Data services 到 enumerate
            regions: AWS regions 到 目标
            additional_args: 附加 Pacu arguments

        返回:
            AWS exploitation 框架 结果
        """
        data = {
            "session_name": session_name,
            "modules": modules,
            "data_services": data_services,
            "regions": regions,
            "additional_args": additional_args
        }
        logger.info(f"  Starting Pacu AWS exploitation")
        result = hexstrike_client.safe_post("api/tools/pacu", data)
        if result.get("success"):
            logger.info(f" Pacu exploitation completed")
        else:
            logger.error(f" Pacu exploitation failed")
        return result

    @mcp.tool()
    def kube_hunter_scan(target: str = "", remote: str = "", cidr: str = "",
                        interface: str = "", active: bool = False, report: str = "json",
                        additional_args: str = "") -> Dict[str, Any]:
        """
        执行 kube-hunter 用于 Kubernetes penetration 测试.

        参数:
            target: Specific 目标 到 扫描
            remote: Remote 目标 到 扫描
            cidr: CIDR range 到 扫描
            interface: 网络 接口 到 扫描
            active: 启用 active hunting (potentially harmful)
            report: 说明：Report format (JSON, yaml)
            additional_args: 附加 kube-hunter arguments

        返回:
            Kubernetes penetration 测试 结果
        """
        data = {
            "target": target,
            "remote": remote,
            "cidr": cidr,
            "interface": interface,
            "active": active,
            "report": report,
            "additional_args": additional_args
        }
        logger.info(f"  Starting kube-hunter Kubernetes scan")
        result = hexstrike_client.safe_post("api/tools/kube-hunter", data)
        if result.get("success"):
            logger.info(f" kube-hunter scan completed")
        else:
            logger.error(f" kube-hunter scan failed")
        return result

    @mcp.tool()
    def kube_bench_cis(targets: str = "", version: str = "", config_dir: str = "",
                      output_format: str = "json", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 kube-bench 用于 CIS Kubernetes benchmark checks.

        参数:
            targets: Targets 到 检查 (master, node, etcd, policies)
            version: 说明：Kubernetes version
            config_dir: 配置 目录
            output_format: 输出 format (JSON, yaml)
            additional_args: 附加 kube-bench arguments

        返回:
            CIS Kubernetes benchmark 结果
        """
        data = {
            "targets": targets,
            "version": version,
            "config_dir": config_dir,
            "output_format": output_format,
            "additional_args": additional_args
        }
        logger.info(f"  Starting kube-bench CIS benchmark")
        result = hexstrike_client.safe_post("api/tools/kube-bench", data)
        if result.get("success"):
            logger.info(f" kube-bench benchmark completed")
        else:
            logger.error(f" kube-bench benchmark failed")
        return result

    @mcp.tool()
    def docker_bench_security_scan(checks: str = "", exclude: str = "",
                                  output_file: str = "/tmp/docker-bench-results.json",
                                  additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Docker Bench 用于 安全 用于 Docker 安全 assessment.

        参数:
            checks: Specific checks 到 run
            exclude: Checks 到 exclude
            output_file: 输出 文件 path
            additional_args: 附加 Docker Bench arguments

        返回:
            Docker 安全 assessment 结果
        """
        data = {
            "checks": checks,
            "exclude": exclude,
            "output_file": output_file,
            "additional_args": additional_args
        }
        logger.info(f" Starting Docker Bench Security assessment")
        result = hexstrike_client.safe_post("api/tools/docker-bench-security", data)
        if result.get("success"):
            logger.info(f" Docker Bench Security completed")
        else:
            logger.error(f" Docker Bench Security failed")
        return result

    @mcp.tool()
    def clair_vulnerability_scan(image: str, config: str = "/etc/clair/config.yaml",
                                output_format: str = "json", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Clair 用于 容器 漏洞 分析.

        参数:
            image: 容器 image 到 扫描
            config: Clair 配置 文件
            output_format: 输出 format (JSON, yaml)
            additional_args: 附加 Clair arguments

        返回:
            容器 漏洞 分析 结果
        """
        data = {
            "image": image,
            "config": config,
            "output_format": output_format,
            "additional_args": additional_args
        }
        logger.info(f" Starting Clair vulnerability scan: {image}")
        result = hexstrike_client.safe_post("api/tools/clair", data)
        if result.get("success"):
            logger.info(f" Clair scan completed for {image}")
        else:
            logger.error(f" Clair scan failed for {image}")
        return result

    @mcp.tool()
    def falco_runtime_monitoring(config_file: str = "/etc/falco/falco.yaml",
                                rules_file: str = "", output_format: str = "json",
                                duration: int = 60, additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Falco 用于 runtime 安全 监控.

        参数:
            config_file: Falco 配置 文件
            rules_file: Custom rules 文件
            output_format: 输出 format (JSON, text)
            duration: 监控 持续时间 在 seconds
            additional_args: 附加 Falco arguments

        返回:
            Runtime 安全 监控 结果
        """
        data = {
            "config_file": config_file,
            "rules_file": rules_file,
            "output_format": output_format,
            "duration": duration,
            "additional_args": additional_args
        }
        logger.info(f"  Starting Falco runtime monitoring for {duration}s")
        result = hexstrike_client.safe_post("api/tools/falco", data)
        if result.get("success"):
            logger.info(f" Falco monitoring completed")
        else:
            logger.error(f" Falco monitoring failed")
        return result

    @mcp.tool()
    def checkov_iac_scan(directory: str = ".", framework: str = "", check: str = "",
                        skip_check: str = "", output_format: str = "json",
                        additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Checkov 用于 infrastructure as code 安全 扫描.

        参数:
            directory: 目录 到 扫描
            framework: 框架 到 扫描 (terraform, cloudformation, kubernetes, etc.)
            check: Specific 检查 到 run
            skip_check: 检查 到 skip
            output_format: 输出 format (JSON, yaml, cli)
            additional_args: 附加 Checkov arguments

        返回:
            Infrastructure as code 安全 扫描 结果
        """
        data = {
            "directory": directory,
            "framework": framework,
            "check": check,
            "skip_check": skip_check,
            "output_format": output_format,
            "additional_args": additional_args
        }
        logger.info(f" Starting Checkov IaC scan: {directory}")
        result = hexstrike_client.safe_post("api/tools/checkov", data)
        if result.get("success"):
            logger.info(f" Checkov scan completed")
        else:
            logger.error(f" Checkov scan failed")
        return result

    @mcp.tool()
    def terrascan_iac_scan(scan_type: str = "all", iac_dir: str = ".",
                          policy_type: str = "", output_format: str = "json",
                          severity: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Terrascan 用于 infrastructure as code 安全 扫描.

        参数:
            scan_type: 类型 的 扫描 (全部, terraform, k8s, etc.)
            iac_dir: Infrastructure as code 目录
            policy_type: Policy 类型 到 use
            output_format: 输出 format (JSON, yaml, xml)
            severity: Severity filter (高, 中, 低)
            additional_args: 附加 Terrascan arguments

        返回:
            Infrastructure as code 安全 扫描 结果
        """
        data = {
            "scan_type": scan_type,
            "iac_dir": iac_dir,
            "policy_type": policy_type,
            "output_format": output_format,
            "severity": severity,
            "additional_args": additional_args
        }
        logger.info(f" Starting Terrascan IaC scan: {iac_dir}")
        result = hexstrike_client.safe_post("api/tools/terrascan", data)
        if result.get("success"):
            logger.info(f" Terrascan scan completed")
        else:
            logger.error(f" Terrascan scan failed")
        return result

    # ============================================================================
    # 文件 操作 & 载荷 GENERATION
    # ============================================================================

    @mcp.tool()
    def create_file(filename: str, content: str, binary: bool = False) -> Dict[str, Any]:
        """
        创建 a 文件 使用 specified content 在 the HexStrike 服务端.

        参数:
            filename: Name 的 the 文件 到 创建
            content: Content 到 write 到 the 文件
            binary: Whether the content is 二进制 data

        返回:
            文件 creation 结果
        """
        data = {
            "filename": filename,
            "content": content,
            "binary": binary
        }
        logger.info(f" Creating file: {filename}")
        result = hexstrike_client.safe_post("api/files/create", data)
        if result.get("success"):
            logger.info(f" File created successfully: {filename}")
        else:
            logger.error(f" Failed to create file: {filename}")
        return result

    @mcp.tool()
    def modify_file(filename: str, content: str, append: bool = False) -> Dict[str, Any]:
        """
        Modify an existing 文件 在 the HexStrike 服务端.

        参数:
            filename: Name 的 the 文件 到 modify
            content: Content 到 write 或 append
            append: Whether 到 append 到 the 文件 (True) 或 overwrite (False)

        返回:
            文件 modification 结果
        """
        data = {
            "filename": filename,
            "content": content,
            "append": append
        }
        logger.info(f"  Modifying file: {filename}")
        result = hexstrike_client.safe_post("api/files/modify", data)
        if result.get("success"):
            logger.info(f" File modified successfully: {filename}")
        else:
            logger.error(f" Failed to modify file: {filename}")
        return result

    @mcp.tool()
    def delete_file(filename: str) -> Dict[str, Any]:
        """
        删除 a 文件 或 目录 在 the HexStrike 服务端.

        参数:
            filename: Name 的 the 文件 或 目录 到 删除

        返回:
            文件 deletion 结果
        """
        data = {
            "filename": filename
        }
        logger.info(f"  Deleting file: {filename}")
        result = hexstrike_client.safe_post("api/files/delete", data)
        if result.get("success"):
            logger.info(f" File deleted successfully: {filename}")
        else:
            logger.error(f" Failed to delete file: {filename}")
        return result

    @mcp.tool()
    def list_files(directory: str = ".") -> Dict[str, Any]:
        """
        列出 文件 在 a 目录 在 the HexStrike 服务端.

        参数:
            directory: 目录 到 列出 (relative 到 服务端's base 目录)

        返回:
            目录 listing 结果
        """
        logger.info(f" Listing files in directory: {directory}")
        result = hexstrike_client.safe_get("api/files/list", {"directory": directory})
        if result.get("success"):
            file_count = len(result.get("files", []))
            logger.info(f" Listed {file_count} files in {directory}")
        else:
            logger.error(f" Failed to list files in {directory}")
        return result

    @mcp.tool()
    def generate_payload(payload_type: str = "buffer", size: int = 1024, pattern: str = "A", filename: str = "") -> Dict[str, Any]:
        """
        生成 large payloads 用于 测试 与 exploitation.

        参数:
            payload_type: 类型 的 载荷 (buffer, cyclic, random)
            size: Size 的 the 载荷 在 bytes
            pattern: Pattern 到 use 用于 buffer payloads
            filename: Custom filename (auto-generated 如果 empty)

        返回:
            载荷 generation 结果
        """
        data = {
            "type": payload_type,
            "size": size,
            "pattern": pattern
        }
        if filename:
            data["filename"] = filename

        logger.info(f" Generating {payload_type} payload: {size} bytes")
        result = hexstrike_client.safe_post("api/payloads/generate", data)
        if result.get("success"):
            logger.info(f" Payload generated successfully")
        else:
            logger.error(f" Failed to generate payload")
        return result

    # ============================================================================
    # 说明：PYTHON ENVIRONMENT MANAGEMENT
    # ============================================================================

    @mcp.tool()
    def install_python_package(package: str, env_name: str = "default") -> Dict[str, Any]:
        """
        安装 a Python package 在 a virtual environment 在 the HexStrike 服务端.

        参数:
            package: Name 的 the Python package 到 安装
            env_name: Name 的 the virtual environment

        返回:
            Package installation 结果
        """
        data = {
            "package": package,
            "env_name": env_name
        }
        logger.info(f" Installing Python package: {package} in env {env_name}")
        result = hexstrike_client.safe_post("api/python/install", data)
        if result.get("success"):
            logger.info(f" Package {package} installed successfully")
        else:
            logger.error(f" Failed to install package {package}")
        return result

    @mcp.tool()
    def execute_python_script(script: str, env_name: str = "default", filename: str = "") -> Dict[str, Any]:
        """
        执行 a Python script 在 a virtual environment 在 the HexStrike 服务端.

        参数:
            script: Python script content 到 执行
            env_name: Name 的 the virtual environment
            filename: Custom script filename (auto-generated 如果 empty)

        返回:
            Script execution 结果
        """
        data = {
            "script": script,
            "env_name": env_name
        }
        if filename:
            data["filename"] = filename

        logger.info(f" Executing Python script in env {env_name}")
        result = hexstrike_client.safe_post("api/python/execute", data)
        if result.get("success"):
            logger.info(f" Python script executed successfully")
        else:
            logger.error(f" Python script execution failed")
        return result

    # ============================================================================
    # 附加 安全 工具 来自 ORIGINAL IMPLEMENTATION
    # ============================================================================

    @mcp.tool()
    def dirb_scan(url: str, wordlist: str = "/usr/share/wordlists/dirb/common.txt", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Dirb 用于 目录 brute forcing 使用 增强日志.

        参数:
            url: The 目标 URL
            wordlist: Path 到 wordlist 文件
            additional_args: 附加 Dirb arguments

        返回:
            扫描 结果 使用 增强 telemetry
        """
        data = {
            "url": url,
            "wordlist": wordlist,
            "additional_args": additional_args
        }
        logger.info(f" Starting Dirb scan: {url}")
        result = hexstrike_client.safe_post("api/tools/dirb", data)
        if result.get("success"):
            logger.info(f" Dirb scan completed for {url}")
        else:
            logger.error(f" Dirb scan failed for {url}")
        return result

    @mcp.tool()
    def nikto_scan(target: str, additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Nikto web 漏洞 scanner 使用 增强日志.

        参数:
            target: The 目标 URL 或 IP
            additional_args: 附加 Nikto arguments

        返回:
            扫描 结果 使用 discovered 漏洞
        """
        data = {
            "target": target,
            "additional_args": additional_args
        }
        logger.info(f" Starting Nikto scan: {target}")
        result = hexstrike_client.safe_post("api/tools/nikto", data)
        if result.get("success"):
            logger.info(f" Nikto scan completed for {target}")
        else:
            logger.error(f" Nikto scan failed for {target}")
        return result

    @mcp.tool()
    def sqlmap_scan(url: str, data: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 SQLMap 用于 SQL injection 测试 使用 增强日志.

        参数:
            url: The 目标 URL
            data: POST data 用于 测试
            additional_args: 附加 SQLMap arguments

        返回:
            SQL injection 测试 结果
        """
        data_payload = {
            "url": url,
            "data": data,
            "additional_args": additional_args
        }
        logger.info(f" Starting SQLMap scan: {url}")
        result = hexstrike_client.safe_post("api/tools/sqlmap", data_payload)
        if result.get("success"):
            logger.info(f" SQLMap scan completed for {url}")
        else:
            logger.error(f" SQLMap scan failed for {url}")
        return result

    @mcp.tool()
    def metasploit_run(module: str, options: Dict[str, Any] = {}) -> Dict[str, Any]:
        """
        执行 a Metasploit module 使用 增强日志.

        参数:
            module: The Metasploit module 到 use
            options: Dictionary 的 module options

        返回:
            Metasploit execution 结果
        """
        data = {
            "module": module,
            "options": options
        }
        logger.info(f" Starting Metasploit module: {module}")
        result = hexstrike_client.safe_post("api/tools/metasploit", data)
        if result.get("success"):
            logger.info(f" Metasploit module completed: {module}")
        else:
            logger.error(f" Metasploit module failed: {module}")
        return result

    @mcp.tool()
    def hydra_attack(
        target: str,
        service: str,
        username: str = "",
        username_file: str = "",
        password: str = "",
        password_file: str = "",
        additional_args: str = ""
    ) -> Dict[str, Any]:
        """
        执行 Hydra 用于 password brute forcing 使用 增强日志.

        参数:
            target: The 目标 IP 或 hostname
            service: The service 到 attack (ssh, ftp, HTTP, etc.)
            username: Single username 到 测试
            username_file: 文件 containing usernames
            password: Single password 到 测试
            password_file: 文件 containing passwords
            additional_args: 附加 Hydra arguments

        返回:
            Brute force attack 结果
        """
        data = {
            "target": target,
            "service": service,
            "username": username,
            "username_file": username_file,
            "password": password,
            "password_file": password_file,
            "additional_args": additional_args
        }
        logger.info(f" Starting Hydra attack: {target}:{service}")
        result = hexstrike_client.safe_post("api/tools/hydra", data)
        if result.get("success"):
            logger.info(f" Hydra attack completed for {target}")
        else:
            logger.error(f" Hydra attack failed for {target}")
        return result

    @mcp.tool()
    def john_crack(
        hash_file: str,
        wordlist: str = "/usr/share/wordlists/rockyou.txt",
        format_type: str = "",
        additional_args: str = ""
    ) -> Dict[str, Any]:
        """
        执行 John the Ripper 用于 password cracking 使用 增强日志.

        参数:
            hash_file: 文件 containing password hashes
            wordlist: Wordlist 文件 到 use
            format_type: Hash format 类型
            additional_args: 附加 John arguments

        返回:
            Password cracking 结果
        """
        data = {
            "hash_file": hash_file,
            "wordlist": wordlist,
            "format": format_type,
            "additional_args": additional_args
        }
        logger.info(f" Starting John the Ripper: {hash_file}")
        result = hexstrike_client.safe_post("api/tools/john", data)
        if result.get("success"):
            logger.info(f" John the Ripper completed")
        else:
            logger.error(f" John the Ripper failed")
        return result

    @mcp.tool()
    def wpscan_analyze(url: str, additional_args: str = "") -> Dict[str, Any]:
        """
        执行 WPScan 用于 WordPress 漏洞 扫描 使用 增强日志.

        参数:
            url: 说明：The WordPress site URL
            additional_args: 附加 WPScan arguments

        返回:
            WordPress 漏洞 扫描 结果
        """
        data = {
            "url": url,
            "additional_args": additional_args
        }
        logger.info(f" Starting WPScan: {url}")
        result = hexstrike_client.safe_post("api/tools/wpscan", data)
        if result.get("success"):
            logger.info(f" WPScan completed for {url}")
        else:
            logger.error(f" WPScan failed for {url}")
        return result

    @mcp.tool()
    def enum4linux_scan(target: str, additional_args: str = "-a") -> Dict[str, Any]:
        """
        执行 Enum4linux 用于 SMB enumeration 使用 增强日志.

        参数:
            target: The 目标 IP address
            additional_args: 附加 Enum4linux arguments

        返回:
            SMB enumeration 结果
        """
        data = {
            "target": target,
            "additional_args": additional_args
        }
        logger.info(f" Starting Enum4linux: {target}")
        result = hexstrike_client.safe_post("api/tools/enum4linux", data)
        if result.get("success"):
            logger.info(f" Enum4linux completed for {target}")
        else:
            logger.error(f" Enum4linux failed for {target}")
        return result

    @mcp.tool()
    def ffuf_scan(url: str, wordlist: str = "/usr/share/wordlists/dirb/common.txt", mode: str = "directory", match_codes: str = "200,204,301,302,307,401,403", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 FFuf 用于 web fuzzing 使用 增强日志.

        参数:
            url: The 目标 URL
            wordlist: Wordlist 文件 到 use
            mode: Fuzzing 模式 (目录, vhost, 参数)
            match_codes: HTTP 状态 codes 到 match
            additional_args: 附加 FFuf arguments

        返回:
            Web fuzzing 结果
        """
        data = {
            "url": url,
            "wordlist": wordlist,
            "mode": mode,
            "match_codes": match_codes,
            "additional_args": additional_args
        }
        logger.info(f" Starting FFuf {mode} fuzzing: {url}")
        result = hexstrike_client.safe_post("api/tools/ffuf", data)
        if result.get("success"):
            logger.info(f" FFuf fuzzing completed for {url}")
        else:
            logger.error(f" FFuf fuzzing failed for {url}")
        return result

    @mcp.tool()
    def netexec_scan(target: str, protocol: str = "smb", username: str = "", password: str = "", hash_value: str = "", module: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 NetExec (formerly CrackMapExec) 用于 网络 enumeration 使用 增强日志.

        参数:
            target: The 目标 IP 或 网络
            protocol: Protocol 到 use (smb, ssh, winrm, etc.)
            username: Username 用于 认证
            password: Password 用于 认证
            hash_value: Hash 用于 pass-the-hash attacks
            module: NetExec module 到 执行
            additional_args: 附加 NetExec arguments

        返回:
            网络 enumeration 结果
        """
        data = {
            "target": target,
            "protocol": protocol,
            "username": username,
            "password": password,
            "hash": hash_value,
            "module": module,
            "additional_args": additional_args
        }
        logger.info(f" Starting NetExec {protocol} scan: {target}")
        result = hexstrike_client.safe_post("api/tools/netexec", data)
        if result.get("success"):
            logger.info(f" NetExec scan completed for {target}")
        else:
            logger.error(f" NetExec scan failed for {target}")
        return result

    @mcp.tool()
    def amass_scan(domain: str, mode: str = "enum", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Amass 用于 subdomain enumeration 使用 增强日志.

        参数:
            domain: The 目标 域名
            mode: Amass 模式 (enum, intel, viz)
            additional_args: 附加 Amass arguments

        返回:
            Subdomain enumeration 结果
        """
        data = {
            "domain": domain,
            "mode": mode,
            "additional_args": additional_args
        }
        logger.info(f" Starting Amass {mode}: {domain}")
        result = hexstrike_client.safe_post("api/tools/amass", data)
        if result.get("success"):
            logger.info(f" Amass completed for {domain}")
        else:
            logger.error(f" Amass failed for {domain}")
        return result

    @mcp.tool()
    def hashcat_crack(hash_file: str, hash_type: str, attack_mode: str = "0", wordlist: str = "/usr/share/wordlists/rockyou.txt", mask: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Hashcat 用于 高级 password cracking 使用 增强日志.

        参数:
            hash_file: 文件 containing password hashes
            hash_type: Hash 类型 number 用于 Hashcat
            attack_mode: Attack 模式 (0=dict, 1=combo, 3=mask, etc.)
            wordlist: Wordlist 文件 用于 dictionary attacks
            mask: Mask 用于 mask attacks
            additional_args: 附加 Hashcat arguments

        返回:
            Password cracking 结果
        """
        data = {
            "hash_file": hash_file,
            "hash_type": hash_type,
            "attack_mode": attack_mode,
            "wordlist": wordlist,
            "mask": mask,
            "additional_args": additional_args
        }
        logger.info(f" Starting Hashcat attack: mode {attack_mode}")
        result = hexstrike_client.safe_post("api/tools/hashcat", data)
        if result.get("success"):
            logger.info(f" Hashcat attack completed")
        else:
            logger.error(f" Hashcat attack failed")
        return result

    @mcp.tool()
    def subfinder_scan(domain: str, silent: bool = True, all_sources: bool = False, additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Subfinder 用于 passive subdomain enumeration 使用 增强日志.

        参数:
            domain: The 目标 域名
            silent: Run 在 silent 模式
            all_sources: Use 全部 sources
            additional_args: 附加 Subfinder arguments

        返回:
            Passive subdomain enumeration 结果
        """
        data = {
            "domain": domain,
            "silent": silent,
            "all_sources": all_sources,
            "additional_args": additional_args
        }
        logger.info(f" Starting Subfinder: {domain}")
        result = hexstrike_client.safe_post("api/tools/subfinder", data)
        if result.get("success"):
            logger.info(f" Subfinder completed for {domain}")
        else:
            logger.error(f" Subfinder failed for {domain}")
        return result

    @mcp.tool()
    def smbmap_scan(target: str, username: str = "", password: str = "", domain: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 SMBMap 用于 SMB share enumeration 使用 增强日志.

        参数:
            target: The 目标 IP address
            username: Username 用于 认证
            password: Password 用于 认证
            domain: 域名 用于 认证
            additional_args: 附加 SMBMap arguments

        返回:
            SMB share enumeration 结果
        """
        data = {
            "target": target,
            "username": username,
            "password": password,
            "domain": domain,
            "additional_args": additional_args
        }
        logger.info(f" Starting SMBMap: {target}")
        result = hexstrike_client.safe_post("api/tools/smbmap", data)
        if result.get("success"):
            logger.info(f" SMBMap completed for {target}")
        else:
            logger.error(f" SMBMap failed for {target}")
        return result

    # ============================================================================
    # 增强 网络 PENETRATION 测试 工具 (v6.0)
    # ============================================================================

    @mcp.tool()
    def rustscan_fast_scan(target: str, ports: str = "", ulimit: int = 5000,
                          batch_size: int = 4500, timeout: int = 1500,
                          scripts: bool = False, additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Rustscan 用于 ultra-fast port 扫描 使用 增强日志.

        参数:
            target: The 目标 IP address 或 hostname
            ports: Specific ports 到 扫描 (e.g., "22,80,443")
            ulimit: 文件 descriptor limit
            batch_size: Batch size 用于 扫描
            timeout: 超时 在 milliseconds
            scripts: Run Nmap scripts 在 discovered ports
            additional_args: 附加 Rustscan arguments

        返回:
            Ultra-fast port 扫描 结果
        """
        data = {
            "target": target,
            "ports": ports,
            "ulimit": ulimit,
            "batch_size": batch_size,
            "timeout": timeout,
            "scripts": scripts,
            "additional_args": additional_args
        }
        logger.info(f" Starting Rustscan: {target}")
        result = hexstrike_client.safe_post("api/tools/rustscan", data)
        if result.get("success"):
            logger.info(f" Rustscan completed for {target}")
        else:
            logger.error(f" Rustscan failed for {target}")
        return result

    @mcp.tool()
    def masscan_high_speed(target: str, ports: str = "1-65535", rate: int = 1000,
                          interface: str = "", router_mac: str = "", source_ip: str = "",
                          banners: bool = False, additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Masscan 用于 high-speed Internet-scale port 扫描 使用 智能 rate limiting.

        参数:
            target: The 目标 IP address 或 CIDR range
            ports: Port range 到 扫描
            rate: Packets per 第二 rate
            interface: 网络 接口 到 use
            router_mac: 说明：Router MAC address
            source_ip: 说明：Source IP address
            banners: 启用 banner grabbing
            additional_args: 附加 Masscan arguments

        返回:
            High-speed port 扫描 结果 使用 智能 rate limiting
        """
        data = {
            "target": target,
            "ports": ports,
            "rate": rate,
            "interface": interface,
            "router_mac": router_mac,
            "source_ip": source_ip,
            "banners": banners,
            "additional_args": additional_args
        }
        logger.info(f" Starting Masscan: {target} at rate {rate}")
        result = hexstrike_client.safe_post("api/tools/masscan", data)
        if result.get("success"):
            logger.info(f" Masscan completed for {target}")
        else:
            logger.error(f" Masscan failed for {target}")
        return result

    @mcp.tool()
    def nmap_advanced_scan(target: str, scan_type: str = "-sS", ports: str = "",
                          timing: str = "T4", nse_scripts: str = "", os_detection: bool = False,
                          version_detection: bool = False, aggressive: bool = False,
                          stealth: bool = False, additional_args: str = "") -> Dict[str, Any]:
        """
        执行 高级 Nmap scans 使用 custom NSE scripts 与 optimized timing.

        参数:
            target: The 目标 IP address 或 hostname
            scan_type: Nmap 扫描 类型 (e.g., -sS, -sT, -sU)
            ports: Specific ports 到 扫描
            timing: 说明：Timing template (T0-T5)
            nse_scripts: Custom NSE scripts 到 run
            os_detection: 启用 OS detection
            version_detection: 启用 version detection
            aggressive: 启用 aggressive 扫描
            stealth: 启用 stealth 模式
            additional_args: 附加 Nmap arguments

        返回:
            高级 Nmap 扫描 结果 使用 custom NSE scripts
        """
        data = {
            "target": target,
            "scan_type": scan_type,
            "ports": ports,
            "timing": timing,
            "nse_scripts": nse_scripts,
            "os_detection": os_detection,
            "version_detection": version_detection,
            "aggressive": aggressive,
            "stealth": stealth,
            "additional_args": additional_args
        }
        logger.info(f" Starting Advanced Nmap: {target}")
        result = hexstrike_client.safe_post("api/tools/nmap-advanced", data)
        if result.get("success"):
            logger.info(f" Advanced Nmap completed for {target}")
        else:
            logger.error(f" Advanced Nmap failed for {target}")
        return result

    @mcp.tool()
    def autorecon_comprehensive(target: str, output_dir: str = "/tmp/autorecon",
                               port_scans: str = "top-100-ports", service_scans: str = "default",
                               heartbeat: int = 60, timeout: int = 300,
                               additional_args: str = "") -> Dict[str, Any]:
        """
        执行 AutoRecon 用于 综合 automated 侦察.

        参数:
            target: The 目标 IP address 或 hostname
            output_dir: 输出 目录 用于 结果
            port_scans: Port 扫描 配置
            service_scans: Service 扫描 配置
            heartbeat: Heartbeat interval 在 seconds
            timeout: 超时 用于 individual scans
            additional_args: 附加 AutoRecon arguments

        返回:
            综合 automated 侦察 结果
        """
        data = {
            "target": target,
            "output_dir": output_dir,
            "port_scans": port_scans,
            "service_scans": service_scans,
            "heartbeat": heartbeat,
            "timeout": timeout,
            "additional_args": additional_args
        }
        logger.info(f" Starting AutoRecon: {target}")
        result = hexstrike_client.safe_post("api/tools/autorecon", data)
        if result.get("success"):
            logger.info(f" AutoRecon completed for {target}")
        else:
            logger.error(f" AutoRecon failed for {target}")
        return result

    @mcp.tool()
    def enum4linux_ng_advanced(target: str, username: str = "", password: str = "",
                               domain: str = "", shares: bool = True, users: bool = True,
                               groups: bool = True, policy: bool = True,
                               additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Enum4linux-ng 用于 高级 SMB enumeration 使用 增强日志.

        参数:
            target: The 目标 IP address
            username: Username 用于 认证
            password: Password 用于 认证
            domain: 域名 用于 认证
            shares: 说明：Enumerate shares
            users: 说明：Enumerate users
            groups: 说明：Enumerate groups
            policy: 说明：Enumerate policies
            additional_args: 附加 Enum4linux-ng arguments

        返回:
            高级 SMB enumeration 结果
        """
        data = {
            "target": target,
            "username": username,
            "password": password,
            "domain": domain,
            "shares": shares,
            "users": users,
            "groups": groups,
            "policy": policy,
            "additional_args": additional_args
        }
        logger.info(f" Starting Enum4linux-ng: {target}")
        result = hexstrike_client.safe_post("api/tools/enum4linux-ng", data)
        if result.get("success"):
            logger.info(f" Enum4linux-ng completed for {target}")
        else:
            logger.error(f" Enum4linux-ng failed for {target}")
        return result

    @mcp.tool()
    def rpcclient_enumeration(target: str, username: str = "", password: str = "",
                             domain: str = "", commands: str = "enumdomusers;enumdomgroups;querydominfo",
                             additional_args: str = "") -> Dict[str, Any]:
        """
        执行 rpcclient 用于 RPC enumeration 使用 增强日志.

        参数:
            target: The 目标 IP address
            username: Username 用于 认证
            password: Password 用于 认证
            domain: 域名 用于 认证
            commands: Semicolon-separated RPC 命令
            additional_args: 附加 rpcclient arguments

        返回:
            RPC enumeration 结果
        """
        data = {
            "target": target,
            "username": username,
            "password": password,
            "domain": domain,
            "commands": commands,
            "additional_args": additional_args
        }
        logger.info(f" Starting rpcclient: {target}")
        result = hexstrike_client.safe_post("api/tools/rpcclient", data)
        if result.get("success"):
            logger.info(f" rpcclient completed for {target}")
        else:
            logger.error(f" rpcclient failed for {target}")
        return result

    @mcp.tool()
    def nbtscan_netbios(target: str, verbose: bool = False, timeout: int = 2,
                       additional_args: str = "") -> Dict[str, Any]:
        """
        执行 nbtscan 用于 NetBIOS name 扫描 使用 增强日志.

        参数:
            target: The 目标 IP address 或 range
            verbose: 启用 verbose 输出
            timeout: 超时 在 seconds
            additional_args: 附加 nbtscan arguments

        返回:
            NetBIOS name 扫描 结果
        """
        data = {
            "target": target,
            "verbose": verbose,
            "timeout": timeout,
            "additional_args": additional_args
        }
        logger.info(f" Starting nbtscan: {target}")
        result = hexstrike_client.safe_post("api/tools/nbtscan", data)
        if result.get("success"):
            logger.info(f" nbtscan completed for {target}")
        else:
            logger.error(f" nbtscan failed for {target}")
        return result

    @mcp.tool()
    def arp_scan_discovery(target: str = "", interface: str = "", local_network: bool = False,
                          timeout: int = 500, retry: int = 3, additional_args: str = "") -> Dict[str, Any]:
        """
        执行 arp-扫描 用于 网络 发现 使用 增强日志.

        参数:
            target: The 目标 IP range (如果 not using local_network)
            interface: 网络 接口 到 use
            local_network: 扫描 本地 网络
            timeout: 超时 在 milliseconds
            retry: Number 的 retries
            additional_args: 附加 arp-扫描 arguments

        返回:
            网络 发现 结果 via ARP 扫描
        """
        data = {
            "target": target,
            "interface": interface,
            "local_network": local_network,
            "timeout": timeout,
            "retry": retry,
            "additional_args": additional_args
        }
        logger.info(f" Starting arp-scan: {target if target else 'local network'}")
        result = hexstrike_client.safe_post("api/tools/arp-scan", data)
        if result.get("success"):
            logger.info(f" arp-scan completed")
        else:
            logger.error(f" arp-scan failed")
        return result

    @mcp.tool()
    def responder_credential_harvest(interface: str = "eth0", analyze: bool = False,
                                   wpad: bool = True, force_wpad_auth: bool = False,
                                   fingerprint: bool = False, duration: int = 300,
                                   additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Responder 凭据采集，并启用增强日志。

        参数:
            interface: 使用的网络接口
            analyze: 仅分析模式
            wpad: 启用 WPAD 伪代理
            force_wpad_auth: 强制进行 WPAD 认证
            fingerprint: 启用指纹模式
            duration: 运行时长（秒）
            additional_args: 额外的 Responder 参数

        返回:
            凭据采集结果
        """
        data = {
            "interface": interface,
            "analyze": analyze,
            "wpad": wpad,
            "force_wpad_auth": force_wpad_auth,
            "fingerprint": fingerprint,
            "duration": duration,
            "additional_args": additional_args
        }
        logger.info(f" Starting Responder on interface: {interface}")
        result = hexstrike_client.safe_post("api/tools/responder", data)
        if result.get("success"):
            logger.info(f" Responder completed")
        else:
            logger.error(f" Responder failed")
        return result

    @mcp.tool()
    def volatility_analyze(memory_file: str, plugin: str, profile: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Volatility 用于 内存 取证 分析 使用 增强日志.

        参数:
            memory_file: Path 到 内存 dump 文件
            plugin: Volatility plugin 到 use
            profile: 内存 profile 到 use
            additional_args: 附加 Volatility arguments

        返回:
            内存 取证 分析 结果
        """
        data = {
            "memory_file": memory_file,
            "plugin": plugin,
            "profile": profile,
            "additional_args": additional_args
        }
        logger.info(f" Starting Volatility analysis: {plugin}")
        result = hexstrike_client.safe_post("api/tools/volatility", data)
        if result.get("success"):
            logger.info(f" Volatility analysis completed")
        else:
            logger.error(f" Volatility analysis failed")
        return result

    @mcp.tool()
    def msfvenom_generate(payload: str, format_type: str = "", output_file: str = "", encoder: str = "", iterations: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 MSFVenom 用于 载荷 generation 使用 增强日志.

        参数:
            payload: The 载荷 到 生成
            format_type: 输出 format (exe, elf, raw, etc.)
            output_file: 输出 文件 path
            encoder: Encoder 到 use
            iterations: Number 的 encoding iterations
            additional_args: 附加 MSFVenom arguments

        返回:
            载荷 generation 结果
        """
        data = {
            "payload": payload,
            "format": format_type,
            "output_file": output_file,
            "encoder": encoder,
            "iterations": iterations,
            "additional_args": additional_args
        }
        logger.info(f" Starting MSFVenom payload generation: {payload}")
        result = hexstrike_client.safe_post("api/tools/msfvenom", data)
        if result.get("success"):
            logger.info(f" MSFVenom payload generated")
        else:
            logger.error(f" MSFVenom payload generation failed")
        return result

    # ============================================================================
    # 二进制 分析 & 逆向工程 工具
    # ============================================================================

    @mcp.tool()
    def gdb_analyze(binary: str, commands: str = "", script_file: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 GDB 用于 二进制 分析 与 debugging 使用 增强日志.

        参数:
            binary: Path 到 the 二进制 文件
            commands: GDB 命令 到 执行
            script_file: Path 到 GDB script 文件
            additional_args: 附加 GDB arguments

        返回:
            二进制 分析 结果
        """
        data = {
            "binary": binary,
            "commands": commands,
            "script_file": script_file,
            "additional_args": additional_args
        }
        logger.info(f" Starting GDB analysis: {binary}")
        result = hexstrike_client.safe_post("api/tools/gdb", data)
        if result.get("success"):
            logger.info(f" GDB analysis completed for {binary}")
        else:
            logger.error(f" GDB analysis failed for {binary}")
        return result

    @mcp.tool()
    def radare2_analyze(binary: str, commands: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Radare2 用于 二进制 分析 与 逆向工程 使用 增强日志.

        参数:
            binary: Path 到 the 二进制 文件
            commands: Radare2 命令 到 执行
            additional_args: 附加 Radare2 arguments

        返回:
            二进制 分析 结果
        """
        data = {
            "binary": binary,
            "commands": commands,
            "additional_args": additional_args
        }
        logger.info(f" Starting Radare2 analysis: {binary}")
        result = hexstrike_client.safe_post("api/tools/radare2", data)
        if result.get("success"):
            logger.info(f" Radare2 analysis completed for {binary}")
        else:
            logger.error(f" Radare2 analysis failed for {binary}")
        return result

    @mcp.tool()
    def binwalk_analyze(file_path: str, extract: bool = False, additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Binwalk 用于 firmware 与 文件 分析 使用 增强日志.

        参数:
            file_path: Path 到 the 文件 到 分析
            extract: Whether 到 extract discovered 文件
            additional_args: 附加 Binwalk arguments

        返回:
            Firmware 分析 结果
        """
        data = {
            "file_path": file_path,
            "extract": extract,
            "additional_args": additional_args
        }
        logger.info(f" Starting Binwalk analysis: {file_path}")
        result = hexstrike_client.safe_post("api/tools/binwalk", data)
        if result.get("success"):
            logger.info(f" Binwalk analysis completed for {file_path}")
        else:
            logger.error(f" Binwalk analysis failed for {file_path}")
        return result

    @mcp.tool()
    def ropgadget_search(binary: str, gadget_type: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        Search 用于 ROP gadgets 在 a 二进制 using ROPgadget 使用 增强日志.

        参数:
            binary: Path 到 the 二进制 文件
            gadget_type: 类型 的 gadgets 到 search 用于
            additional_args: 附加 ROPgadget arguments

        返回:
            ROP gadget search 结果
        """
        data = {
            "binary": binary,
            "gadget_type": gadget_type,
            "additional_args": additional_args
        }
        logger.info(f" Starting ROPgadget search: {binary}")
        result = hexstrike_client.safe_post("api/tools/ropgadget", data)
        if result.get("success"):
            logger.info(f" ROPgadget search completed for {binary}")
        else:
            logger.error(f" ROPgadget search failed for {binary}")
        return result

    @mcp.tool()
    def checksec_analyze(binary: str) -> Dict[str, Any]:
        """
        检查 安全 features 的 a 二进制 使用 增强日志.

        参数:
            binary: Path 到 the 二进制 文件

        返回:
            安全 features 分析 结果
        """
        data = {
            "binary": binary
        }
        logger.info(f" Starting Checksec analysis: {binary}")
        result = hexstrike_client.safe_post("api/tools/checksec", data)
        if result.get("success"):
            logger.info(f" Checksec analysis completed for {binary}")
        else:
            logger.error(f" Checksec analysis failed for {binary}")
        return result

    @mcp.tool()
    def xxd_hexdump(file_path: str, offset: str = "0", length: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        创建 a hex dump 的 a 文件 using xxd 使用 增强日志.

        参数:
            file_path: Path 到 the 文件
            offset: Offset 到 start reading 来自
            length: Number 的 bytes 到 read
            additional_args: 附加 xxd arguments

        返回:
            Hex dump 结果
        """
        data = {
            "file_path": file_path,
            "offset": offset,
            "length": length,
            "additional_args": additional_args
        }
        logger.info(f" Starting XXD hex dump: {file_path}")
        result = hexstrike_client.safe_post("api/tools/xxd", data)
        if result.get("success"):
            logger.info(f" XXD hex dump completed for {file_path}")
        else:
            logger.error(f" XXD hex dump failed for {file_path}")
        return result

    @mcp.tool()
    def strings_extract(file_path: str, min_len: int = 4, additional_args: str = "") -> Dict[str, Any]:
        """
        Extract strings 来自 a 二进制 文件 使用 增强日志.

        参数:
            file_path: Path 到 the 文件
            min_len: 说明：Minimum string length
            additional_args: 附加 strings arguments

        返回:
            String extraction 结果
        """
        data = {
            "file_path": file_path,
            "min_len": min_len,
            "additional_args": additional_args
        }
        logger.info(f" Starting Strings extraction: {file_path}")
        result = hexstrike_client.safe_post("api/tools/strings", data)
        if result.get("success"):
            logger.info(f" Strings extraction completed for {file_path}")
        else:
            logger.error(f" Strings extraction failed for {file_path}")
        return result

    @mcp.tool()
    def objdump_analyze(binary: str, disassemble: bool = True, additional_args: str = "") -> Dict[str, Any]:
        """
        分析 a 二进制 using objdump 使用 增强日志.

        参数:
            binary: Path 到 the 二进制 文件
            disassemble: Whether 到 disassemble the 二进制
            additional_args: 附加 objdump arguments

        返回:
            二进制 分析 结果
        """
        data = {
            "binary": binary,
            "disassemble": disassemble,
            "additional_args": additional_args
        }
        logger.info(f" Starting Objdump analysis: {binary}")
        result = hexstrike_client.safe_post("api/tools/objdump", data)
        if result.get("success"):
            logger.info(f" Objdump analysis completed for {binary}")
        else:
            logger.error(f" Objdump analysis failed for {binary}")
        return result

    # ============================================================================
    # 增强 二进制 分析 与 EXPLOITATION 框架 (v6.0)
    # ============================================================================

    @mcp.tool()
    def ghidra_analysis(binary: str, project_name: str = "hexstrike_analysis",
                       script_file: str = "", analysis_timeout: int = 300,
                       output_format: str = "xml", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Ghidra 用于 高级 二进制 分析 与 逆向工程.

        参数:
            binary: Path 到 the 二进制 文件
            project_name: 说明：Ghidra project name
            script_file: Custom Ghidra script 到 run
            analysis_timeout: 分析 超时 在 seconds
            output_format: 输出 format (xml, JSON)
            additional_args: 附加 Ghidra arguments

        返回:
            高级 二进制 分析 结果 来自 Ghidra
        """
        data = {
            "binary": binary,
            "project_name": project_name,
            "script_file": script_file,
            "analysis_timeout": analysis_timeout,
            "output_format": output_format,
            "additional_args": additional_args
        }
        logger.info(f" Starting Ghidra analysis: {binary}")
        result = hexstrike_client.safe_post("api/tools/ghidra", data)
        if result.get("success"):
            logger.info(f" Ghidra analysis completed for {binary}")
        else:
            logger.error(f" Ghidra analysis failed for {binary}")
        return result

    @mcp.tool()
    def pwntools_exploit(script_content: str = "", target_binary: str = "",
                        target_host: str = "", target_port: int = 0,
                        exploit_type: str = "local", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Pwntools 用于 利用 development 与 automation.

        参数:
            script_content: 说明：Python script content using pwntools
            target_binary: 本地 二进制 到 利用
            target_host: Remote host 到 connect 到
            target_port: Remote port 到 connect 到
            exploit_type: 类型 的 利用 (本地, remote, format_string, rop)
            additional_args: 附加 arguments

        返回:
            利用 execution 结果
        """
        data = {
            "script_content": script_content,
            "target_binary": target_binary,
            "target_host": target_host,
            "target_port": target_port,
            "exploit_type": exploit_type,
            "additional_args": additional_args
        }
        logger.info(f" Starting Pwntools exploit: {exploit_type}")
        result = hexstrike_client.safe_post("api/tools/pwntools", data)
        if result.get("success"):
            logger.info(f" Pwntools exploit completed")
        else:
            logger.error(f" Pwntools exploit failed")
        return result

    @mcp.tool()
    def one_gadget_search(libc_path: str, level: int = 1, additional_args: str = "") -> Dict[str, Any]:
        """
        执行 one_gadget 到 find one-shot RCE gadgets 在 libc.

        参数:
            libc_path: Path 到 libc 二进制
            level: 说明：Constraint level (0, 1, 2)
            additional_args: 附加 one_gadget arguments

        返回:
            One-shot RCE gadget search 结果
        """
        data = {
            "libc_path": libc_path,
            "level": level,
            "additional_args": additional_args
        }
        logger.info(f" Starting one_gadget analysis: {libc_path}")
        result = hexstrike_client.safe_post("api/tools/one-gadget", data)
        if result.get("success"):
            logger.info(f" one_gadget analysis completed")
        else:
            logger.error(f" one_gadget analysis failed")
        return result

    @mcp.tool()
    def libc_database_lookup(action: str = "find", symbols: str = "",
                            libc_id: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 libc-database 用于 libc identification 与 offset lookup.

        参数:
            action: Action 到 perform (find, dump, download)
            symbols: Symbols 使用 offsets 用于 find action (format: "symbol1:offset1 symbol2:offset2")
            libc_id: Libc ID 用于 dump/download actions
            additional_args: 附加 arguments

        返回:
            Libc database lookup 结果
        """
        data = {
            "action": action,
            "symbols": symbols,
            "libc_id": libc_id,
            "additional_args": additional_args
        }
        logger.info(f" Starting libc-database {action}: {symbols or libc_id}")
        result = hexstrike_client.safe_post("api/tools/libc-database", data)
        if result.get("success"):
            logger.info(f" libc-database {action} completed")
        else:
            logger.error(f" libc-database {action} failed")
        return result

    @mcp.tool()
    def gdb_peda_debug(binary: str = "", commands: str = "", attach_pid: int = 0,
                      core_file: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 GDB 使用 PEDA 用于 增强 debugging 与 exploitation.

        参数:
            binary: 二进制 到 debug
            commands: GDB 命令 到 执行
            attach_pid: 进程 ID 到 attach 到
            core_file: Core dump 文件 到 分析
            additional_args: 附加 GDB arguments

        返回:
            增强 debugging 结果 使用 PEDA
        """
        data = {
            "binary": binary,
            "commands": commands,
            "attach_pid": attach_pid,
            "core_file": core_file,
            "additional_args": additional_args
        }
        logger.info(f" Starting GDB-PEDA analysis: {binary or f'PID {attach_pid}' or core_file}")
        result = hexstrike_client.safe_post("api/tools/gdb-peda", data)
        if result.get("success"):
            logger.info(f" GDB-PEDA analysis completed")
        else:
            logger.error(f" GDB-PEDA analysis failed")
        return result

    @mcp.tool()
    def angr_symbolic_execution(binary: str, script_content: str = "",
                               find_address: str = "", avoid_addresses: str = "",
                               analysis_type: str = "symbolic", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 angr 用于 symbolic execution 与 二进制 分析.

        参数:
            binary: 二进制 到 分析
            script_content: 说明：Custom angr script content
            find_address: Address 到 find during symbolic execution
            avoid_addresses: Comma-separated addresses 到 avoid
            analysis_type: 类型 的 分析 (symbolic, cfg, static)
            additional_args: 附加 arguments

        返回:
            Symbolic execution 与 二进制 分析 结果
        """
        data = {
            "binary": binary,
            "script_content": script_content,
            "find_address": find_address,
            "avoid_addresses": avoid_addresses,
            "analysis_type": analysis_type,
            "additional_args": additional_args
        }
        logger.info(f" Starting angr analysis: {binary}")
        result = hexstrike_client.safe_post("api/tools/angr", data)
        if result.get("success"):
            logger.info(f" angr analysis completed")
        else:
            logger.error(f" angr analysis failed")
        return result

    @mcp.tool()
    def ropper_gadget_search(binary: str, gadget_type: str = "rop", quality: int = 1,
                            arch: str = "", search_string: str = "",
                            additional_args: str = "") -> Dict[str, Any]:
        """
        执行 ropper 用于 高级 ROP/JOP gadget searching.

        参数:
            binary: 二进制 到 search 用于 gadgets
            gadget_type: 类型 的 gadgets (rop, jop, sys, 全部)
            quality: 说明：Gadget quality level (1-5)
            arch: 目标 architecture (x86, x86_64, arm, etc.)
            search_string: Specific gadget pattern 到 search 用于
            additional_args: 附加 ropper arguments

        返回:
            高级 ROP/JOP gadget search 结果
        """
        data = {
            "binary": binary,
            "gadget_type": gadget_type,
            "quality": quality,
            "arch": arch,
            "search_string": search_string,
            "additional_args": additional_args
        }
        logger.info(f" Starting ropper analysis: {binary}")
        result = hexstrike_client.safe_post("api/tools/ropper", data)
        if result.get("success"):
            logger.info(f" ropper analysis completed")
        else:
            logger.error(f" ropper analysis failed")
        return result

    @mcp.tool()
    def pwninit_setup(binary: str, libc: str = "", ld: str = "",
                     template_type: str = "python", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 pwninit 用于 CTF 二进制 exploitation 初始化.

        参数:
            binary: 二进制 文件 到 设置 up
            libc: Libc 文件 到 use
            ld: Loader 文件 到 use
            template_type: Template 类型 (python, c)
            additional_args: 附加 pwninit arguments

        返回:
            CTF 二进制 exploitation 初始化 结果
        """
        data = {
            "binary": binary,
            "libc": libc,
            "ld": ld,
            "template_type": template_type,
            "additional_args": additional_args
        }
        logger.info(f" Starting pwninit setup: {binary}")
        result = hexstrike_client.safe_post("api/tools/pwninit", data)
        if result.get("success"):
            logger.info(f" pwninit setup completed")
        else:
            logger.error(f" pwninit setup failed")
        return result

    @mcp.tool()
    def feroxbuster_scan(url: str, wordlist: str = "/usr/share/wordlists/dirb/common.txt", threads: int = 10, additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Feroxbuster 用于 recursive content 发现 使用 增强日志.

        参数:
            url: The 目标 URL
            wordlist: Wordlist 文件 到 use
            threads: Number 的 threads
            additional_args: 附加 Feroxbuster arguments

        返回:
            Content 发现 结果
        """
        data = {
            "url": url,
            "wordlist": wordlist,
            "threads": threads,
            "additional_args": additional_args
        }
        logger.info(f" Starting Feroxbuster scan: {url}")
        result = hexstrike_client.safe_post("api/tools/feroxbuster", data)
        if result.get("success"):
            logger.info(f" Feroxbuster scan completed for {url}")
        else:
            logger.error(f" Feroxbuster scan failed for {url}")
        return result

    @mcp.tool()
    def dotdotpwn_scan(target: str, module: str = "http", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 DotDotPwn 用于 目录 traversal 测试 使用 增强日志.

        参数:
            target: The 目标 hostname 或 IP
            module: Module 到 use (HTTP, ftp, tftp, etc.)
            additional_args: 附加 DotDotPwn arguments

        返回:
            目录 traversal 测试 结果
        """
        data = {
            "target": target,
            "module": module,
            "additional_args": additional_args
        }
        logger.info(f" Starting DotDotPwn scan: {target}")
        result = hexstrike_client.safe_post("api/tools/dotdotpwn", data)
        if result.get("success"):
            logger.info(f" DotDotPwn scan completed for {target}")
        else:
            logger.error(f" DotDotPwn scan failed for {target}")
        return result

    @mcp.tool()
    def xsser_scan(url: str, params: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 XSSer 用于 XSS 漏洞 测试 使用 增强日志.

        参数:
            url: The 目标 URL
            params: 参数 到 测试
            additional_args: 附加 XSSer arguments

        返回:
            XSS 漏洞 测试 结果
        """
        data = {
            "url": url,
            "params": params,
            "additional_args": additional_args
        }
        logger.info(f" Starting XSSer scan: {url}")
        result = hexstrike_client.safe_post("api/tools/xsser", data)
        if result.get("success"):
            logger.info(f" XSSer scan completed for {url}")
        else:
            logger.error(f" XSSer scan failed for {url}")
        return result

    @mcp.tool()
    def wfuzz_scan(url: str, wordlist: str = "/usr/share/wordlists/dirb/common.txt", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Wfuzz 用于 web application fuzzing 使用 增强日志.

        参数:
            url: The 目标 URL (use FUZZ where you want 到 inject payloads)
            wordlist: Wordlist 文件 到 use
            additional_args: 附加 Wfuzz arguments

        返回:
            Web application fuzzing 结果
        """
        data = {
            "url": url,
            "wordlist": wordlist,
            "additional_args": additional_args
        }
        logger.info(f" Starting Wfuzz scan: {url}")
        result = hexstrike_client.safe_post("api/tools/wfuzz", data)
        if result.get("success"):
            logger.info(f" Wfuzz scan completed for {url}")
        else:
            logger.error(f" Wfuzz scan failed for {url}")
        return result

    # ============================================================================
    # 增强 WEB APPLICATION 安全 工具 (v6.0)
    # ============================================================================

    @mcp.tool()
    def dirsearch_scan(url: str, extensions: str = "php,html,js,txt,xml,json",
                      wordlist: str = "/usr/share/wordlists/dirsearch/common.txt",
                      threads: int = 30, recursive: bool = False, additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Dirsearch 用于 高级 目录 与 文件 发现 使用 增强日志.

        参数:
            url: The 目标 URL
            extensions: 文件 extensions 到 search 用于
            wordlist: Wordlist 文件 到 use
            threads: Number 的 threads 到 use
            recursive: 启用 recursive 扫描
            additional_args: 附加 Dirsearch arguments

        返回:
            高级 目录 发现 结果
        """
        data = {
            "url": url,
            "extensions": extensions,
            "wordlist": wordlist,
            "threads": threads,
            "recursive": recursive,
            "additional_args": additional_args
        }
        logger.info(f" Starting Dirsearch scan: {url}")
        result = hexstrike_client.safe_post("api/tools/dirsearch", data)
        if result.get("success"):
            logger.info(f" Dirsearch scan completed for {url}")
        else:
            logger.error(f" Dirsearch scan failed for {url}")
        return result

    @mcp.tool()
    def katana_crawl(url: str, depth: int = 3, js_crawl: bool = True,
                    form_extraction: bool = True, output_format: str = "json",
                    additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Katana 用于 next-generation crawling 与 spidering 使用 增强日志.

        参数:
            url: The 目标 URL 到 crawl
            depth: 说明：Crawling depth
            js_crawl: 启用 JavaScript crawling
            form_extraction: 启用 form extraction
            output_format: 输出 format (JSON, txt)
            additional_args: 附加 Katana arguments

        返回:
            高级 web crawling 结果 使用 端点 与 forms
        """
        data = {
            "url": url,
            "depth": depth,
            "js_crawl": js_crawl,
            "form_extraction": form_extraction,
            "output_format": output_format,
            "additional_args": additional_args
        }
        logger.info(f"  Starting Katana crawl: {url}")
        result = hexstrike_client.safe_post("api/tools/katana", data)
        if result.get("success"):
            logger.info(f" Katana crawl completed for {url}")
        else:
            logger.error(f" Katana crawl failed for {url}")
        return result

    @mcp.tool()
    def gau_discovery(domain: str, providers: str = "wayback,commoncrawl,otx,urlscan",
                     include_subs: bool = True, blacklist: str = "png,jpg,gif,jpeg,swf,woff,svg,pdf,css,ico",
                     additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Gau (获取 全部 URLs) 用于 URL 发现 来自 multiple sources 使用 增强日志.

        参数:
            domain: The 目标 域名
            providers: Data providers 到 use
            include_subs: 说明：Include subdomains
            blacklist: 文件 extensions 到 blacklist
            additional_args: 附加 Gau arguments

        返回:
            综合 URL 发现 结果 来自 multiple sources
        """
        data = {
            "domain": domain,
            "providers": providers,
            "include_subs": include_subs,
            "blacklist": blacklist,
            "additional_args": additional_args
        }
        logger.info(f" Starting Gau URL discovery: {domain}")
        result = hexstrike_client.safe_post("api/tools/gau", data)
        if result.get("success"):
            logger.info(f" Gau URL discovery completed for {domain}")
        else:
            logger.error(f" Gau URL discovery failed for {domain}")
        return result

    @mcp.tool()
    def waybackurls_discovery(domain: str, get_versions: bool = False,
                             no_subs: bool = False, additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Waybackurls 用于 historical URL 发现 使用 增强日志.

        参数:
            domain: The 目标 域名
            get_versions: 获取 全部 versions 的 URLs
            no_subs: 说明：Don't include subdomains
            additional_args: 附加 Waybackurls arguments

        返回:
            Historical URL 发现 结果 来自 Wayback Machine
        """
        data = {
            "domain": domain,
            "get_versions": get_versions,
            "no_subs": no_subs,
            "additional_args": additional_args
        }
        logger.info(f"  Starting Waybackurls discovery: {domain}")
        result = hexstrike_client.safe_post("api/tools/waybackurls", data)
        if result.get("success"):
            logger.info(f" Waybackurls discovery completed for {domain}")
        else:
            logger.error(f" Waybackurls discovery failed for {domain}")
        return result

    @mcp.tool()
    def arjun_parameter_discovery(url: str, method: str = "GET", wordlist: str = "",
                                 delay: int = 0, threads: int = 25, stable: bool = False,
                                 additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Arjun 用于 HTTP 参数 发现 使用 增强日志.

        参数:
            url: The 目标 URL
            method: HTTP method 到 use
            wordlist: Custom wordlist 文件
            delay: Delay between 请求
            threads: Number 的 threads
            stable: Use stable 模式
            additional_args: 附加 Arjun arguments

        返回:
            HTTP 参数 发现 结果
        """
        data = {
            "url": url,
            "method": method,
            "wordlist": wordlist,
            "delay": delay,
            "threads": threads,
            "stable": stable,
            "additional_args": additional_args
        }
        logger.info(f" Starting Arjun parameter discovery: {url}")
        result = hexstrike_client.safe_post("api/tools/arjun", data)
        if result.get("success"):
            logger.info(f" Arjun parameter discovery completed for {url}")
        else:
            logger.error(f" Arjun parameter discovery failed for {url}")
        return result

    @mcp.tool()
    def paramspider_mining(domain: str, level: int = 2,
                          exclude: str = "png,jpg,gif,jpeg,swf,woff,svg,pdf,css,ico",
                          output: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 ParamSpider 用于 参数 mining 来自 web archives 使用 增强日志.

        参数:
            domain: The 目标 域名
            level: 说明：Mining level depth
            exclude: 文件 extensions 到 exclude
            output: 输出 文件 path
            additional_args: 附加 ParamSpider arguments

        返回:
            参数 mining 结果 来自 web archives
        """
        data = {
            "domain": domain,
            "level": level,
            "exclude": exclude,
            "output": output,
            "additional_args": additional_args
        }
        logger.info(f"  Starting ParamSpider mining: {domain}")
        result = hexstrike_client.safe_post("api/tools/paramspider", data)
        if result.get("success"):
            logger.info(f" ParamSpider mining completed for {domain}")
        else:
            logger.error(f" ParamSpider mining failed for {domain}")
        return result

    @mcp.tool()
    def x8_parameter_discovery(url: str, wordlist: str = "/usr/share/wordlists/x8/params.txt",
                              method: str = "GET", body: str = "", headers: str = "",
                              additional_args: str = "") -> Dict[str, Any]:
        """
        执行 x8 用于 hidden 参数 发现 使用 增强日志.

        参数:
            url: The 目标 URL
            wordlist: 参数 wordlist
            method: 说明：HTTP method
            body: 请求 body
            headers: Custom 请求头
            additional_args: 附加 x8 arguments

        返回:
            Hidden 参数 发现 结果
        """
        data = {
            "url": url,
            "wordlist": wordlist,
            "method": method,
            "body": body,
            "headers": headers,
            "additional_args": additional_args
        }
        logger.info(f" Starting x8 parameter discovery: {url}")
        result = hexstrike_client.safe_post("api/tools/x8", data)
        if result.get("success"):
            logger.info(f" x8 parameter discovery completed for {url}")
        else:
            logger.error(f" x8 parameter discovery failed for {url}")
        return result

    @mcp.tool()
    def jaeles_vulnerability_scan(url: str, signatures: str = "", config: str = "",
                                 threads: int = 20, timeout: int = 20,
                                 additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Jaeles 用于 高级 漏洞 扫描 使用 custom signatures.

        参数:
            url: The 目标 URL
            signatures: 说明：Custom signature path
            config: 配置 文件
            threads: Number 的 threads
            timeout: 请求 超时
            additional_args: 附加 Jaeles arguments

        返回:
            高级 漏洞 扫描 结果 使用 custom signatures
        """
        data = {
            "url": url,
            "signatures": signatures,
            "config": config,
            "threads": threads,
            "timeout": timeout,
            "additional_args": additional_args
        }
        logger.info(f" Starting Jaeles vulnerability scan: {url}")
        result = hexstrike_client.safe_post("api/tools/jaeles", data)
        if result.get("success"):
            logger.info(f" Jaeles vulnerability scan completed for {url}")
        else:
            logger.error(f" Jaeles vulnerability scan failed for {url}")
        return result

    @mcp.tool()
    def dalfox_xss_scan(url: str, pipe_mode: bool = False, blind: bool = False,
                       mining_dom: bool = True, mining_dict: bool = True,
                       custom_payload: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Dalfox 用于 高级 XSS 漏洞 扫描 使用 增强日志.

        参数:
            url: The 目标 URL
            pipe_mode: Use pipe 模式 用于 输入
            blind: 启用 blind XSS 测试
            mining_dom: 启用 DOM mining
            mining_dict: 启用 dictionary mining
            custom_payload: Custom XSS 载荷
            additional_args: 附加 Dalfox arguments

        返回:
            高级 XSS 漏洞 扫描 结果
        """
        data = {
            "url": url,
            "pipe_mode": pipe_mode,
            "blind": blind,
            "mining_dom": mining_dom,
            "mining_dict": mining_dict,
            "custom_payload": custom_payload,
            "additional_args": additional_args
        }
        logger.info(f" Starting Dalfox XSS scan: {url if url else 'pipe mode'}")
        result = hexstrike_client.safe_post("api/tools/dalfox", data)
        if result.get("success"):
            logger.info(f" Dalfox XSS scan completed")
        else:
            logger.error(f" Dalfox XSS scan failed")
        return result

    @mcp.tool()
    def httpx_probe(target: str, probe: bool = True, tech_detect: bool = False,
                   status_code: bool = False, content_length: bool = False,
                   title: bool = False, web_server: bool = False, threads: int = 50,
                   additional_args: str = "") -> Dict[str, Any]:
        """
        执行 httpx 用于 fast HTTP probing 与 technology detection.

        参数:
            target: 目标 文件 或 single URL
            probe: 启用 probing
            tech_detect: 启用 technology detection
            status_code: Show 状态 codes
            content_length: 说明：Show content length
            title: 说明：Show page titles
            web_server: Show web 服务端
            threads: Number 的 threads
            additional_args: 附加 httpx arguments

        返回:
            Fast HTTP probing 结果 使用 technology detection
        """
        data = {
            "target": target,
            "probe": probe,
            "tech_detect": tech_detect,
            "status_code": status_code,
            "content_length": content_length,
            "title": title,
            "web_server": web_server,
            "threads": threads,
            "additional_args": additional_args
        }
        logger.info(f" Starting httpx probe: {target}")
        result = hexstrike_client.safe_post("api/tools/httpx", data)
        if result.get("success"):
            logger.info(f" httpx probe completed for {target}")
        else:
            logger.error(f" httpx probe failed for {target}")
        return result

    @mcp.tool()
    def anew_data_processing(input_data: str, output_file: str = "",
                            additional_args: str = "") -> Dict[str, Any]:
        """
        执行 anew 用于 appending new lines 到 文件 (useful 用于 data processing).

        参数:
            input_data: 输入 data 到 进程
            output_file: 输出 文件 path
            additional_args: 附加 anew arguments

        返回:
            Data processing 结果 使用 unique line filtering
        """
        data = {
            "input_data": input_data,
            "output_file": output_file,
            "additional_args": additional_args
        }
        logger.info(" Starting anew data processing")
        result = hexstrike_client.safe_post("api/tools/anew", data)
        if result.get("success"):
            logger.info(" anew data processing completed")
        else:
            logger.error(" anew data processing failed")
        return result

    @mcp.tool()
    def qsreplace_parameter_replacement(urls: str, replacement: str = "FUZZ",
                                       additional_args: str = "") -> Dict[str, Any]:
        """
        执行 qsreplace 用于 query string 参数 replacement.

        参数:
            urls: URLs 到 进程
            replacement: Replacement string 用于 参数
            additional_args: 附加 qsreplace arguments

        返回:
            参数 replacement 结果 用于 fuzzing
        """
        data = {
            "urls": urls,
            "replacement": replacement,
            "additional_args": additional_args
        }
        logger.info(" Starting qsreplace parameter replacement")
        result = hexstrike_client.safe_post("api/tools/qsreplace", data)
        if result.get("success"):
            logger.info(" qsreplace parameter replacement completed")
        else:
            logger.error(" qsreplace parameter replacement failed")
        return result

    @mcp.tool()
    def uro_url_filtering(urls: str, whitelist: str = "", blacklist: str = "",
                         additional_args: str = "") -> Dict[str, Any]:
        """
        执行 uro 用于 filtering out similar URLs.

        参数:
            urls: URLs 到 filter
            whitelist: 说明：Whitelist patterns
            blacklist: 说明：Blacklist patterns
            additional_args: 附加 uro arguments

        返回:
            Filtered URL 结果 使用 duplicates removed
        """
        data = {
            "urls": urls,
            "whitelist": whitelist,
            "blacklist": blacklist,
            "additional_args": additional_args
        }
        logger.info(" Starting uro URL filtering")
        result = hexstrike_client.safe_post("api/tools/uro", data)
        if result.get("success"):
            logger.info(" uro URL filtering completed")
        else:
            logger.error(" uro URL filtering failed")
        return result

    # ============================================================================
    # AI-POWERED 载荷 GENERATION (v5.0 ENHANCEMENT)
    # ============================================================================

    @mcp.tool()
    def ai_generate_payload(attack_type: str, complexity: str = "basic", technology: str = "", url: str = "") -> Dict[str, Any]:
        """
        生成 AI-powered contextual payloads 用于 安全 测试.

        参数:
            attack_type: 类型 的 attack (xss, sqli, lfi, cmd_injection, ssti, xxe)
            complexity: Complexity level (基础, 高级, bypass)
            technology: 目标 technology (php, asp, jsp, python, nodejs)
            url: 目标 URL 用于 context

        返回:
            Contextual payloads 使用 risk assessment 与 测试 cases
        """
        data = {
            "attack_type": attack_type,
            "complexity": complexity,
            "technology": technology,
            "url": url
        }
        logger.info(f" Generating AI payloads for {attack_type} attack")
        result = hexstrike_client.safe_post("api/ai/generate_payload", data)

        if result.get("success"):
            payload_data = result.get("ai_payload_generation", {})
            count = payload_data.get("payload_count", 0)
            logger.info(f" Generated {count} contextual {attack_type} payloads")

            # Log some 示例 payloads 用于 user awareness
            payloads = payload_data.get("payloads", [])
            if payloads:
                logger.info(" Sample payloads generated:")
                for i, payload_info in enumerate(payloads[:3]):  # Show 第一 3
                    risk = payload_info.get("risk_level", "UNKNOWN")
                    context = payload_info.get("context", "basic")
                    logger.info(f"   ├─ [{risk}] {context}: {payload_info['payload'][:50]}...")
        else:
            logger.error(" AI payload generation failed")

        return result

    @mcp.tool()
    def ai_test_payload(payload: str, target_url: str, method: str = "GET") -> Dict[str, Any]:
        """
        测试 generated 载荷 against 目标 使用 AI 分析.

        参数:
            payload: The 载荷 到 测试
            target_url: 目标 URL 到 测试 against
            method: HTTP method (获取, POST)

        返回:
            测试 结果 使用 AI 分析 与 漏洞 assessment
        """
        data = {
            "payload": payload,
            "target_url": target_url,
            "method": method
        }
        logger.info(f" Testing AI payload against {target_url}")
        result = hexstrike_client.safe_post("api/ai/test_payload", data)

        if result.get("success"):
            analysis = result.get("ai_analysis", {})
            potential_vuln = analysis.get("potential_vulnerability", False)
            logger.info(f" Payload test completed | Vulnerability detected: {potential_vuln}")

            if potential_vuln:
                logger.warning("  Potential vulnerability found! Review the response carefully.")
            else:
                logger.info(" No obvious vulnerability indicators detected")
        else:
            logger.error(" Payload testing failed")

        return result

    @mcp.tool()
    def ai_generate_attack_suite(target_url: str, attack_types: str = "xss,sqli,lfi") -> Dict[str, Any]:
        """
        生成 综合 attack suite 使用 multiple 载荷 types.

        参数:
            target_url: 目标 URL 用于 测试
            attack_types: Comma-separated 列出 的 attack types

        返回:
            综合 attack suite 使用 multiple 载荷 types
        """
        attack_list = [attack.strip() for attack in attack_types.split(",")]
        results = {
            "target_url": target_url,
            "attack_types": attack_list,
            "payload_suites": {},
            "summary": {
                "total_payloads": 0,
                "high_risk_payloads": 0,
                "test_cases": 0
            }
        }

        logger.info(f" Generating comprehensive attack suite for {target_url}")
        logger.info(f" Attack types: {', '.join(attack_list)}")

        for attack_type in attack_list:
            logger.info(f" Generating {attack_type} payloads...")

            # 生成 payloads 用于 this attack 类型
            payload_result = self.ai_generate_payload(attack_type, "advanced", "", target_url)

            if payload_result.get("success"):
                payload_data = payload_result.get("ai_payload_generation", {})
                results["payload_suites"][attack_type] = payload_data

                # 更新 summary
                results["summary"]["total_payloads"] += payload_data.get("payload_count", 0)
                results["summary"]["test_cases"] += len(payload_data.get("test_cases", []))

                # 说明：Count high-risk payloads
                for payload_info in payload_data.get("payloads", []):
                    if payload_info.get("risk_level") == "HIGH":
                        results["summary"]["high_risk_payloads"] += 1

        logger.info(f" Attack suite generated:")
        logger.info(f"   ├─ Total payloads: {results['summary']['total_payloads']}")
        logger.info(f"   ├─ High-risk payloads: {results['summary']['high_risk_payloads']}")
        logger.info(f"   └─ Test cases: {results['summary']['test_cases']}")

        return {
            "success": True,
            "attack_suite": results,
            "timestamp": time.time()
        }

    # ============================================================================
    # 高级 API 测试 工具 (v5.0 ENHANCEMENT)
    # ============================================================================

    @mcp.tool()
    def api_fuzzer(base_url: str, endpoints: str = "", methods: str = "GET,POST,PUT,DELETE", wordlist: str = "/usr/share/wordlists/api/api-endpoints.txt") -> Dict[str, Any]:
        """
        高级 API 端点 fuzzing 使用 智能 参数 发现.

        参数:
            base_url: Base URL 的 the API
            endpoints: Comma-separated 列出 的 specific 端点 到 测试
            methods: HTTP methods 到 测试 (comma-separated)
            wordlist: Wordlist 用于 端点 发现

        返回:
            API fuzzing 结果 使用 端点 发现 与 漏洞 assessment
        """
        data = {
            "base_url": base_url,
            "endpoints": [e.strip() for e in endpoints.split(",") if e.strip()] if endpoints else [],
            "methods": [m.strip() for m in methods.split(",")],
            "wordlist": wordlist
        }

        logger.info(f" Starting API fuzzing: {base_url}")
        result = hexstrike_client.safe_post("api/tools/api_fuzzer", data)

        if result.get("success"):
            fuzzing_type = result.get("fuzzing_type", "unknown")
            if fuzzing_type == "endpoint_testing":
                endpoint_count = len(result.get("results", []))
                logger.info(f" API endpoint testing completed: {endpoint_count} endpoints tested")
            else:
                logger.info(f" API endpoint discovery completed")
        else:
            logger.error(" API fuzzing failed")

        return result

    @mcp.tool()
    def graphql_scanner(endpoint: str, introspection: bool = True, query_depth: int = 10, test_mutations: bool = True) -> Dict[str, Any]:
        """
        高级 GraphQL 安全 扫描 与 introspection.

        参数:
            endpoint: GraphQL 端点 URL
            introspection: 测试 introspection queries
            query_depth: Maximum query depth 到 测试
            test_mutations: 测试 mutation 操作

        返回:
            GraphQL 安全 扫描 结果 使用 漏洞 assessment
        """
        data = {
            "endpoint": endpoint,
            "introspection": introspection,
            "query_depth": query_depth,
            "test_mutations": test_mutations
        }

        logger.info(f" Starting GraphQL security scan: {endpoint}")
        result = hexstrike_client.safe_post("api/tools/graphql_scanner", data)

        if result.get("success"):
            scan_results = result.get("graphql_scan_results", {})
            vuln_count = len(scan_results.get("vulnerabilities", []))
            tests_count = len(scan_results.get("tests_performed", []))

            logger.info(f" GraphQL scan completed: {tests_count} tests, {vuln_count} vulnerabilities")

            if vuln_count > 0:
                logger.warning(f"  Found {vuln_count} GraphQL vulnerabilities!")
                for vuln in scan_results.get("vulnerabilities", [])[:3]:  # Show 第一 3
                    severity = vuln.get("severity", "UNKNOWN")
                    vuln_type = vuln.get("type", "unknown")
                    logger.warning(f"   ├─ [{severity}] {vuln_type}")
        else:
            logger.error(" GraphQL scanning failed")

        return result

    @mcp.tool()
    def jwt_analyzer(jwt_token: str, target_url: str = "") -> Dict[str, Any]:
        """
        高级 JWT token 分析 与 漏洞 测试.

        参数:
            jwt_token: JWT token 到 分析
            target_url: Optional 目标 URL 用于 测试 token manipulation

        返回:
            JWT 分析 结果 使用 漏洞 assessment 与 attack vectors
        """
        data = {
            "jwt_token": jwt_token,
            "target_url": target_url
        }

        logger.info(f" Starting JWT security analysis")
        result = hexstrike_client.safe_post("api/tools/jwt_analyzer", data)

        if result.get("success"):
            analysis = result.get("jwt_analysis_results", {})
            vuln_count = len(analysis.get("vulnerabilities", []))
            algorithm = analysis.get("token_info", {}).get("algorithm", "unknown")

            logger.info(f" JWT analysis completed: {vuln_count} vulnerabilities found")
            logger.info(f" Token algorithm: {algorithm}")

            if vuln_count > 0:
                logger.warning(f"  Found {vuln_count} JWT vulnerabilities!")
                for vuln in analysis.get("vulnerabilities", [])[:3]:  # Show 第一 3
                    severity = vuln.get("severity", "UNKNOWN")
                    vuln_type = vuln.get("type", "unknown")
                    logger.warning(f"   ├─ [{severity}] {vuln_type}")
        else:
            logger.error(" JWT analysis failed")

        return result

    @mcp.tool()
    def api_schema_analyzer(schema_url: str, schema_type: str = "openapi") -> Dict[str, Any]:
        """
        分析 API schemas 与 identify potential 安全 issues.

        参数:
            schema_url: URL 到 the API schema (OpenAPI/Swagger/GraphQL)
            schema_type: 类型 的 schema (openapi, swagger, graphql)

        返回:
            Schema 分析 结果 使用 安全 issues 与 recommendations
        """
        data = {
            "schema_url": schema_url,
            "schema_type": schema_type
        }

        logger.info(f" Starting API schema analysis: {schema_url}")
        result = hexstrike_client.safe_post("api/tools/api_schema_analyzer", data)

        if result.get("success"):
            analysis = result.get("schema_analysis_results", {})
            endpoint_count = len(analysis.get("endpoints_found", []))
            issue_count = len(analysis.get("security_issues", []))

            logger.info(f" Schema analysis completed: {endpoint_count} endpoints, {issue_count} issues")

            if issue_count > 0:
                logger.warning(f"  Found {issue_count} security issues in schema!")
                for issue in analysis.get("security_issues", [])[:3]:  # Show 第一 3
                    severity = issue.get("severity", "UNKNOWN")
                    issue_type = issue.get("issue", "unknown")
                    logger.warning(f"   ├─ [{severity}] {issue_type}")

            if endpoint_count > 0:
                logger.info(f" Discovered endpoints:")
                for endpoint in analysis.get("endpoints_found", [])[:5]:  # Show 第一 5
                    method = endpoint.get("method", "GET")
                    path = endpoint.get("path", "/")
                    logger.info(f"   ├─ {method} {path}")
        else:
            logger.error(" Schema analysis failed")

        return result

    @mcp.tool()
    def comprehensive_api_audit(base_url: str, schema_url: str = "", jwt_token: str = "", graphql_endpoint: str = "") -> Dict[str, Any]:
        """
        综合 API 安全 audit combining multiple 测试 techniques.

        参数:
            base_url: Base URL 的 the API
            schema_url: 说明：Optional API schema URL
            jwt_token: Optional JWT token 用于 分析
            graphql_endpoint: Optional GraphQL 端点

        返回:
            综合 audit 结果 使用 全部 API 安全 tests
        """
        audit_results = {
            "base_url": base_url,
            "audit_timestamp": time.time(),
            "tests_performed": [],
            "total_vulnerabilities": 0,
            "summary": {},
            "recommendations": []
        }

        logger.info(f" Starting comprehensive API security audit: {base_url}")

        # 1. API 端点 Fuzzing
        logger.info(" Phase 1: API endpoint discovery and fuzzing")
        fuzz_result = self.api_fuzzer(base_url)
        if fuzz_result.get("success"):
            audit_results["tests_performed"].append("api_fuzzing")
            audit_results["api_fuzzing"] = fuzz_result

        # 2. Schema 分析 (如果 provided)
        if schema_url:
            logger.info(" Phase 2: API schema analysis")
            schema_result = self.api_schema_analyzer(schema_url)
            if schema_result.get("success"):
                audit_results["tests_performed"].append("schema_analysis")
                audit_results["schema_analysis"] = schema_result

                schema_data = schema_result.get("schema_analysis_results", {})
                audit_results["total_vulnerabilities"] += len(schema_data.get("security_issues", []))

        # 3. JWT 分析 (如果 provided)
        if jwt_token:
            logger.info(" Phase 3: JWT token analysis")
            jwt_result = self.jwt_analyzer(jwt_token, base_url)
            if jwt_result.get("success"):
                audit_results["tests_performed"].append("jwt_analysis")
                audit_results["jwt_analysis"] = jwt_result

                jwt_data = jwt_result.get("jwt_analysis_results", {})
                audit_results["total_vulnerabilities"] += len(jwt_data.get("vulnerabilities", []))

        # 4. GraphQL 测试 (如果 provided)
        if graphql_endpoint:
            logger.info(" Phase 4: GraphQL security scanning")
            graphql_result = self.graphql_scanner(graphql_endpoint)
            if graphql_result.get("success"):
                audit_results["tests_performed"].append("graphql_scanning")
                audit_results["graphql_scanning"] = graphql_result

                graphql_data = graphql_result.get("graphql_scan_results", {})
                audit_results["total_vulnerabilities"] += len(graphql_data.get("vulnerabilities", []))

        # 生成 综合 recommendations
        audit_results["recommendations"] = [
            "Implement proper authentication and authorization",
            "Use HTTPS for all API communications",
            "Validate and sanitize all input parameters",
            "Implement rate limiting and request throttling",
            "Add comprehensive logging and monitoring",
            "Regular security testing and code reviews",
            "Keep API documentation updated and secure",
            "Implement proper error handling"
        ]

        # 说明：Summary
        audit_results["summary"] = {
            "tests_performed": len(audit_results["tests_performed"]),
            "total_vulnerabilities": audit_results["total_vulnerabilities"],
            "audit_coverage": "comprehensive" if len(audit_results["tests_performed"]) >= 3 else "partial"
        }

        logger.info(f" Comprehensive API audit completed:")
        logger.info(f"   ├─ Tests performed: {audit_results['summary']['tests_performed']}")
        logger.info(f"   ├─ Total vulnerabilities: {audit_results['summary']['total_vulnerabilities']}")
        logger.info(f"   └─ Coverage: {audit_results['summary']['audit_coverage']}")

        return {
            "success": True,
            "comprehensive_audit": audit_results
        }

    # ============================================================================
    # 高级 CTF 工具 (v5.0 ENHANCEMENT)
    # ============================================================================

    @mcp.tool()
    def volatility3_analyze(memory_file: str, plugin: str, output_file: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Volatility3 用于 高级 内存 取证 使用 增强日志.

        参数:
            memory_file: Path 到 内存 dump 文件
            plugin: Volatility3 plugin 到 执行
            output_file: 输出 文件 path
            additional_args: 附加 Volatility3 arguments

        返回:
            高级 内存 取证 结果
        """
        data = {
            "memory_file": memory_file,
            "plugin": plugin,
            "output_file": output_file,
            "additional_args": additional_args
        }
        logger.info(f" Starting Volatility3 analysis: {plugin}")
        result = hexstrike_client.safe_post("api/tools/volatility3", data)
        if result.get("success"):
            logger.info(f" Volatility3 analysis completed")
        else:
            logger.error(f" Volatility3 analysis failed")
        return result

    @mcp.tool()
    def foremost_carving(input_file: str, output_dir: str = "/tmp/foremost_output", file_types: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Foremost 用于 文件 carving 使用 增强日志.

        参数:
            input_file: 输入 文件 或 device 到 carve
            output_dir: 输出 目录 用于 carved 文件
            file_types: 文件 types 到 carve (jpg,gif,png,etc.)
            additional_args: 附加 Foremost arguments

        返回:
            文件 carving 结果
        """
        data = {
            "input_file": input_file,
            "output_dir": output_dir,
            "file_types": file_types,
            "additional_args": additional_args
        }
        logger.info(f" Starting Foremost file carving: {input_file}")
        result = hexstrike_client.safe_post("api/tools/foremost", data)
        if result.get("success"):
            logger.info(f" Foremost carving completed")
        else:
            logger.error(f" Foremost carving failed")
        return result

    @mcp.tool()
    def steghide_analysis(action: str, cover_file: str, embed_file: str = "", passphrase: str = "", output_file: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Steghide 用于 steganography 分析 使用 增强日志.

        参数:
            action: Action 到 perform (extract, embed, info)
            cover_file: Cover 文件 用于 steganography
            embed_file: 文件 到 embed (用于 embed action)
            passphrase: Passphrase 用于 steganography
            output_file: 输出 文件 path
            additional_args: 附加 Steghide arguments

        返回:
            Steganography 分析 结果
        """
        data = {
            "action": action,
            "cover_file": cover_file,
            "embed_file": embed_file,
            "passphrase": passphrase,
            "output_file": output_file,
            "additional_args": additional_args
        }
        logger.info(f" Starting Steghide {action}: {cover_file}")
        result = hexstrike_client.safe_post("api/tools/steghide", data)
        if result.get("success"):
            logger.info(f" Steghide {action} completed")
        else:
            logger.error(f" Steghide {action} failed")
        return result

    @mcp.tool()
    def exiftool_extract(file_path: str, output_format: str = "", tags: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 ExifTool 用于 metadata extraction 使用 增强日志.

        参数:
            file_path: Path 到 文件 用于 metadata extraction
            output_format: 输出 format (JSON, xml, csv)
            tags: Specific tags 到 extract
            additional_args: 附加 ExifTool arguments

        返回:
            Metadata extraction 结果
        """
        data = {
            "file_path": file_path,
            "output_format": output_format,
            "tags": tags,
            "additional_args": additional_args
        }
        logger.info(f" Starting ExifTool analysis: {file_path}")
        result = hexstrike_client.safe_post("api/tools/exiftool", data)
        if result.get("success"):
            logger.info(f" ExifTool analysis completed")
        else:
            logger.error(f" ExifTool analysis failed")
        return result

    @mcp.tool()
    def hashpump_attack(signature: str, data: str, key_length: str, append_data: str, additional_args: str = "") -> Dict[str, Any]:
        """
        执行 HashPump 用于 hash length extension attacks 使用 增强日志.

        参数:
            signature: 说明：Original hash signature
            data: 说明：Original data
            key_length: Length 的 secret key
            append_data: Data 到 append
            additional_args: 附加 HashPump arguments

        返回:
            Hash length extension attack 结果
        """
        data = {
            "signature": signature,
            "data": data,
            "key_length": key_length,
            "append_data": append_data,
            "additional_args": additional_args
        }
        logger.info(f" Starting HashPump attack")
        result = hexstrike_client.safe_post("api/tools/hashpump", data)
        if result.get("success"):
            logger.info(f" HashPump attack completed")
        else:
            logger.error(f" HashPump attack failed")
        return result

    # ============================================================================
    # BUG BOUNTY 侦察 工具 (v5.0 ENHANCEMENT)
    # ============================================================================

    @mcp.tool()
    def hakrawler_crawl(url: str, depth: int = 2, forms: bool = True, robots: bool = True, sitemap: bool = True, wayback: bool = False, additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Hakrawler 用于 web 端点 发现 使用 增强日志.

        Note: Uses standard Kali Linux hakrawler (hakluke/hakrawler) 使用 参数 mapping:
        - URL: Piped via echo 到 stdin (not -URL flag)
        - depth: Mapped 到 -d flag (not -depth)
        - forms: Mapped 到 -s flag 用于 showing sources
        - robots/sitemap/wayback: Mapped 到 -subs 用于 subdomain inclusion
        - Always includes -u 用于 unique URLs

        参数:
            url: 目标 URL 到 crawl
            depth: Crawling depth (mapped 到 -d)
            forms: Include forms 在 crawling (mapped 到 -s)
            robots: 检查 robots.txt (mapped 到 -subs)
            sitemap: 检查 sitemap.xml (mapped 到 -subs)
            wayback: Use Wayback Machine (mapped 到 -subs)
            additional_args: 附加 Hakrawler arguments

        返回:
            Web 端点 发现 结果
        """
        data = {
            "url": url,
            "depth": depth,
            "forms": forms,
            "robots": robots,
            "sitemap": sitemap,
            "wayback": wayback,
            "additional_args": additional_args
        }
        logger.info(f" Starting Hakrawler crawling: {url}")
        result = hexstrike_client.safe_post("api/tools/hakrawler", data)
        if result.get("success"):
            logger.info(f" Hakrawler crawling completed")
        else:
            logger.error(f" Hakrawler crawling failed")
        return result

    @mcp.tool(name="httpx_bulk_probe")
    def httpx_bulk_probe(targets: str = "", target_file: str = "", ports: str = "", methods: str = "GET", status_code: str = "", content_length: bool = False, output_file: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 HTTPx 用于 HTTP probing 使用 增强日志.

        参数:
            targets: 目标 URLs 或 IPs
            target_file: 文件 containing targets
            ports: Ports 到 probe
            methods: HTTP methods 到 use
            status_code: 过滤 由 状态 code
            content_length: 说明：Show content length
            output_file: 输出 文件 path
            additional_args: 附加 HTTPx arguments

        返回:
            HTTP probing 结果
        """
        data = {
            "targets": targets,
            "target_file": target_file,
            "ports": ports,
            "methods": methods,
            "status_code": status_code,
            "content_length": content_length,
            "output_file": output_file,
            "additional_args": additional_args
        }
        logger.info(f" Starting HTTPx probing")
        result = hexstrike_client.safe_post("api/tools/httpx", data)
        if result.get("success"):
            logger.info(f" HTTPx probing completed")
        else:
            logger.error(f" HTTPx probing failed")
        return result

    @mcp.tool()
    def paramspider_discovery(domain: str, exclude: str = "", output_file: str = "", level: int = 2, additional_args: str = "") -> Dict[str, Any]:
        """
        执行 ParamSpider 用于 参数 发现 使用 增强日志.

        参数:
            domain: 目标 域名
            exclude: Extensions 到 exclude
            output_file: 输出 文件 path
            level: 说明：Crawling level
            additional_args: 附加 ParamSpider arguments

        返回:
            参数 发现 结果
        """
        data = {
            "domain": domain,
            "exclude": exclude,
            "output_file": output_file,
            "level": level,
            "additional_args": additional_args
        }
        logger.info(f" Starting ParamSpider discovery: {domain}")
        result = hexstrike_client.safe_post("api/tools/paramspider", data)
        if result.get("success"):
            logger.info(f" ParamSpider discovery completed")
        else:
            logger.error(f" ParamSpider discovery failed")
        return result

    # ============================================================================
    # 高级 WEB 安全 工具 CONTINUED
    # ============================================================================

    @mcp.tool()
    def burpsuite_scan(project_file: str = "", config_file: str = "", target: str = "", headless: bool = False, scan_type: str = "", scan_config: str = "", output_file: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Burp Suite 使用 增强日志.

        参数:
            project_file: Burp project 文件 path
            config_file: Burp 配置 文件 path
            target: 目标 URL
            headless: Run 在 headless 模式
            scan_type: 类型 的 扫描 到 perform
            scan_config: 扫描 配置
            output_file: 输出 文件 path
            additional_args: 附加 Burp Suite arguments

        返回:
            Burp Suite 扫描 结果
        """
        data = {
            "project_file": project_file,
            "config_file": config_file,
            "target": target,
            "headless": headless,
            "scan_type": scan_type or "comprehensive",
            "scan_config": scan_config,
            "output_file": output_file,
            "additional_args": additional_args
        }
        logger.info(f" Starting Burp Suite scan")
        result = hexstrike_client.safe_post("api/tools/burpsuite-alternative", data)
        if result.get("success"):
            logger.info(f" Burp Suite scan completed")
        else:
            logger.error(f" Burp Suite scan failed")
        return result

    @mcp.tool()
    def zap_scan(target: str = "", scan_type: str = "baseline", api_key: str = "", daemon: bool = False, port: str = "8090", host: str = "0.0.0.0", format_type: str = "xml", output_file: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 OWASP ZAP 使用 增强日志.

        参数:
            target: 目标 URL
            scan_type: 类型 的 扫描 (baseline, full, API)
            api_key: 说明：ZAP API key
            daemon: Run 在 daemon 模式
            port: Port 用于 ZAP daemon
            host: Host 用于 ZAP daemon
            format_type: 输出 format (xml, JSON, html)
            output_file: 输出 文件 path
            additional_args: 附加 ZAP arguments

        返回:
            ZAP 扫描 结果
        """
        data = {
            "target": target,
            "scan_type": scan_type,
            "api_key": api_key,
            "daemon": daemon,
            "port": port,
            "host": host,
            "format": format_type,
            "output_file": output_file,
            "additional_args": additional_args
        }
        logger.info(f" Starting ZAP scan: {target}")
        result = hexstrike_client.safe_post("api/tools/zap", data)
        if result.get("success"):
            logger.info(f" ZAP scan completed for {target}")
        else:
            logger.error(f" ZAP scan failed for {target}")
        return result

    @mcp.tool()
    def arjun_scan(url: str, method: str = "GET", data: str = "", headers: str = "", timeout: str = "", output_file: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 Arjun 用于 参数 发现 使用 增强日志.

        参数:
            url: 目标 URL
            method: HTTP method (获取, POST, etc.)
            data: POST data 用于 测试
            headers: Custom 请求头
            timeout: 请求 超时
            output_file: 输出 文件 path
            additional_args: 附加 Arjun arguments

        返回:
            参数 发现 结果
        """
        data = {
            "url": url,
            "method": method,
            "data": data,
            "headers": headers,
            "timeout": timeout,
            "output_file": output_file,
            "additional_args": additional_args
        }
        logger.info(f" Starting Arjun parameter discovery: {url}")
        result = hexstrike_client.safe_post("api/tools/arjun", data)
        if result.get("success"):
            logger.info(f" Arjun completed for {url}")
        else:
            logger.error(f" Arjun failed for {url}")
        return result

    @mcp.tool()
    def wafw00f_scan(target: str, additional_args: str = "") -> Dict[str, Any]:
        """
        执行 wafw00f 到 identify 与 指纹 WAF products 使用 增强日志.

        参数:
            target: 目标 URL 或 IP
            additional_args: 附加 wafw00f arguments

        返回:
            WAF detection 结果
        """
        data = {
            "target": target,
            "additional_args": additional_args
        }
        logger.info(f" Starting Wafw00f WAF detection: {target}")
        result = hexstrike_client.safe_post("api/tools/wafw00f", data)
        if result.get("success"):
            logger.info(f" Wafw00f completed for {target}")
        else:
            logger.error(f" Wafw00f failed for {target}")
        return result

    @mcp.tool()
    def fierce_scan(domain: str, dns_server: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 fierce 用于 DNS 侦察 使用 增强日志.

        参数:
            domain: 目标 域名
            dns_server: DNS 服务端 到 use
            additional_args: 附加 fierce arguments

        返回:
            DNS 侦察 结果
        """
        data = {
            "domain": domain,
            "dns_server": dns_server,
            "additional_args": additional_args
        }
        logger.info(f" Starting Fierce DNS recon: {domain}")
        result = hexstrike_client.safe_post("api/tools/fierce", data)
        if result.get("success"):
            logger.info(f" Fierce completed for {domain}")
        else:
            logger.error(f" Fierce failed for {domain}")
        return result

    @mcp.tool()
    def dnsenum_scan(domain: str, dns_server: str = "", wordlist: str = "", additional_args: str = "") -> Dict[str, Any]:
        """
        执行 dnsenum 用于 DNS enumeration 使用 增强日志.

        参数:
            domain: 目标 域名
            dns_server: DNS 服务端 到 use
            wordlist: Wordlist 用于 brute forcing
            additional_args: 附加 dnsenum arguments

        返回:
            DNS enumeration 结果
        """
        data = {
            "domain": domain,
            "dns_server": dns_server,
            "wordlist": wordlist,
            "additional_args": additional_args
        }
        logger.info(f" Starting DNSenum: {domain}")
        result = hexstrike_client.safe_post("api/tools/dnsenum", data)
        if result.get("success"):
            logger.info(f" DNSenum completed for {domain}")
        else:
            logger.error(f" DNSenum failed for {domain}")
        return result

    @mcp.tool()
    def autorecon_scan(
        target: str = "",
        target_file: str = "",
        ports: str = "",
        output_dir: str = "",
        max_scans: str = "",
        max_port_scans: str = "",
        heartbeat: str = "",
        timeout: str = "",
        target_timeout: str = "",
        config_file: str = "",
        global_file: str = "",
        plugins_dir: str = "",
        add_plugins_dir: str = "",
        tags: str = "",
        exclude_tags: str = "",
        port_scans: str = "",
        service_scans: str = "",
        reports: str = "",
        single_target: bool = False,
        only_scans_dir: bool = False,
        no_port_dirs: bool = False,
        nmap: str = "",
        nmap_append: str = "",
        proxychains: bool = False,
        disable_sanity_checks: bool = False,
        disable_keyboard_control: bool = False,
        force_services: str = "",
        accessible: bool = False,
        verbose: int = 0,
        curl_path: str = "",
        dirbuster_tool: str = "",
        dirbuster_wordlist: str = "",
        dirbuster_threads: str = "",
        dirbuster_ext: str = "",
        onesixtyone_community_strings: str = "",
        global_username_wordlist: str = "",
        global_password_wordlist: str = "",
        global_domain: str = "",
        additional_args: str = ""
    ) -> Dict[str, Any]:
        """
        执行 AutoRecon 用于 综合 目标 enumeration 使用 full 参数 support.

        参数:
            target: Single 目标 到 扫描
            target_file: 文件 containing multiple targets
            ports: Specific ports 到 扫描
            output_dir: 输出 目录
            max_scans: Maximum number 的 concurrent scans
            max_port_scans: Maximum number 的 concurrent port scans
            heartbeat: 说明：Heartbeat interval
            timeout: 全局 超时
            target_timeout: Per-target 超时
            config_file: 配置 文件 path
            global_file: 全局 配置 文件
            plugins_dir: Plugins 目录
            add_plugins_dir: 附加 plugins 目录
            tags: Plugin tags 到 include
            exclude_tags: Plugin tags 到 exclude
            port_scans: Port 扫描 plugins 到 run
            service_scans: Service 扫描 plugins 到 run
            reports: Report plugins 到 run
            single_target: Use single 目标 目录 structure
            only_scans_dir: 仅 创建 scans 目录
            no_port_dirs: Don't 创建 port directories
            nmap: Custom nmap 命令
            nmap_append: Arguments 到 append 到 nmap
            proxychains: 说明：Use proxychains
            disable_sanity_checks: 禁用 sanity checks
            disable_keyboard_control: 禁用 keyboard control
            force_services: 说明：Force service detection
            accessible: 启用 accessible 输出
            verbose: 说明：Verbosity level (0-3)
            curl_path: 说明：Custom curl path
            dirbuster_tool: 目录 busting 工具
            dirbuster_wordlist: 目录 busting wordlist
            dirbuster_threads: 目录 busting threads
            dirbuster_ext: 目录 busting extensions
            onesixtyone_community_strings: 说明：SNMP community strings
            global_username_wordlist: 全局 username wordlist
            global_password_wordlist: 全局 password wordlist
            global_domain: 全局 域名
            additional_args: 附加 AutoRecon arguments

        返回:
            综合 enumeration 结果 使用 full configurability
        """
        data = {
            "target": target,
            "target_file": target_file,
            "ports": ports,
            "output_dir": output_dir,
            "max_scans": max_scans,
            "max_port_scans": max_port_scans,
            "heartbeat": heartbeat,
            "timeout": timeout,
            "target_timeout": target_timeout,
            "config_file": config_file,
            "global_file": global_file,
            "plugins_dir": plugins_dir,
            "add_plugins_dir": add_plugins_dir,
            "tags": tags,
            "exclude_tags": exclude_tags,
            "port_scans": port_scans,
            "service_scans": service_scans,
            "reports": reports,
            "single_target": single_target,
            "only_scans_dir": only_scans_dir,
            "no_port_dirs": no_port_dirs,
            "nmap": nmap,
            "nmap_append": nmap_append,
            "proxychains": proxychains,
            "disable_sanity_checks": disable_sanity_checks,
            "disable_keyboard_control": disable_keyboard_control,
            "force_services": force_services,
            "accessible": accessible,
            "verbose": verbose,
            "curl_path": curl_path,
            "dirbuster_tool": dirbuster_tool,
            "dirbuster_wordlist": dirbuster_wordlist,
            "dirbuster_threads": dirbuster_threads,
            "dirbuster_ext": dirbuster_ext,
            "onesixtyone_community_strings": onesixtyone_community_strings,
            "global_username_wordlist": global_username_wordlist,
            "global_password_wordlist": global_password_wordlist,
            "global_domain": global_domain,
            "additional_args": additional_args
        }
        logger.info(f" Starting AutoRecon comprehensive enumeration: {target}")
        result = hexstrike_client.safe_post("api/tools/autorecon", data)
        if result.get("success"):
            logger.info(f" AutoRecon comprehensive enumeration completed for {target}")
        else:
            logger.error(f" AutoRecon failed for {target}")
        return result

    # ============================================================================
    # 系统 监控 & TELEMETRY
    # ============================================================================

    @mcp.tool()
    def server_health() -> Dict[str, Any]:
        """
        检查 the 健康 状态 的 the HexStrike AI 服务端.

        返回:
            服务端 健康 information 使用 工具 availability 与 telemetry
        """
        logger.info(f" Checking HexStrike AI server health")
        result = hexstrike_client.check_health()
        if result.get("status") == "healthy":
            logger.info(f" Server is healthy - {result.get('total_tools_available', 0)} tools available")
        else:
            logger.warning(f"  Server health check returned: {result.get('status', 'unknown')}")
        return result

    @mcp.tool()
    def get_cache_stats() -> Dict[str, Any]:
        """
        获取 缓存 统计 来自 the HexStrike AI 服务端.

        返回:
            缓存 performance 统计
        """
        logger.info(f" Getting cache statistics")
        result = hexstrike_client.safe_get("api/cache/stats")
        if "hit_rate" in result:
            logger.info(f" Cache hit rate: {result.get('hit_rate', 'unknown')}")
        return result

    @mcp.tool()
    def clear_cache() -> Dict[str, Any]:
        """
        Clear the 缓存 在 the HexStrike AI 服务端.

        返回:
            缓存 clear 操作 结果
        """
        logger.info(f" Clearing server cache")
        result = hexstrike_client.safe_post("api/cache/clear", {})
        if result.get("success"):
            logger.info(f" Cache cleared successfully")
        else:
            logger.error(f" Failed to clear cache")
        return result

    @mcp.tool()
    def get_telemetry() -> Dict[str, Any]:
        """
        获取 系统 telemetry 来自 the HexStrike AI 服务端.

        返回:
            系统 performance 与 usage telemetry
        """
        logger.info(f" Getting system telemetry")
        result = hexstrike_client.safe_get("api/telemetry")
        if "commands_executed" in result:
            logger.info(f" Commands executed: {result.get('commands_executed', 0)}")
        return result

    # ============================================================================
    # 进程 MANAGEMENT 工具 (v5.0 ENHANCEMENT)
    # ============================================================================

    @mcp.tool()
    def list_active_processes() -> Dict[str, Any]:
        """
        列出 全部 active processes 在 the HexStrike AI 服务端.

        返回:
            列出 的 active processes 使用 their 状态 与 progress
        """
        logger.info(" Listing active processes")
        result = hexstrike_client.safe_get("api/processes/list")
        if result.get("success"):
            logger.info(f" Found {result.get('total_count', 0)} active processes")
        else:
            logger.error(" Failed to list processes")
        return result

    @mcp.tool()
    def get_process_status(pid: int) -> Dict[str, Any]:
        """
        获取 the 状态 的 a specific 进程.

        参数:
            pid: 进程 ID 到 检查

        返回:
            进程 状态 information including progress 与 runtime
        """
        logger.info(f" Checking status of process {pid}")
        result = hexstrike_client.safe_get(f"api/processes/status/{pid}")
        if result.get("success"):
            logger.info(f" Process {pid} status retrieved")
        else:
            logger.error(f" Process {pid} not found or error occurred")
        return result

    @mcp.tool()
    def terminate_process(pid: int) -> Dict[str, Any]:
        """
        Terminate a specific running 进程.

        参数:
            pid: 进程 ID 到 terminate

        返回:
            成功 状态 的 the termination 操作
        """
        logger.info(f" Terminating process {pid}")
        result = hexstrike_client.safe_post(f"api/processes/terminate/{pid}", {})
        if result.get("success"):
            logger.info(f" Process {pid} terminated successfully")
        else:
            logger.error(f" Failed to terminate process {pid}")
        return result

    @mcp.tool()
    def pause_process(pid: int) -> Dict[str, Any]:
        """
        Pause a specific running 进程.

        参数:
            pid: 进程 ID 到 pause

        返回:
            成功 状态 的 the pause 操作
        """
        logger.info(f" Pausing process {pid}")
        result = hexstrike_client.safe_post(f"api/processes/pause/{pid}", {})
        if result.get("success"):
            logger.info(f" Process {pid} paused successfully")
        else:
            logger.error(f" Failed to pause process {pid}")
        return result

    @mcp.tool()
    def resume_process(pid: int) -> Dict[str, Any]:
        """
        Resume a paused 进程.

        参数:
            pid: 进程 ID 到 resume

        返回:
            成功 状态 的 the resume 操作
        """
        logger.info(f" Resuming process {pid}")
        result = hexstrike_client.safe_post(f"api/processes/resume/{pid}", {})
        if result.get("success"):
            logger.info(f" Process {pid} resumed successfully")
        else:
            logger.error(f" Failed to resume process {pid}")
        return result

    @mcp.tool()
    def get_process_dashboard() -> Dict[str, Any]:
        """
        获取 增强 进程 dashboard 使用 visual 状态 indicators.

        返回:
            Real-time dashboard 使用 progress bars, 系统 指标, 与 进程 状态
        """
        logger.info(" Getting process dashboard")
        result = hexstrike_client.safe_get("api/processes/dashboard")
        if result.get("success", True) and "total_processes" in result:
            total = result.get("total_processes", 0)
            logger.info(f" Dashboard retrieved: {total} active processes")

            # Log visual summary 用于 better UX
            if total > 0:
                logger.info(" Active Processes Summary:")
                for proc in result.get("processes", [])[:3]:  # Show 第一 3
                    logger.info(f"   ├─ PID {proc['pid']}: {proc['progress_bar']} {proc['progress_percent']}")
        else:
            logger.error(" Failed to get process dashboard")
        return result

    @mcp.tool()
    def execute_command(command: str, use_cache: bool = True) -> Dict[str, Any]:
        """
        执行 an arbitrary 命令 在 the HexStrike AI 服务端 使用 增强日志.

        参数:
            command: The 命令 到 执行
            use_cache: Whether 到 use caching 用于 this 命令

        返回:
            命令 execution 结果 使用 增强 telemetry
        """
        try:
            logger.info(f" Executing command: {command}")
            result = hexstrike_client.execute_command(command, use_cache)
            if "error" in result:
                logger.error(f" Command failed: {result['error']}")
                return {
                    "success": False,
                    "error": result["error"],
                    "stdout": "",
                    "stderr": f"Error executing command: {result['error']}"
                }

            if result.get("success"):
                execution_time = result.get("execution_time", 0)
                logger.info(f" Command completed successfully in {execution_time:.2f}s")
            else:
                logger.warning(f"  Command completed with errors")

            return result
        except Exception as e:
            logger.error(f" Error executing command '{command}': {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "stdout": "",
                "stderr": f"Error executing command: {str(e)}"
            }

    # ============================================================================
    # 高级 漏洞 INTELLIGENCE MCP 工具 (v6.0 ENHANCEMENT)
    # ============================================================================

    @mcp.tool()
    def monitor_cve_feeds(hours: int = 24, severity_filter: str = "HIGH,CRITICAL", keywords: str = "") -> Dict[str, Any]:
        """
        监控 CVE databases 用于 new 漏洞 使用 AI 分析.

        参数:
            hours: Hours 到 look back 用于 new CVEs (默认: 24)
            severity_filter: 过滤 由 CVSS severity - comma-separated values (低,中,高,严重,全部)
            keywords: 过滤 CVEs 由 keywords 在 description (comma-separated)

        返回:
            Latest CVEs 使用 exploitability 分析 与 threat intelligence

        示例:
            monitor_cve_feeds(48, "严重", "remote code execution")
        """
        data = {
            "hours": hours,
            "severity_filter": severity_filter,
            "keywords": keywords
        }
        logger.info(f" Monitoring CVE feeds for last {hours} hours | Severity: {severity_filter}")
        result = hexstrike_client.safe_post("api/vuln-intel/cve-monitor", data)

        if result.get("success"):
            cve_count = len(result.get("cve_monitoring", {}).get("cves", []))
            exploit_analysis_count = len(result.get("exploitability_analysis", []))
            logger.info(f" Found {cve_count} CVEs with {exploit_analysis_count} exploitability analyses")

        return result

    @mcp.tool()
    def generate_exploit_from_cve(cve_id: str, target_os: str = "", target_arch: str = "x64", exploit_type: str = "poc", evasion_level: str = "none") -> Dict[str, Any]:
        """
        生成 working exploits 来自 CVE information using AI-powered 分析.

        参数:
            cve_id: 说明：CVE identifier (e.g., CVE-2024-1234)
            target_os: 目标 operating 系统 (windows, linux, macos, any)
            target_arch: 目标 architecture (x86, x64, arm, any)
            exploit_type: 类型 的 利用 到 生成 (poc, weaponized, stealth)
            evasion_level: Evasion sophistication (none, 基础, 高级)

        返回:
            Generated 利用 code 使用 测试 instructions 与 evasion techniques

        示例:
            generate_exploit_from_cve("CVE-2024-1234", "linux", "x64", "weaponized", "高级")
        """
        data = {
            "cve_id": cve_id,
            "target_os": target_os,
            "target_arch": target_arch,
            "exploit_type": exploit_type,
            "evasion_level": evasion_level
        }
        logger.info(f" Generating {exploit_type} exploit for {cve_id} | Target: {target_os} {target_arch}")
        result = hexstrike_client.safe_post("api/vuln-intel/exploit-generate", data)

        if result.get("success"):
            cve_analysis = result.get("cve_analysis", {})
            exploit_gen = result.get("exploit_generation", {})
            exploitability = cve_analysis.get("exploitability_level", "UNKNOWN")
            exploit_success = exploit_gen.get("success", False)

            logger.info(f" CVE Analysis: {exploitability} exploitability")
            logger.info(f" Exploit Generation: {'SUCCESS' if exploit_success else 'FAILED'}")

        return result

    @mcp.tool()
    def discover_attack_chains(target_software: str, attack_depth: int = 3, include_zero_days: bool = False) -> Dict[str, Any]:
        """
        Discover multi-stage attack chains 用于 目标 software 使用 漏洞 correlation.

        参数:
            target_software: 目标 software/系统 (e.g., "Apache HTTP 服务端", "Windows 服务端 2019")
            attack_depth: Maximum number 的 stages 在 attack chain (1-5)
            include_zero_days: Include potential zero-day 漏洞 在 分析

        返回:
            Attack chains 使用 漏洞 combinations, 成功 probabilities, 与 利用 availability

        示例:
            discover_attack_chains("Apache HTTP 服务端 2.4", 4, True)
        """
        data = {
            "target_software": target_software,
            "attack_depth": min(max(attack_depth, 1), 5),  # 说明：Clamp between 1-5
            "include_zero_days": include_zero_days
        }
        logger.info(f" Discovering attack chains for {target_software} | Depth: {attack_depth} | Zero-days: {include_zero_days}")
        result = hexstrike_client.safe_post("api/vuln-intel/attack-chains", data)

        if result.get("success"):
            chains = result.get("attack_chain_discovery", {}).get("attack_chains", [])
            enhanced_chains = result.get("attack_chain_discovery", {}).get("enhanced_chains", [])

            logger.info(f" Found {len(chains)} attack chains")
            if enhanced_chains:
                logger.info(f" Enhanced {len(enhanced_chains)} chains with exploit analysis")

        return result

    @mcp.tool()
    def research_zero_day_opportunities(target_software: str, analysis_depth: str = "standard", source_code_url: str = "") -> Dict[str, Any]:
        """
        Automated zero-day 漏洞 research using AI 分析 与 pattern recognition.

        参数:
            target_software: Software 到 research 用于 漏洞 (e.g., "nginx", "OpenSSL")
            analysis_depth: Depth 的 分析 (quick, standard, 综合)
            source_code_url: URL 到 source code repository 用于 增强 分析

        返回:
            Potential 漏洞 areas 使用 exploitation feasibility 与 research recommendations

        示例:
            research_zero_day_opportunities("nginx 1.20", "综合", "HTTPS://github.com/nginx/nginx")
        """
        if analysis_depth not in ["quick", "standard", "comprehensive"]:
            analysis_depth = "standard"

        data = {
            "target_software": target_software,
            "analysis_depth": analysis_depth,
            "source_code_url": source_code_url
        }
        logger.info(f" Researching zero-day opportunities in {target_software} | Depth: {analysis_depth}")
        result = hexstrike_client.safe_post("api/vuln-intel/zero-day-research", data)

        if result.get("success"):
            research = result.get("zero_day_research", {})
            potential_vulns = len(research.get("potential_vulnerabilities", []))
            risk_score = research.get("risk_assessment", {}).get("risk_score", 0)

            logger.info(f" Found {potential_vulns} potential vulnerability areas")
            logger.info(f" Risk Score: {risk_score}/100")

        return result

    @mcp.tool()
    def correlate_threat_intelligence(indicators: str, timeframe: str = "30d", sources: str = "all") -> Dict[str, Any]:
        """
        Correlate threat intelligence across multiple sources 使用 高级 分析.

        参数:
            indicators: 说明：Comma-separated IOCs (IPs, domains, hashes, CVEs, etc.)
            timeframe: Time window 用于 correlation (7d, 30d, 90d, 1y)
            sources: Intelligence sources 到 query (cve, 利用-db, github, twitter, 全部)

        返回:
            Correlated threat intelligence 使用 attribution, timeline, 与 threat scoring

        示例:
            correlate_threat_intelligence("CVE-2024-1234,192.168.1.100,malware.exe", "90d", "全部")
        """
        # 校验 timeframe
        valid_timeframes = ["7d", "30d", "90d", "1y"]
        if timeframe not in valid_timeframes:
            timeframe = "30d"

        # 解析 indicators
        indicator_list = [i.strip() for i in indicators.split(",") if i.strip()]

        if not indicator_list:
            logger.error(" No valid indicators provided")
            return {"success": False, "error": "No valid indicators provided"}

        data = {
            "indicators": indicator_list,
            "timeframe": timeframe,
            "sources": sources
        }
        logger.info(f" Correlating threat intelligence for {len(indicator_list)} indicators | Timeframe: {timeframe}")
        result = hexstrike_client.safe_post("api/vuln-intel/threat-feeds", data)

        if result.get("success"):
            threat_intel = result.get("threat_intelligence", {})
            correlations = len(threat_intel.get("correlations", []))
            threat_score = threat_intel.get("threat_score", 0)

            logger.info(f" Found {correlations} threat correlations")
            logger.info(f" Overall Threat Score: {threat_score:.1f}/100")

        return result

    @mcp.tool()
    def advanced_payload_generation(attack_type: str, target_context: str = "", evasion_level: str = "standard", custom_constraints: str = "") -> Dict[str, Any]:
        """
        生成 高级 payloads 使用 AI-powered evasion techniques 与 contextual adaptation.

        参数:
            attack_type: 类型 的 attack (rce, privilege_escalation, persistence, exfiltration, xss, sqli)
            target_context: 目标 environment details (OS, software versions, 安全 controls)
            evasion_level: Evasion sophistication (基础, standard, 高级, nation-state)
            custom_constraints: Custom 载荷 constraints (size limits, character restrictions, etc.)

        返回:
            高级 payloads 使用 multiple evasion techniques 与 deployment instructions

        示例:
            说明：advanced_payload_generation("rce", "Windows 11 + Defender + AppLocker", "nation-state", "max_size:256,no_quotes")
        """
        valid_attack_types = ["rce", "privilege_escalation", "persistence", "exfiltration", "xss", "sqli", "lfi", "ssrf"]
        valid_evasion_levels = ["basic", "standard", "advanced", "nation-state"]

        if attack_type not in valid_attack_types:
            attack_type = "rce"

        if evasion_level not in valid_evasion_levels:
            evasion_level = "standard"

        data = {
            "attack_type": attack_type,
            "target_context": target_context,
            "evasion_level": evasion_level,
            "custom_constraints": custom_constraints
        }
        logger.info(f" Generating advanced {attack_type} payload | Evasion: {evasion_level}")
        if target_context:
            logger.info(f" Target Context: {target_context}")

        result = hexstrike_client.safe_post("api/ai/advanced-payload-generation", data)

        if result.get("success"):
            payload_gen = result.get("advanced_payload_generation", {})
            payload_count = payload_gen.get("payload_count", 0)
            evasion_applied = payload_gen.get("evasion_level", "none")

            logger.info(f" Generated {payload_count} advanced payloads")
            logger.info(f" Evasion Level Applied: {evasion_applied}")

        return result

    @mcp.tool()
    def vulnerability_intelligence_dashboard() -> Dict[str, Any]:
        """
        获取 a 综合 漏洞 intelligence dashboard 使用 latest threats 与 trends.

        返回:
            Dashboard 使用 latest CVEs, trending 漏洞, 利用 availability, 与 threat landscape

        示例:
            说明：vulnerability_intelligence_dashboard()
        """
        logger.info(" Generating vulnerability intelligence dashboard")

        # 获取 latest 严重 CVEs
        latest_cves = hexstrike_client.safe_post("api/vuln-intel/cve-monitor", {
            "hours": 24,
            "severity_filter": "CRITICAL",
            "keywords": ""
        })

        # 获取 trending attack types
        trending_research = hexstrike_client.safe_post("api/vuln-intel/zero-day-research", {
            "target_software": "web applications",
            "analysis_depth": "quick"
        })

        # 说明：Compile dashboard
        dashboard = {
            "timestamp": time.time(),
            "latest_critical_cves": latest_cves.get("cve_monitoring", {}).get("cves", [])[:5],
            "threat_landscape": {
                "high_risk_software": ["Apache HTTP Server", "Microsoft Exchange", "VMware vCenter", "Fortinet FortiOS"],
                "trending_attack_vectors": ["Supply chain attacks", "Cloud misconfigurations", "Zero-day exploits", "AI-powered attacks"],
                "active_threat_groups": ["APT29", "Lazarus Group", "FIN7", "REvil"],
            },
            "exploit_intelligence": {
                "new_public_exploits": "Simulated data - check exploit-db for real data",
                "weaponized_exploits": "Monitor threat intelligence feeds",
                "exploit_kits": "Track underground markets"
            },
            "recommendations": [
                "Prioritize patching for critical CVEs discovered in last 24h",
                "Monitor for zero-day activity in trending attack vectors",
                "Implement advanced threat detection for active threat groups",
                "Review security controls against nation-state level attacks"
            ]
        }

        logger.info(" Vulnerability intelligence dashboard generated")
        return {
            "success": True,
            "dashboard": dashboard
        }

    @mcp.tool()
    def threat_hunting_assistant(target_environment: str, threat_indicators: str = "", hunt_focus: str = "general") -> Dict[str, Any]:
        """
        AI-powered threat hunting assistant 使用 漏洞 correlation 与 attack simulation.

        参数:
            target_environment: Environment 到 hunt 在 (e.g., "Windows 域名", "云 Infrastructure")
            threat_indicators: Known IOCs 或 suspicious indicators 到 investigate
            hunt_focus: 说明：Focus area (general, apt, ransomware, insider_threat, supply_chain)

        返回:
            Threat hunting playbook 使用 detection queries, IOCs, 与 investigation steps

        示例:
            threat_hunting_assistant("Windows 域名", "suspicious_process.exe,192.168.1.100", "apt")
        """
        valid_hunt_focus = ["general", "apt", "ransomware", "insider_threat", "supply_chain"]
        if hunt_focus not in valid_hunt_focus:
            hunt_focus = "general"

        logger.info(f" Generating threat hunting playbook for {target_environment} | Focus: {hunt_focus}")

        # 解析 indicators 如果 provided
        indicators = [i.strip() for i in threat_indicators.split(",") if i.strip()] if threat_indicators else []

        # 生成 hunting playbook
        hunting_playbook = {
            "target_environment": target_environment,
            "hunt_focus": hunt_focus,
            "indicators_analyzed": indicators,
            "detection_queries": [],
            "investigation_steps": [],
            "threat_scenarios": [],
            "mitigation_strategies": []
        }

        # 说明：Environment-specific detection queries
        if "windows" in target_environment.lower():
            hunting_playbook["detection_queries"] = [
                "Get-WinEvent | Where-Object {$_.Id -eq 4688 -and $_.Message -like '*suspicious*'}",
                "Get-Process | Where-Object {$_.ProcessName -notin @('explorer.exe', 'svchost.exe')}",
                "Get-ItemProperty HKLM:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run",
                "Get-NetTCPConnection | Where-Object {$_.State -eq 'Established' -and $_.RemoteAddress -notlike '10.*'}"
            ]
        elif "cloud" in target_environment.lower():
            hunting_playbook["detection_queries"] = [
                "CloudTrail logs for unusual API calls",
                "Failed authentication attempts from unknown IPs",
                "Privilege escalation events",
                "Data exfiltration indicators"
            ]

        # 说明：Focus-specific threat scenarios
        focus_scenarios = {
            "apt": [
                "Spear phishing with weaponized documents",
                "Living-off-the-land techniques",
                "Lateral movement via stolen credentials",
                "Data staging and exfiltration"
            ],
            "ransomware": [
                "Initial access via RDP/VPN",
                "Privilege escalation and persistence",
                "Shadow copy deletion",
                "Encryption and ransom note deployment"
            ],
            "insider_threat": [
                "Unusual data access patterns",
                "After-hours activity",
                "Large data downloads",
                "Access to sensitive systems"
            ]
        }

        hunting_playbook["threat_scenarios"] = focus_scenarios.get(hunt_focus, [
            "Unauthorized access attempts",
            "Suspicious process execution",
            "Network anomalies",
            "Data access violations"
        ])

        # 说明：Investigation steps
        hunting_playbook["investigation_steps"] = [
            "1. Validate initial indicators and expand IOC list",
            "2. Run detection queries and analyze results",
            "3. Correlate events across multiple data sources",
            "4. Identify affected systems and user accounts",
            "5. Assess scope and impact of potential compromise",
            "6. Implement containment measures if threat confirmed",
            "7. Document findings and update detection rules"
        ]

        # Correlate 使用 漏洞 intelligence 如果 indicators provided
        if indicators:
            logger.info(f" Correlating {len(indicators)} indicators with threat intelligence")
            correlation_result = correlate_threat_intelligence(",".join(indicators), "30d", "all")

            if correlation_result.get("success"):
                hunting_playbook["threat_correlation"] = correlation_result.get("threat_intelligence", {})

        logger.info(" Threat hunting playbook generated")
        return {
            "success": True,
            "hunting_playbook": hunting_playbook
        }

    # ============================================================================
    # 增强 VISUAL 输出 工具
    # ============================================================================

    @mcp.tool()
    def get_live_dashboard() -> Dict[str, Any]:
        """
        获取 a beautiful live dashboard showing 全部 active processes 使用 增强 visual formatting.

        返回:
            Live dashboard 使用 visual 进程 监控 与 系统 指标
        """
        logger.info(" Fetching live process dashboard")
        result = hexstrike_client.safe_get("api/processes/dashboard")
        if result.get("success", True):
            logger.info(" Live dashboard retrieved successfully")
        else:
            logger.error(" Failed to retrieve live dashboard")
        return result

    @mcp.tool()
    def create_vulnerability_report(vulnerabilities: str, target: str = "", scan_type: str = "comprehensive") -> Dict[str, Any]:
        """
        创建 a beautiful 漏洞 report 使用 severity-based styling 与 visual indicators.

        参数:
            vulnerabilities: JSON string containing 漏洞 data
            target: 目标 that was scanned
            scan_type: 类型 的 扫描 performed

        返回:
            Formatted 漏洞 report 使用 visual enhancements
        """
        import json

        try:
            # 解析 漏洞 如果 provided as JSON string
            if isinstance(vulnerabilities, str):
                vuln_data = json.loads(vulnerabilities)
            else:
                vuln_data = vulnerabilities

            logger.info(f" Creating vulnerability report for {len(vuln_data)} findings")

            # 创建 individual 漏洞 cards
            vulnerability_cards = []
            for vuln in vuln_data:
                card_result = hexstrike_client.safe_post("api/visual/vulnerability-card", vuln)
                if card_result.get("success"):
                    vulnerability_cards.append(card_result.get("vulnerability_card", ""))

            # 创建 summary report
            summary_data = {
                "target": target,
                "vulnerabilities": vuln_data,
                "tools_used": [scan_type],
                "execution_time": 0
            }

            summary_result = hexstrike_client.safe_post("api/visual/summary-report", summary_data)

            logger.info(" Vulnerability report created successfully")
            return {
                "success": True,
                "vulnerability_cards": vulnerability_cards,
                "summary_report": summary_result.get("summary_report", ""),
                "total_vulnerabilities": len(vuln_data),
                "timestamp": summary_result.get("timestamp", "")
            }

        except Exception as e:
            logger.error(f" Failed to create vulnerability report: {str(e)}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def format_tool_output_visual(tool_name: str, output: str, success: bool = True) -> Dict[str, Any]:
        """
        Format 工具 输出 使用 beautiful visual styling, syntax highlighting, 与 structure.

        参数:
            tool_name: Name 的 the 安全 工具
            output: Raw 输出 来自 the 工具
            success: Whether the 工具 execution was successful

        返回:
            Beautifully formatted 工具 输出 使用 visual enhancements
        """
        logger.info(f" Formatting output for {tool_name}")

        data = {
            "tool": tool_name,
            "output": output,
            "success": success
        }

        result = hexstrike_client.safe_post("api/visual/tool-output", data)
        if result.get("success"):
            logger.info(f" Tool output formatted successfully for {tool_name}")
        else:
            logger.error(f" Failed to format tool output for {tool_name}")

        return result

    @mcp.tool()
    def create_scan_summary(target: str, tools_used: str, vulnerabilities_found: int = 0,
                           execution_time: float = 0.0, findings: str = "") -> Dict[str, Any]:
        """
        创建 a 综合 扫描 summary report 使用 beautiful visual formatting.

        参数:
            target: 目标 that was scanned
            tools_used: Comma-separated 列出 的 工具 used
            vulnerabilities_found: Number 的 漏洞 discovered
            execution_time: Total execution time 在 seconds
            findings: 附加 findings 或 说明

        返回:
            Beautiful 扫描 summary report 使用 visual enhancements
        """
        logger.info(f" Creating scan summary for {target}")

        tools_list = [tool.strip() for tool in tools_used.split(",")]

        summary_data = {
            "target": target,
            "tools_used": tools_list,
            "execution_time": execution_time,
            "vulnerabilities": [{"severity": "info"}] * vulnerabilities_found,  # Mock data 用于 count
            "findings": findings
        }

        result = hexstrike_client.safe_post("api/visual/summary-report", summary_data)
        if result.get("success"):
            logger.info(" Scan summary created successfully")
        else:
            logger.error(" Failed to create scan summary")

        return result

    @mcp.tool()
    def display_system_metrics() -> Dict[str, Any]:
        """
        展示 current 系统 指标 与 performance indicators 使用 visual formatting.

        返回:
            系统 指标 使用 beautiful visual presentation
        """
        logger.info(" Fetching system metrics")

        # 获取 telemetry data
        telemetry_result = hexstrike_client.safe_get("api/telemetry")

        if telemetry_result.get("success", True):
            logger.info(" System metrics retrieved successfully")

            # Format the 指标 用于 better 展示
            metrics = telemetry_result.get("system_metrics", {})
            stats = {
                "cpu_percent": metrics.get("cpu_percent", 0),
                "memory_percent": metrics.get("memory_percent", 0),
                "disk_usage": metrics.get("disk_usage", 0),
                "uptime_seconds": telemetry_result.get("uptime_seconds", 0),
                "commands_executed": telemetry_result.get("commands_executed", 0),
                "success_rate": telemetry_result.get("success_rate", "0%")
            }

            return {
                "success": True,
                "metrics": stats,
                "formatted_display": f"""
  System Performance Metrics:
├─ CPU Usage: {stats['cpu_percent']:.1f}%
├─ Memory Usage: {stats['memory_percent']:.1f}%
├─ Disk Usage: {stats['disk_usage']:.1f}%
├─ Uptime: {stats['uptime_seconds']:.0f}s
├─ Commands Executed: {stats['commands_executed']}
└─ Success Rate: {stats['success_rate']}
""",
                "timestamp": telemetry_result.get("timestamp", "")
            }
        else:
            logger.error(" Failed to retrieve system metrics")
            return telemetry_result

    # ============================================================================
    # 智能 DECISION ENGINE 工具
    # ============================================================================

    @mcp.tool()
    def analyze_target_intelligence(target: str) -> Dict[str, Any]:
        """
        分析 目标 using AI-powered intelligence 到 创建 综合 profile.

        参数:
            target: 目标 URL, IP address, 或 域名 到 分析

        返回:
            综合 目标 profile 使用 technology detection, risk assessment, 与 recommendations
        """
        logger.info(f" Analyzing target intelligence for: {target}")

        data = {"target": target}
        result = hexstrike_client.safe_post("api/intelligence/analyze-target", data)

        if result.get("success"):
            profile = result.get("target_profile", {})
            logger.info(f" Target analysis completed - Type: {profile.get('target_type')}, Risk: {profile.get('risk_level')}")
        else:
            logger.error(f" Target analysis failed for {target}")

        return result

    @mcp.tool()
    def select_optimal_tools_ai(target: str, objective: str = "comprehensive") -> Dict[str, Any]:
        """
        Use AI 到 选择 optimal 安全 工具 based 在 目标 分析 与 测试 objective.

        参数:
            target: 目标 到 分析
            objective: 测试 objective - "综合", "quick", 或 "stealth"

        返回:
            AI-selected optimal 工具 使用 effectiveness ratings 与 目标 profile
        """
        logger.info(f" Selecting optimal tools for {target} with objective: {objective}")

        data = {
            "target": target,
            "objective": objective
        }
        result = hexstrike_client.safe_post("api/intelligence/select-tools", data)

        if result.get("success"):
            tools = result.get("selected_tools", [])
            logger.info(f" AI selected {len(tools)} optimal tools: {', '.join(tools[:3])}{'...' if len(tools) > 3 else ''}")
        else:
            logger.error(f" Tool selection failed for {target}")

        return result

    @mcp.tool()
    def optimize_tool_parameters_ai(target: str, tool: str, context: str = "{}") -> Dict[str, Any]:
        """
        Use AI 到 optimize 工具 参数 based 在 目标 profile 与 context.

        参数:
            target: 目标 到 测试
            tool: 安全 工具 到 optimize
            context: JSON string 使用 附加 context (stealth, aggressive, etc.)

        返回:
            AI-optimized 参数 用于 maximum effectiveness
        """
        import json

        logger.info(f"  Optimizing parameters for {tool} against {target}")

        try:
            context_dict = json.loads(context) if context != "{}" else {}
        except:
            context_dict = {}

        data = {
            "target": target,
            "tool": tool,
            "context": context_dict
        }
        result = hexstrike_client.safe_post("api/intelligence/optimize-parameters", data)

        if result.get("success"):
            params = result.get("optimized_parameters", {})
            logger.info(f" Parameters optimized for {tool} - {len(params)} parameters configured")
        else:
            logger.error(f" Parameter optimization failed for {tool}")

        return result

    @mcp.tool()
    def create_attack_chain_ai(target: str, objective: str = "comprehensive") -> Dict[str, Any]:
        """
        创建 an 智能 attack chain using AI-driven 工具 sequencing 与 optimization.

        参数:
            target: 目标 用于 the attack chain
            objective: Attack objective - "综合", "quick", 或 "stealth"

        返回:
            AI-generated attack chain 使用 成功 probability 与 time estimates
        """
        logger.info(f"  Creating AI-driven attack chain for {target}")

        data = {
            "target": target,
            "objective": objective
        }
        result = hexstrike_client.safe_post("api/intelligence/create-attack-chain", data)

        if result.get("success"):
            chain = result.get("attack_chain", {})
            steps = len(chain.get("steps", []))
            success_prob = chain.get("success_probability", 0)
            estimated_time = chain.get("estimated_time", 0)

            logger.info(f" Attack chain created - {steps} steps, {success_prob:.2f} success probability, ~{estimated_time}s")
        else:
            logger.error(f" Attack chain creation failed for {target}")

        return result

    @mcp.tool()
    def intelligent_smart_scan(target: str, objective: str = "comprehensive", max_tools: int = 5) -> Dict[str, Any]:
        """
        执行 an 智能 扫描 using AI-driven 工具 selection 与 参数 optimization.

        参数:
            target: 目标 到 扫描
            objective: 扫描 objective - "综合", "quick", 或 "stealth"
            max_tools: Maximum number 的 工具 到 use

        返回:
            结果 来自 AI-optimized 扫描 使用 工具 execution summary
        """
        logger.info(f"{HexStrikeColors.FIRE_RED} Starting intelligent smart scan for {target}{HexStrikeColors.RESET}")

        data = {
            "target": target,
            "objective": objective,
            "max_tools": max_tools
        }
        result = hexstrike_client.safe_post("api/intelligence/smart-scan", data)

        if result.get("success"):
            scan_results = result.get("scan_results", {})
            tools_executed = scan_results.get("tools_executed", [])
            execution_summary = scan_results.get("execution_summary", {})

            # 增强日志 使用 detailed 结果
            logger.info(f"{HexStrikeColors.SUCCESS} Intelligent scan completed for {target}{HexStrikeColors.RESET}")
            logger.info(f"{HexStrikeColors.CYBER_ORANGE} Execution Summary:{HexStrikeColors.RESET}")
            logger.info(f"   • Tools executed: {execution_summary.get('successful_tools', 0)}/{execution_summary.get('total_tools', 0)}")
            logger.info(f"   • Success rate: {execution_summary.get('success_rate', 0):.1f}%")
            logger.info(f"   • Total vulnerabilities: {scan_results.get('total_vulnerabilities', 0)}")
            logger.info(f"   • Execution time: {execution_summary.get('total_execution_time', 0):.2f}s")

            # Log successful 工具
            successful_tools = [t['tool'] for t in tools_executed if t.get('success')]
            if successful_tools:
                logger.info(f"{HexStrikeColors.HIGHLIGHT_GREEN} Successful tools: {', '.join(successful_tools)} {HexStrikeColors.RESET}")

            # Log 失败 工具
            failed_tools = [t['tool'] for t in tools_executed if not t.get('success')]
            if failed_tools:
                logger.warning(f"{HexStrikeColors.HIGHLIGHT_RED} Failed tools: {', '.join(failed_tools)} {HexStrikeColors.RESET}")

            # Log 漏洞 found
            if scan_results.get('total_vulnerabilities', 0) > 0:
                logger.warning(f"{HexStrikeColors.VULN_HIGH} {scan_results['total_vulnerabilities']} vulnerabilities detected!{HexStrikeColors.RESET}")
        else:
            logger.error(f"{HexStrikeColors.ERROR} Intelligent scan failed for {target}: {result.get('error', 'Unknown error')}{HexStrikeColors.RESET}")

        return result

    @mcp.tool()
    def detect_technologies_ai(target: str) -> Dict[str, Any]:
        """
        Use AI 到 detect technologies 与 provide technology-specific 测试 recommendations.

        参数:
            target: 目标 到 分析 用于 technology detection

        返回:
            Detected technologies 使用 AI-generated 测试 recommendations
        """
        logger.info(f" Detecting technologies for {target}")

        data = {"target": target}
        result = hexstrike_client.safe_post("api/intelligence/technology-detection", data)

        if result.get("success"):
            technologies = result.get("detected_technologies", [])
            cms = result.get("cms_type")
            recommendations = result.get("technology_recommendations", {})

            tech_info = f"Technologies: {', '.join(technologies)}"
            if cms:
                tech_info += f", CMS: {cms}"

            logger.info(f" Technology detection completed - {tech_info}")
            logger.info(f" Generated {len(recommendations)} technology-specific recommendations")
        else:
            logger.error(f" Technology detection failed for {target}")

        return result

    @mcp.tool()
    def ai_reconnaissance_workflow(target: str, depth: str = "standard") -> Dict[str, Any]:
        """
        执行 AI-driven 侦察 workflow 使用 智能 工具 chaining.

        参数:
            target: 目标 用于 侦察
            depth: 侦察 depth - "surface", "standard", 或 "deep"

        返回:
            综合 侦察 结果 使用 AI-driven insights
        """
        logger.info(f"  Starting AI reconnaissance workflow for {target} (depth: {depth})")

        # 第一 分析 the 目标
        analysis_result = hexstrike_client.safe_post("api/intelligence/analyze-target", {"target": target})

        if not analysis_result.get("success"):
            return analysis_result

        # 创建 attack chain 用于 侦察
        objective = "comprehensive" if depth == "deep" else "quick" if depth == "surface" else "comprehensive"
        chain_result = hexstrike_client.safe_post("api/intelligence/create-attack-chain", {
            "target": target,
            "objective": objective
        })

        if not chain_result.get("success"):
            return chain_result

        # 执行 the 侦察
        scan_result = hexstrike_client.safe_post("api/intelligence/smart-scan", {
            "target": target,
            "objective": objective,
            "max_tools": 8 if depth == "deep" else 3 if depth == "surface" else 5
        })

        logger.info(f" AI reconnaissance workflow completed for {target}")

        return {
            "success": True,
            "target": target,
            "depth": depth,
            "target_analysis": analysis_result.get("target_profile", {}),
            "attack_chain": chain_result.get("attack_chain", {}),
            "scan_results": scan_result.get("scan_results", {}),
            "timestamp": datetime.now().isoformat()
        }

    @mcp.tool()
    def ai_vulnerability_assessment(target: str, focus_areas: str = "all") -> Dict[str, Any]:
        """
        Perform AI-driven 漏洞 assessment 使用 智能 prioritization.

        参数:
            target: 目标 用于 漏洞 assessment
            focus_areas: Comma-separated focus areas - "web", "网络", "API", "全部"

        返回:
            Prioritized 漏洞 assessment 结果 使用 AI insights
        """
        logger.info(f" Starting AI vulnerability assessment for {target}")

        # 分析 目标 第一
        analysis_result = hexstrike_client.safe_post("api/intelligence/analyze-target", {"target": target})

        if not analysis_result.get("success"):
            return analysis_result

        profile = analysis_result.get("target_profile", {})
        target_type = profile.get("target_type", "unknown")

        # 选择 工具 based 在 focus areas 与 目标 类型
        if focus_areas == "all":
            objective = "comprehensive"
        elif "web" in focus_areas and target_type == "web_application":
            objective = "comprehensive"
        elif "network" in focus_areas and target_type == "network_host":
            objective = "comprehensive"
        else:
            objective = "quick"

        # 执行 漏洞 assessment
        scan_result = hexstrike_client.safe_post("api/intelligence/smart-scan", {
            "target": target,
            "objective": objective,
            "max_tools": 6
        })

        logger.info(f" AI vulnerability assessment completed for {target}")

        return {
            "success": True,
            "target": target,
            "focus_areas": focus_areas,
            "target_analysis": profile,
            "vulnerability_scan": scan_result.get("scan_results", {}),
            "risk_assessment": {
                "risk_level": profile.get("risk_level", "unknown"),
                "attack_surface_score": profile.get("attack_surface_score", 0),
                "confidence_score": profile.get("confidence_score", 0)
            },
            "timestamp": datetime.now().isoformat()
        }

    # ============================================================================
    # 说明：BUG BOUNTY HUNTING SPECIALIZED WORKFLOWS
    # ============================================================================

    @mcp.tool()
    def bugbounty_reconnaissance_workflow(domain: str, scope: str = "", out_of_scope: str = "",
                                        program_type: str = "web") -> Dict[str, Any]:
        """
        创建 综合 侦察 workflow 用于 bug bounty hunting.

        参数:
            domain: 目标 域名 用于 bug bounty
            scope: Comma-separated 列出 的 in-scope domains/IPs
            out_of_scope: Comma-separated 列出 的 out-of-scope domains/IPs
            program_type: 类型 的 program (web, API, mobile, iot)

        返回:
            综合 侦察 workflow 使用 phases 与 工具
        """
        data = {
            "domain": domain,
            "scope": scope.split(",") if scope else [],
            "out_of_scope": out_of_scope.split(",") if out_of_scope else [],
            "program_type": program_type
        }

        logger.info(f" Creating reconnaissance workflow for {domain}")
        result = hexstrike_client.safe_post("api/bugbounty/reconnaissance-workflow", data)

        if result.get("success"):
            workflow = result.get("workflow", {})
            logger.info(f" Reconnaissance workflow created - {workflow.get('tools_count', 0)} tools, ~{workflow.get('estimated_time', 0)}s")
        else:
            logger.error(f" Failed to create reconnaissance workflow for {domain}")

        return result

    @mcp.tool()
    def bugbounty_vulnerability_hunting(domain: str, priority_vulns: str = "rce,sqli,xss,idor,ssrf",
                                       bounty_range: str = "unknown") -> Dict[str, Any]:
        """
        创建 漏洞 hunting workflow prioritized 由 impact 与 bounty potential.

        参数:
            domain: 目标 域名 用于 bug bounty
            priority_vulns: Comma-separated 列出 的 priority 漏洞 types
            bounty_range: Expected bounty range (低, 中, 高, 严重)

        返回:
            漏洞 hunting workflow prioritized 由 impact
        """
        data = {
            "domain": domain,
            "priority_vulns": priority_vulns.split(",") if priority_vulns else [],
            "bounty_range": bounty_range
        }

        logger.info(f" Creating vulnerability hunting workflow for {domain}")
        result = hexstrike_client.safe_post("api/bugbounty/vulnerability-hunting-workflow", data)

        if result.get("success"):
            workflow = result.get("workflow", {})
            logger.info(f" Vulnerability hunting workflow created - Priority score: {workflow.get('priority_score', 0)}")
        else:
            logger.error(f" Failed to create vulnerability hunting workflow for {domain}")

        return result

    @mcp.tool()
    def bugbounty_business_logic_testing(domain: str, program_type: str = "web") -> Dict[str, Any]:
        """
        创建 business logic 测试 workflow 用于 高级 bug bounty hunting.

        参数:
            domain: 目标 域名 用于 bug bounty
            program_type: 类型 的 program (web, API, mobile)

        返回:
            Business logic 测试 workflow 使用 manual 与 automated tests
        """
        data = {
            "domain": domain,
            "program_type": program_type
        }

        logger.info(f" Creating business logic testing workflow for {domain}")
        result = hexstrike_client.safe_post("api/bugbounty/business-logic-workflow", data)

        if result.get("success"):
            workflow = result.get("workflow", {})
            test_count = sum(len(category["tests"]) for category in workflow.get("business_logic_tests", []))
            logger.info(f" Business logic testing workflow created - {test_count} tests")
        else:
            logger.error(f" Failed to create business logic testing workflow for {domain}")

        return result

    @mcp.tool()
    def bugbounty_osint_gathering(domain: str) -> Dict[str, Any]:
        """
        创建 OSINT (Open Source Intelligence) gathering workflow 用于 bug bounty 侦察.

        参数:
            domain: 目标 域名 用于 OSINT gathering

        返回:
            OSINT gathering workflow 使用 multiple intelligence phases
        """
        data = {"domain": domain}

        logger.info(f" Creating OSINT gathering workflow for {domain}")
        result = hexstrike_client.safe_post("api/bugbounty/osint-workflow", data)

        if result.get("success"):
            workflow = result.get("workflow", {})
            phases = len(workflow.get("osint_phases", []))
            logger.info(f" OSINT workflow created - {phases} intelligence phases")
        else:
            logger.error(f" Failed to create OSINT workflow for {domain}")

        return result

    @mcp.tool()
    def bugbounty_file_upload_testing(target_url: str) -> Dict[str, Any]:
        """
        创建 文件 upload 漏洞 测试 workflow 使用 bypass techniques.

        参数:
            target_url: 目标 URL 使用 文件 upload functionality

        返回:
            文件 upload 测试 workflow 使用 malicious 文件 与 bypass techniques
        """
        data = {"target_url": target_url}

        logger.info(f" Creating file upload testing workflow for {target_url}")
        result = hexstrike_client.safe_post("api/bugbounty/file-upload-testing", data)

        if result.get("success"):
            workflow = result.get("workflow", {})
            phases = len(workflow.get("test_phases", []))
            logger.info(f" File upload testing workflow created - {phases} test phases")
        else:
            logger.error(f" Failed to create file upload testing workflow for {target_url}")

        return result

    @mcp.tool()
    def bugbounty_comprehensive_assessment(domain: str, scope: str = "",
                                         priority_vulns: str = "rce,sqli,xss,idor,ssrf",
                                         include_osint: bool = True,
                                         include_business_logic: bool = True) -> Dict[str, Any]:
        """
        创建 综合 bug bounty assessment combining 全部 specialized workflows.

        参数:
            domain: 目标 域名 用于 bug bounty
            scope: Comma-separated 列出 的 in-scope domains/IPs
            priority_vulns: Comma-separated 列出 的 priority 漏洞 types
            include_osint: 说明：Include OSINT gathering workflow
            include_business_logic: Include business logic 测试 workflow

        返回:
            综合 bug bounty assessment 使用 全部 workflows 与 summary
        """
        data = {
            "domain": domain,
            "scope": scope.split(",") if scope else [],
            "priority_vulns": priority_vulns.split(",") if priority_vulns else [],
            "include_osint": include_osint,
            "include_business_logic": include_business_logic
        }

        logger.info(f" Creating comprehensive bug bounty assessment for {domain}")
        result = hexstrike_client.safe_post("api/bugbounty/comprehensive-assessment", data)

        if result.get("success"):
            assessment = result.get("assessment", {})
            summary = assessment.get("summary", {})
            logger.info(f" Comprehensive assessment created - {summary.get('workflow_count', 0)} workflows, ~{summary.get('total_estimated_time', 0)}s")
        else:
            logger.error(f" Failed to create comprehensive assessment for {domain}")

        return result

    @mcp.tool()
    def bugbounty_authentication_bypass_testing(target_url: str, auth_type: str = "form") -> Dict[str, Any]:
        """
        创建 认证 bypass 测试 workflow 用于 bug bounty hunting.

        参数:
            target_url: 目标 URL 使用 认证
            auth_type: 类型 的 认证 (form, jwt, oauth, saml)

        返回:
            认证 bypass 测试 strategies 与 techniques
        """
        bypass_techniques = {
            "form": [
                {"technique": "SQL Injection", "payloads": ["admin'--", "' OR '1'='1'--"]},
                {"technique": "Default Credentials", "payloads": ["admin:admin", "admin:password"]},
                {"technique": "Password Reset", "description": "Test password reset token reuse and manipulation"},
                {"technique": "Session Fixation", "description": "Test session ID prediction and fixation"}
            ],
            "jwt": [
                {"technique": "Algorithm Confusion", "description": "Change RS256 to HS256"},
                {"technique": "None Algorithm", "description": "Set algorithm to 'none'"},
                {"technique": "Key Confusion", "description": "Use public key as HMAC secret"},
                {"technique": "Token Manipulation", "description": "Modify claims and resign token"}
            ],
            "oauth": [
                {"technique": "Redirect URI Manipulation", "description": "Test open redirect in redirect_uri"},
                {"technique": "State Parameter", "description": "Test CSRF via missing/weak state parameter"},
                {"technique": "Code Reuse", "description": "Test authorization code reuse"},
                {"technique": "Client Secret", "description": "Test for exposed client secrets"}
            ],
            "saml": [
                {"technique": "XML Signature Wrapping", "description": "Manipulate SAML assertions"},
                {"technique": "XML External Entity", "description": "Test XXE in SAML requests"},
                {"technique": "Replay Attacks", "description": "Test assertion replay"},
                {"technique": "Signature Bypass", "description": "Test signature validation bypass"}
            ]
        }

        workflow = {
            "target": target_url,
            "auth_type": auth_type,
            "bypass_techniques": bypass_techniques.get(auth_type, []),
            "testing_phases": [
                {"phase": "reconnaissance", "description": "Identify authentication mechanisms"},
                {"phase": "baseline_testing", "description": "Test normal authentication flow"},
                {"phase": "bypass_testing", "description": "Apply bypass techniques"},
                {"phase": "privilege_escalation", "description": "Test for privilege escalation"}
            ],
            "estimated_time": 240,
            "manual_testing_required": True
        }

        logger.info(f" Created authentication bypass testing workflow for {target_url}")

        return {
            "success": True,
            "workflow": workflow,
            "timestamp": datetime.now().isoformat()
        }

    # ============================================================================
    # 增强 HTTP 测试 框架 & 浏览器 AGENT (BURP SUITE ALTERNATIVE)
    # ============================================================================

    @mcp.tool()
    def http_framework_test(url: str, method: str = "GET", data: dict = {},
                           headers: dict = {}, cookies: dict = {}, action: str = "request") -> Dict[str, Any]:
        """
        增强 HTTP 测试 框架 (Burp Suite alternative) 用于 综合 web 安全 测试.

        参数:
            url: 目标 URL 到 测试
            method: HTTP method (获取, POST, PUT, 删除, etc.)
            data: 请求 data/参数
            headers: Custom 请求头
            cookies: 说明：Custom Cookie
            action: Action 到 perform (请求, spider, proxy_history, set_rules, set_scope, repeater, intruder)

        返回:
            HTTP 测试 结果 使用 漏洞 分析
        """
        data_payload = {
            "url": url,
            "method": method,
            "data": data,
            "headers": headers,
            "cookies": cookies,
            "action": action
        }

        logger.info(f"{HexStrikeColors.FIRE_RED} Starting HTTP Framework {action}: {url}{HexStrikeColors.RESET}")
        result = hexstrike_client.safe_post("api/tools/http-framework", data_payload)

        if result.get("success"):
            logger.info(f"{HexStrikeColors.SUCCESS} HTTP Framework {action} completed for {url}{HexStrikeColors.RESET}")

            # 增强日志 用于 漏洞 found
            if result.get("result", {}).get("vulnerabilities"):
                vuln_count = len(result["result"]["vulnerabilities"])
                logger.info(f"{HexStrikeColors.HIGHLIGHT_RED} Found {vuln_count} potential vulnerabilities {HexStrikeColors.RESET}")
        else:
            logger.error(f"{HexStrikeColors.ERROR} HTTP Framework {action} failed for {url}{HexStrikeColors.RESET}")

        return result

    @mcp.tool()
    def browser_agent_inspect(url: str, headless: bool = True, wait_time: int = 5,
                             action: str = "navigate", proxy_port: int = None, active_tests: bool = False) -> Dict[str, Any]:
        """
        AI-powered 浏览器 agent 用于 综合 web application inspection 与 安全 分析.

        参数:
            url: 目标 URL 到 inspect
            headless: Run 浏览器 在 headless 模式
            wait_time: Time 到 wait after page load
            action: Action 到 perform (navigate, screenshot, 关闭, 状态)
            proxy_port: Optional 代理 port 用于 请求 interception
            active_tests: Run 轻量 active reflected XSS tests (safe GET-only)

        返回:
            浏览器 inspection 结果 使用 安全 分析
        """
        data_payload = {
            "url": url,
            "headless": headless,
            "wait_time": wait_time,
            "action": action,
            "proxy_port": proxy_port,
            "active_tests": active_tests
        }

        logger.info(f"{HexStrikeColors.CRIMSON} Starting Browser Agent {action}: {url}{HexStrikeColors.RESET}")
        result = hexstrike_client.safe_post("api/tools/browser-agent", data_payload)

        if result.get("success"):
            logger.info(f"{HexStrikeColors.SUCCESS} Browser Agent {action} completed for {url}{HexStrikeColors.RESET}")

            # 增强日志 用于 安全 分析
            if action == "navigate" and result.get("result", {}).get("security_analysis"):
                security_analysis = result["result"]["security_analysis"]
                issues_count = security_analysis.get("total_issues", 0)
                security_score = security_analysis.get("security_score", 0)

                if issues_count > 0:
                    logger.warning(f"{HexStrikeColors.HIGHLIGHT_YELLOW} Security Issues: {issues_count} | Score: {security_score}/100 {HexStrikeColors.RESET}")
                else:
                    logger.info(f"{HexStrikeColors.HIGHLIGHT_GREEN} No security issues found | Score: {security_score}/100 {HexStrikeColors.RESET}")
        else:
            logger.error(f"{HexStrikeColors.ERROR} Browser Agent {action} failed for {url}{HexStrikeColors.RESET}")

        return result

    # ---------------- 附加 HTTP 框架 工具 (sync 使用 服务端) ----------------
    @mcp.tool()
    def http_set_rules(rules: list) -> Dict[str, Any]:
        """设置 match/replace rules used 到 rewrite parts 的 URL/query/请求头/body before sending.
        Rule format: {'where':'URL|query|请求头|body','pattern':'regex','replacement':'string'}"""
        payload = {"action": "set_rules", "rules": rules}
        return hexstrike_client.safe_post("api/tools/http-framework", payload)

    @mcp.tool()
    def http_set_scope(host: str, include_subdomains: bool = True) -> Dict[str, Any]:
        """Define in-scope host (与 optionally subdomains) so out-of-scope 请求 are skipped."""
        payload = {"action": "set_scope", "host": host, "include_subdomains": include_subdomains}
        return hexstrike_client.safe_post("api/tools/http-framework", payload)

    @mcp.tool()
    def http_repeater(request_spec: dict) -> Dict[str, Any]:
        """Send a crafted 请求 (Burp Repeater equivalent). request_spec keys: URL, method, 请求头, Cookie, data."""
        payload = {"action": "repeater", "request": request_spec}
        return hexstrike_client.safe_post("api/tools/http-framework", payload)

    @mcp.tool()
    def http_intruder(url: str, method: str = "GET", location: str = "query", params: list = None,
                      payloads: list = None, base_data: dict = None, max_requests: int = 100) -> Dict[str, Any]:
        """简单 Intruder (sniper) fuzzing. Iterates payloads over each param individually.
        location: query|body|请求头|Cookie."""
        payload = {
            "action": "intruder",
            "url": url,
            "method": method,
            "location": location,
            "params": params or [],
            "payloads": payloads or [],
            "base_data": base_data or {},
            "max_requests": max_requests
        }
        return hexstrike_client.safe_post("api/tools/http-framework", payload)

    @mcp.tool()
    def burpsuite_alternative_scan(target: str, scan_type: str = "comprehensive",
                                  headless: bool = True, max_depth: int = 3,
                                  max_pages: int = 50) -> Dict[str, Any]:
        """
        Burp Suite 替代扫描入口：整合 HTTP 框架与浏览器代理能力。

        参数:
            target: 待扫描目标 URL/域名
            scan_type: 扫描类型（综合/spider/passive/active）
            headless: 是否无头浏览器模式
            max_depth: 最大爬取深度
            max_pages: 最大分析页面数

        返回:
            综合安全评估结果
        """
        # 与服务端 API 契约保持一致，避免字段命名漂移。
        data_payload = {
            "target": target,
            "scan_type": scan_type,
            "headless": headless,
            "max_depth": max_depth,
            "max_pages": max_pages
        }

        logger.info(f"{HexStrikeColors.BLOOD_RED} 开始 Burp 替代扫描（{scan_type}）：{target}{HexStrikeColors.RESET}")
        result = hexstrike_client.safe_post("api/tools/burpsuite-alternative", data_payload)

        if result.get("success"):
            logger.info(f"{HexStrikeColors.SUCCESS} Burp 替代扫描完成：{target}{HexStrikeColors.RESET}")

            # 结果摘要日志：便于 CLI 快速查看扫描产出。
            if result.get("summary"):
                summary = result["summary"]
                total_vulns = summary.get("total_vulnerabilities", 0)
                pages_analyzed = summary.get("pages_analyzed", 0)
                security_score = summary.get("security_score", 0)

                logger.info(f"{HexStrikeColors.HIGHLIGHT_BLUE} 扫描摘要 {HexStrikeColors.RESET}")
                logger.info(f"   分析页面: {pages_analyzed}")
                logger.info(f"   漏洞数量: {total_vulns}")
                logger.info(f"   安全评分: {security_score}/100")

                # 细分风险等级，方便排定修复优先级。
                vuln_breakdown = summary.get("vulnerability_breakdown", {})
                for severity, count in vuln_breakdown.items():
                    if count > 0:
                        color = {
                            'critical': HexStrikeColors.CRITICAL,
                            'high': HexStrikeColors.FIRE_RED,
                            'medium': HexStrikeColors.CYBER_ORANGE,
                            'low': HexStrikeColors.YELLOW,
                            'info': HexStrikeColors.INFO
                        }.get(severity.lower(), HexStrikeColors.WHITE)

                        logger.info(f"  {color}{severity.upper()}: {count}{HexStrikeColors.RESET}")
        else:
            logger.error(f"{HexStrikeColors.ERROR} Burp 替代扫描失败：{target}{HexStrikeColors.RESET}")

        return result

    @mcp.tool()
    def burp_passive_scan(target: str, headless: bool = True, max_depth: int = 3,
                         max_pages: int = 60, request_limit: int = 60,
                         wait_time: int = 5, include_browser: bool = True,
                         include_subdomains: bool = True, reset_state: bool = True,
                         close_browser: bool = True, output_file: str = "") -> Dict[str, Any]:
        """
        Burp 风格被动扫描（适合复杂系统的低风险渗透测试前期分析）。

        参数:
            target: 目标 URL
            headless: 是否使用无头浏览器
            max_depth: 爬虫深度
            max_pages: 最大爬取页面数
            request_limit: 被动请求分析上限
            wait_time: 浏览器加载等待时间（秒）
            include_browser: 是否启用浏览器运行时被动分析
            include_subdomains: 是否将子域纳入作用域
            reset_state: 是否清空历史扫描状态
            close_browser: 扫描后是否关闭浏览器
            output_file: 结果输出文件（为空则默认写入 /tmp）

        返回:
            被动扫描结果（包含被动发现、严重等级统计、报告路径）
        """
        # 参数透传到服务端统一执行，MCP 只承担编排与展示职责。
        data_payload = {
            "target": target,
            "headless": headless,
            "max_depth": max_depth,
            "max_pages": max_pages,
            "request_limit": request_limit,
            "wait_time": wait_time,
            "include_browser": include_browser,
            "include_subdomains": include_subdomains,
            "reset_state": reset_state,
            "close_browser": close_browser,
            "output_file": output_file,
        }

        logger.info(f"{HexStrikeColors.BLOOD_RED} 开始 Burp 被动扫描：{target}{HexStrikeColors.RESET}")
        result = hexstrike_client.safe_post("api/tools/burp-passive-scan", data_payload)

        if result.get("success"):
            summary = result.get("summary", {})
            total_findings = summary.get("total_findings", 0)
            security_score = summary.get("security_score", 0)
            report_file = result.get("report_file", "")

            logger.info(f"{HexStrikeColors.SUCCESS} Burp 被动扫描完成：{target}{HexStrikeColors.RESET}")
            logger.info(f"{HexStrikeColors.HIGHLIGHT_BLUE} 被动扫描摘要 {HexStrikeColors.RESET}")
            logger.info(f"   发现数量: {total_findings}")
            logger.info(f"   安全评分: {security_score}/100")
            if report_file:
                logger.info(f"   报告文件: {report_file}")
        else:
            logger.error(f"{HexStrikeColors.ERROR} Burp 被动扫描失败：{target}{HexStrikeColors.RESET}")

        return result

    @mcp.tool()
    def burp_forwarded_traffic_analyze(
        traffic: list,
        target: str = "",
        run_safe_verify: bool = True,
        max_verify_requests: int = 20,
        include_subdomains: bool = True,
        reset_state: bool = True,
        output_file: str = ""
    ) -> Dict[str, Any]:
        """
        分析 Burp 转发的数据包，并执行“仅验证”模式的安全复测。

        参数:
            traffic: Burp 转发流量数组，每项包含 请求/响应
            target: 可选目标（用于作用域限制）
            run_safe_verify: 是否执行安全验证请求（仅 获取 + 无害参数）
            max_verify_requests: 安全验证请求上限
            include_subdomains: 作用域是否包含子域
            reset_state: 是否清空历史状态
            output_file: 报告输出文件

        返回:
            带置信度与质量评分的漏洞分析结果
        """
        # traffic 由 Burp 扩展或中间层转发，结构在服务端做兼容解析。
        data_payload = {
            "traffic": traffic,
            "target": target,
            "run_safe_verify": run_safe_verify,
            "max_verify_requests": max_verify_requests,
            "include_subdomains": include_subdomains,
            "reset_state": reset_state,
            "output_file": output_file,
        }

        logger.info(f"{HexStrikeColors.BLOOD_RED} 开始分析 Burp 转发流量{HexStrikeColors.RESET}")
        result = hexstrike_client.safe_post("api/tools/burp-traffic-analyze", data_payload)

        if result.get("success"):
            # 重点输出质量评分，避免只看数量导致误判测试质量。
            summary = result.get("summary", {})
            total_findings = summary.get("total_findings", 0)
            quality_score = summary.get("quality_score", 0)
            report_file = result.get("report_file", "")

            logger.info(f"{HexStrikeColors.SUCCESS} Burp 转发流量分析完成{HexStrikeColors.RESET}")
            logger.info(f"   漏洞总数: {total_findings}")
            logger.info(f"   质量评分: {quality_score}/100")
            if report_file:
                logger.info(f"   报告文件: {report_file}")
        else:
            logger.error(f"{HexStrikeColors.ERROR} Burp 转发流量分析失败{HexStrikeColors.RESET}")

        return result

    @mcp.tool()
    def error_handling_statistics() -> Dict[str, Any]:
        """
        获取 智能 错误 handling 系统 统计 与 最近 错误 patterns.

        返回:
            错误 handling 统计 与 patterns
        """
        logger.info(f"{HexStrikeColors.ELECTRIC_PURPLE} Retrieving error handling statistics{HexStrikeColors.RESET}")
        result = hexstrike_client.safe_get("api/error-handling/statistics")

        if result.get("success"):
            stats = result.get("statistics", {})
            total_errors = stats.get("total_errors", 0)
            recent_errors = stats.get("recent_errors_count", 0)

            logger.info(f"{HexStrikeColors.SUCCESS} Error statistics retrieved{HexStrikeColors.RESET}")
            logger.info(f"   Total Errors: {total_errors}")
            logger.info(f"   Recent Errors: {recent_errors}")

            # Log 错误 breakdown 由 类型
            error_counts = stats.get("error_counts_by_type", {})
            if error_counts:
                logger.info(f"{HexStrikeColors.HIGHLIGHT_BLUE} ERROR BREAKDOWN {HexStrikeColors.RESET}")
                for error_type, count in error_counts.items():
                                          logger.info(f"  {HexStrikeColors.FIRE_RED}{error_type}: {count}{HexStrikeColors.RESET}")
        else:
            logger.error(f"{HexStrikeColors.ERROR} Failed to retrieve error statistics{HexStrikeColors.RESET}")

        return result

    @mcp.tool()
    def test_error_recovery(tool_name: str, error_type: str = "timeout",
                           target: str = "example.com") -> Dict[str, Any]:
        """
        测试 the 智能 错误 恢复 系统 使用 simulated failures.

        参数:
            tool_name: Name 的 工具 到 simulate 错误 用于
            error_type: 类型 的 错误 到 simulate (超时, permission_denied, network_unreachable, etc.)
            target: 目标 用于 the simulated 测试

        返回:
            恢复 策略 与 系统 响应
        """
        data_payload = {
            "tool_name": tool_name,
            "error_type": error_type,
            "target": target
        }

        logger.info(f"{HexStrikeColors.RUBY} Testing error recovery for {tool_name} with {error_type}{HexStrikeColors.RESET}")
        result = hexstrike_client.safe_post("api/error-handling/test-recovery", data_payload)

        if result.get("success"):
            recovery_strategy = result.get("recovery_strategy", {})
            action = recovery_strategy.get("action", "unknown")
            success_prob = recovery_strategy.get("success_probability", 0)

            logger.info(f"{HexStrikeColors.SUCCESS} Error recovery test completed{HexStrikeColors.RESET}")
            logger.info(f"   Recovery Action: {action}")
            logger.info(f"   Success Probability: {success_prob:.2%}")

            # Log alternative 工具 如果 available
            alternatives = result.get("alternative_tools", [])
            if alternatives:
                logger.info(f"   Alternative Tools: {', '.join(alternatives)}")
        else:
            logger.error(f"{HexStrikeColors.ERROR} Error recovery test failed{HexStrikeColors.RESET}")

        return result

    if tool_switch.has_filters():
        enabled_count = len(set(enabled_tools))
        disabled_count = len(set(disabled_tools))
        logger.info(f" MCP 工具开关已生效：启用 {enabled_count} 个，禁用 {disabled_count} 个")
        if tool_switch.enabled_set:
            logger.info(f" 启用名单: {', '.join(sorted(tool_switch.enabled_set))}")
        if tool_switch.disabled_set:
            logger.info(f" 禁用名单: {', '.join(sorted(tool_switch.disabled_set))}")
    if duplicate_skipped_tools:
        logger.warning(f" MCP 工具注册时跳过重复项: {', '.join(sorted(set(duplicate_skipped_tools)))}")

    return mcp

def parse_args():
    """解析 命令 line arguments."""
    parser = argparse.ArgumentParser(description="Run the HexStrike AI MCP Client")
    parser.add_argument("--server", type=str, default=DEFAULT_HEXSTRIKE_SERVER,
                      help=f"HexStrike AI API server URL (default: {DEFAULT_HEXSTRIKE_SERVER})")
    parser.add_argument("--timeout", type=int, default=DEFAULT_REQUEST_TIMEOUT,
                      help=f"Request timeout in seconds (default: {DEFAULT_REQUEST_TIMEOUT})")
    parser.add_argument("--enable-tools", type=str, default=DEFAULT_ENABLE_TOOLS,
                      help="仅启用指定工具（逗号分隔，名称为函数名，如 nmap_scan,gobuster_scan）")
    parser.add_argument("--disable-tools", type=str, default=DEFAULT_DISABLE_TOOLS,
                      help="禁用指定工具（逗号分隔，名称为函数名）")
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args()

def main():
    """主入口 用于 the MCP 服务端."""
    args = parse_args()

    # 配置 logging based 在 debug flag
    if args.debug:
        logger.setLevel(logging.DEBUG)
        logger.debug(" Debug logging enabled")

    # MCP compatibility: No banner 输出 到 avoid JSON parsing issues
    logger.info(f" Starting HexStrike AI MCP Client v6.0")
    logger.info(f" Connecting to: {args.server}")

    try:
        # 初始化 the HexStrike AI 客户端
        hexstrike_client = HexStrikeClient(args.server, args.timeout)

        # 检查 服务端 健康 与 log the 结果
        health = hexstrike_client.check_health()
        if "error" in health:
            logger.warning(f"  Unable to connect to HexStrike AI API server at {args.server}: {health['error']}")
            logger.warning(" MCP server will start, but tool execution may fail")
        else:
            logger.info(f" Successfully connected to HexStrike AI API server at {args.server}")
            logger.info(f" Server health status: {health['status']}")
            logger.info(f" Version: {health.get('version', 'unknown')}")
            if not health.get("all_essential_tools_available", False):
                logger.warning("  Not all essential tools are available on the HexStrike server")
                missing_tools = [tool for tool, available in health.get("tools_status", {}).items() if not available]
                if missing_tools:
                    logger.warning(f" Missing tools: {', '.join(missing_tools[:5])}{'...' if len(missing_tools) > 5 else ''}")

        # 设置 up 与 run the MCP 服务端
        tool_switch = MCPToolSwitch(
            enable_tools=args.enable_tools,
            disable_tools=args.disable_tools,
        )
        mcp = setup_mcp_server(hexstrike_client, tool_switch)
        logger.info(" Starting HexStrike AI MCP server")
        logger.info(" Ready to serve AI agents with enhanced cybersecurity capabilities")

        # 显式使用 stdio，避免不同 FastMCP 版本默认传输模式不一致导致客户端握手失败。
        try:
            mcp.run(transport="stdio")
        except TypeError:
            # 兼容老版本 FastMCP，不支持 transport 参数时回退默认启动方式。
            mcp.run()
    except Exception as e:
        logger.error(f" Error starting MCP server: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        sys.exit(1)

if __name__ == "__main__":
    main()
