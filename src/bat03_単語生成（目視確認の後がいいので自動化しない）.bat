@echo off
chcp 65001 > nul
echo 単語データの追加 (03_add_words_to_json.py) を開始します...
echo.

python 03_add_words_to_json.py

echo.
echo ------------------------------------------
echo 処理が完了しました。何かキーを押すと閉じます。
pause > nul