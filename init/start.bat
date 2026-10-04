@echo off
REM YouTube Downloader 一键启动（Windows 便捷入口）
REM 双击本文件即可运行 init\start.ps1，已带上 -ExecutionPolicy Bypass 绕过脚本执行策略限制。
powershell -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
