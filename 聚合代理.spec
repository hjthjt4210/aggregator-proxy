# -*- mode: python ; coding: utf-8 -*-
"""聚合代理 Windows onedir 发布配置。

发布后布局（均以 EXE 所在目录为根）：
  <发布目录>\聚合代理.exe
  <发布目录>\_internal\   PyInstaller 运行库
  <发布目录>\data\         用户配置（首次运行自动创建）
  <发布目录>\logs\         本地日志（首次运行自动创建）
"""
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = []
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("fastapi")
hiddenimports += collect_submodules("starlette")
hiddenimports += collect_submodules("anyio")

analysis = Analysis(
    ["app.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "unittest", "tkinter"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="聚合代理",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="聚合代理",
)
