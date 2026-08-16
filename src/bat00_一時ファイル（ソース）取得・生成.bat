@echo off
chcp 65001 > nul
echo 🚀 YOUTUBEから概要と字幕を取得し、一時ファイル（ソース）生成 (00_fetch_sources.py) を開始します...
echo.

python 00_fetch_sources.py

echo.
echo ------------------------------------------
echo 処理が完了しました。何かキーを押すと閉じます。
pause > nul