@echo off
chcp 65001 > nul
echo 🚀 全自動パイプライン (00_fetch_urls.py) を開始します...
echo.

python 00_fetch_urls.py

echo.
echo ------------------------------------------
echo 処理が完了しました。何かキーを押すと閉じます。
pause > nul