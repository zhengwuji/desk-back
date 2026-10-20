# -*- coding: utf-8 -*-
"""重写根目录两个启动脚本。

刻意做到「整份 .bat 里没有任何非 ASCII 字符」：
  · 不写死中文 exe 名，改用 `%~dp0*.exe` 通配（根目录只放一个 exe）
  · 提权时用环境变量 DLK_EXE 把路径传给 PowerShell，避免中文出现在命令行里
这样文件编码是 ASCII，cmd 用 936 还是 65001 读都不会乱码。

模板里 `%~dp0`、`%%F` 这种字符不能让 % 格式化碰到，所以统一用 __占位符__ + replace。

只有改启动参数时才需要重跑：
    python _dev/_write_launchers.py
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = r"src\desktop_layout_keeper.py"

FIND_EXE = "\n".join([
    'set "DLK_EXE="',
    'for %%F in ("%~dp0*.exe") do set "DLK_EXE=%%~fF"',
])

# 注意：所有判断都用 `if defined` / `if errorlevel` / `&&` 这类**运行时**求值的形式，
# 不要写 `if %errorlevel%==0 ( ... )` —— 在括号块里 %errorlevel% 会在整块解析时就被
# 提前展开，拿到的是上一次命令的旧值，会误判甚至掉进 pause 把脚本挂死。

NORMAL = """@echo off
rem DesktopLayoutKeeper - normal launch (prefers the packaged exe)
rem ASCII-only on purpose: any console code page reads this file correctly.
cd /d "%~dp0"

__FIND__

if defined DLK_EXE (
    start "" "%DLK_EXE%"
    exit /b 0
)

where pythonw >nul 2>nul && (
    start "" pythonw "__SRC__"
    exit /b 0
)
where pyw >nul 2>nul && (
    start "" pyw "__SRC__"
    exit /b 0
)

echo.
echo No exe next to this script, and no pythonw.exe on PATH.
echo Run "python build.py" once to produce the exe, or install Python 3.8+.
echo.
pause
"""

ELEVATED = """@echo off
rem DesktopLayoutKeeper - launch elevated (UAC prompt)
rem ASCII-only on purpose: any console code page reads this file correctly.
cd /d "%~dp0"

__FIND__

if defined DLK_EXE goto :USE_EXE
goto :USE_SRC

:USE_EXE
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "Start-Process -FilePath $env:DLK_EXE -Verb RunAs -WorkingDirectory (Get-Location).Path"
if errorlevel 1 goto :CANCELLED
exit /b 0

:USE_SRC
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
 "$exe = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source; if (-not $exe) { $exe = (Get-Command pyw.exe -ErrorAction SilentlyContinue).Source }; if (-not $exe) { Write-Host 'pythonw.exe not found.'; exit 1 }; Start-Process -FilePath $exe -ArgumentList '__SRC__' -Verb RunAs -WorkingDirectory (Get-Location).Path"
if errorlevel 1 goto :CANCELLED
exit /b 0

:CANCELLED
echo.
echo Elevation cancelled or failed. You can also right-click the target and pick "Run as administrator".
echo.
pause
exit /b 1
"""


def render(template: str) -> str:
    text = template.replace("__FIND__", FIND_EXE).replace("__SRC__", SRC)
    return text.replace("\n", "\r\n")


def write(name: str, template: str) -> None:
    path = os.path.join(ROOT, name)
    data = render(template)
    assert data.isascii(), "启动脚本里不允许出现非 ASCII 字符"
    with open(path, "wb") as f:                      # ASCII，任何代码页都读得对
        f.write(data.encode("ascii"))
    print("写入 %-22s %6d 字节  纯 ASCII" % (name, os.path.getsize(path)))


if __name__ == "__main__":
    write("启动.bat", NORMAL)
    write("以管理员身份启动.bat", ELEVATED)
