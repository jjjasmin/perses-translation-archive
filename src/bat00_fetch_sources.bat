@echo off
chcp 65001 > nul
echo 🛠️ YOUTUBEから概要と字幕の取得 (00_fetch_sources.py) を開始します...
echo.

python 00_fetch_sources.py

echo.
echo ------------------------------------------
echo 処理が完了しました。何かキーを押すと閉じます。
pause > nul