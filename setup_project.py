"""Portable bootstrap for the public-data YouTube dashboard."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


DEFAULT_ACCOUNTS: list[dict[str, str]] = []


def render_env_file(api_key: str) -> str:
    return (
        "# Local YouTube public dashboard credentials\n"
        "# Fill this value with your YouTube Data API v3 key.\n"
        f"YT_API_KEY={api_key}\n"
    )


def prompt_value(label: str, current: str = "", required: bool = False) -> str:
    suffix = f" [{current}]" if current else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if current:
            return current
        if not required:
            return ""
        print(f"{label} 不能为空。")


def parse_simple_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def bootstrap_workspace(base_dir: Path, *, api_key: str, overwrite_env: bool = False) -> dict[str, bool]:
    (base_dir / "output" / "playwright").mkdir(parents=True, exist_ok=True)
    (base_dir / "snapshots").mkdir(exist_ok=True)

    env_example = base_dir / ".env.example"
    env_example.write_text(
        render_env_file(api_key="your_youtube_data_api_key"),
        encoding="utf-8",
    )

    env_path = base_dir / ".env"
    env_created = False
    if overwrite_env or not env_path.exists():
        env_path.write_text(render_env_file(api_key), encoding="utf-8")
        env_created = True

    accounts_path = base_dir / "accounts.json"
    accounts_created = False
    if not accounts_path.exists():
        accounts_path.write_text(json.dumps(DEFAULT_ACCOUNTS, ensure_ascii=False, indent=2), encoding="utf-8")
        accounts_created = True

    return {"envCreated": env_created, "accountsCreated": accounts_created}


def create_windows_shortcuts(base_dir: Path) -> None:
    start_bat = base_dir / "启动看板.bat"
    start_bat.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        "chcp 65001 >nul\r\n"
        "cd /d \"%~dp0\"\r\n"
        "title YouTube 看板 - 启动服务\r\n"
        "set \"PYTHON_CMD=\"\r\n"
        "where python >nul 2>nul && set \"PYTHON_CMD=python\"\r\n"
        "if not defined PYTHON_CMD (\r\n"
        "  where py >nul 2>nul && set \"PYTHON_CMD=py\"\r\n"
        ")\r\n"
        "if not defined PYTHON_CMD (\r\n"
        "  echo 未找到 Python。\r\n"
        "  echo 请先安装 Python，并在安装时勾选 Add Python to PATH。\r\n"
        "  pause\r\n"
        "  exit /b 1\r\n"
        ")\r\n"
        "echo 正在检查项目环境...\r\n"
        "%PYTHON_CMD% setup_project.py --ensure-only\r\n"
        "if errorlevel 1 goto :fail\r\n"
        "echo 正在启动本地看板服务...\r\n"
        "start \"YouTube Dashboard Server\" powershell -NoExit -ExecutionPolicy Bypass -Command \"Set-Location -LiteralPath '%CD%'; %PYTHON_CMD% youtube_dashboard_app.py --port 8130\"\r\n"
        "timeout /t 3 >nul\r\n"
        "start \"\" http://127.0.0.1:8130/\r\n"
        "echo 看板服务已启动，浏览器将自动打开。\r\n"
        "pause\r\n"
        "exit /b 0\r\n"
        ":fail\r\n"
        "echo 启动失败，请先执行“一键配置环境.bat”完成初始化。\r\n"
        "pause\r\n"
        "exit /b 1\r\n",
        encoding="utf-8",
    )

    setup_bat = base_dir / "一键配置环境.bat"
    setup_bat.write_text(
        "@echo off\r\n"
        "setlocal\r\n"
        "chcp 65001 >nul\r\n"
        "cd /d \"%~dp0\"\r\n"
        "title YouTube 看板 - 一键配置环境\r\n"
        "set \"PYTHON_CMD=\"\r\n"
        "where python >nul 2>nul && set \"PYTHON_CMD=python\"\r\n"
        "if not defined PYTHON_CMD (\r\n"
        "  where py >nul 2>nul && set \"PYTHON_CMD=py\"\r\n"
        ")\r\n"
        "if not defined PYTHON_CMD (\r\n"
        "  echo 未找到 Python。\r\n"
        "  echo 请先安装 Python，并在安装时勾选 Add Python to PATH。\r\n"
        "  echo 下载地址：https://www.python.org/downloads/windows/\r\n"
        "  pause\r\n"
        "  exit /b 1\r\n"
        ")\r\n"
        "echo 正在初始化项目环境...\r\n"
        "%PYTHON_CMD% setup_project.py\r\n"
        "set \"EXIT_CODE=%ERRORLEVEL%\"\r\n"
        "echo.\r\n"
        "if not \"%EXIT_CODE%\"==\"0\" (\r\n"
        "  echo 环境初始化失败，请根据上面的报错处理。\r\n"
        "  pause\r\n"
        "  exit /b %EXIT_CODE%\r\n"
        ")\r\n"
        "echo 环境初始化完成。\r\n"
        "pause\r\n",
        encoding="utf-8",
    )


def ensure_command(name: str) -> bool:
    return shutil.which(name) is not None


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap the local YouTube public dashboard project.")
    parser.add_argument("--ensure-only", action="store_true", help="Only ensure folders and template files exist.")
    parser.add_argument("--api-key", default="", help="YouTube Data API key.")
    parser.add_argument("--overwrite-env", action="store_true", help="Overwrite the existing .env file.")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    create_windows_shortcuts(base_dir)

    if not ensure_command("python"):
        print("未找到 Python，请先安装 Python 并加入 PATH。")
        return 1
    if not ensure_command("node") or not ensure_command("npx"):
        print("未找到 Node.js / npx，请先安装 Node.js。")
        return 1

    current_env = parse_simple_env(base_dir / ".env")
    api_key = args.api_key or current_env.get("YT_API_KEY", "")

    if not args.ensure_only:
        api_key = prompt_value("请输入 YT_API_KEY", api_key, required=False)

    result = bootstrap_workspace(
        base_dir,
        api_key=api_key,
        overwrite_env=args.overwrite_env or (not (base_dir / ".env").exists() and not args.ensure_only),
    )

    print("项目初始化完成。")
    print(f"工作目录: {base_dir}")
    print(f".env {'已创建/更新' if result['envCreated'] else '已保留'}")
    print(f"accounts.json {'已创建' if result['accountsCreated'] else '已保留'}")
    print("已生成快捷脚本：启动看板.bat / 一键配置环境.bat")
    if not api_key:
        print("提示：当前 .env 仍缺少 YT_API_KEY。首次使用前请先执行“一键配置环境.bat”补齐。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
