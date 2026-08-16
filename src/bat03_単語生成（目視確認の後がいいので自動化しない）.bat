@echo off
chcp 65001 > nul
echo 🛠️ 手動修正データのビルド (02_build_final_json.py) を開始します...
echo.

python 02_build_final_json.py

echo.
echo ------------------------------------------
echo 処理が完了しました。何かキーを押すと閉じます。
pause > nul