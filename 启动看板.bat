@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title YouTube 看板 - 启动服务
set "PYTHON_CMD="
where python >nul 2>nul && set "PYTHON_CMD=python"
if not defined PYTHON_CMD (
  where py >nul 2>nul && set "PYTHON_CMD=py"
)
if not defined PYTHON_CMD (
  echo 未找到 Python。
  echo 请先安装 Python，并在安装时勾选 Add Python to PATH。
  pause
  exit /b 1
)
echo 正在检查项目环境...
%PYTHON_CMD% setup_project.py --ensure-only
if errorlevel 1 goto :fail
echo 正在启动本地看板服务...
start "YouTube Dashboard Server" powershell -NoExit -ExecutionPolicy Bypass -Command "Set-Location -LiteralPath '%CD%'; %PYTHON_CMD% youtube_dashboard_app.py --port 8130"
timeout /t 3 >nul
start "" http://127.0.0.1:8130/
echo 看板服务已启动，浏览器将自动打开。
pause
exit /b 0
:fail
echo 启动失败，请先执行“一键配置环境.bat”完成初始化。
pause
exit /b 1
