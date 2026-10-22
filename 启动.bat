@echo off
chcp 65001 >nul
title AI 作文批改助手
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.10 或更高版本
    echo 安装时请勾选 "Add Python to PATH" 选项
    pause
    exit /b 1
)

python -c "import flask, pypdf, requests, reportlab, openpyxl, docx, waitress" >nul 2>nul
if errorlevel 1 (
    echo 首次运行，正在安装依赖（需要联网，约 1-2 分钟）...
    python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo [错误] 依赖安装失败，请检查网络后重试
        pause
        exit /b 1
    )
)

echo 正在启动服务，浏览器将自动打开...
echo 按 Ctrl+C 可停止服务
echo.
python app.py
pause
