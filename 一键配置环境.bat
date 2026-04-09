@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title YouTube 看板 - 一键配置环境
set "PYTHON_CMD="
where python >nul 2>nul && set "PYTHON_CMD=python"
if not defined PYTHON_CMD (
  where py >nul 2>nul && set "PYTHON_CMD=py"
)
if not defined PYTHON_CMD (
  echo 未找到 Python。
  echo 请先安装 Python，并在安装时勾选 Add Python to PATH。
  echo 下载地址：https://www.python.org/downloads/windows/
  pause
  exit /b 1
)
echo 正在初始化项目环境...
%PYTHON_CMD% setup_project.py
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if not "%EXIT_CODE%"=="0" (
  echo 环境初始化失败，请根据上面的报错处理。
  pause
  exit /b %EXIT_CODE%
)
echo 环境初始化完成。
pause
