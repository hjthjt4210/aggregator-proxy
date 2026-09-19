@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [错误] 未找到 .venv\Scripts\python.exe
  pause
  exit /b 1
)

echo [1/3] 清理上次构建产物...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "聚合代理.exe" del /q "聚合代理.exe"
if exist "_internal" rmdir /s /q "_internal"

echo [2/3] 构建 Windows 发布版...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean "聚合代理.spec"
if errorlevel 1 (
  echo [错误] 打包失败。
  pause
  exit /b 1
)

echo [3/3] 整理发布目录...
move /y "dist\聚合代理\聚合代理.exe" ".\聚合代理.exe" >nul
move /y "dist\聚合代理\_internal" ".\_internal" >nul
rmdir /s /q dist

echo.
echo [完成] 发布文件已生成：
 echo   %cd%\聚合代理.exe
 echo   %cd%\_internal\
 echo   %cd%\data\
 echo   %cd%\logs\
echo.
pause
