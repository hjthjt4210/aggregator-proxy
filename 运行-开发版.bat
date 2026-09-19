@echo off
rem 开发阶段用：用项目虚拟环境启动桌面窗口（正式版将打包为 聚合代理.exe）
cd /d "%~dp0"
".venv\Scripts\python.exe" app.py
