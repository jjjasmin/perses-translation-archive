@echo off
chcp 65001 > nul
echo ★通常モード(FLUSH)★タイ語字幕と概要の一時ファイルから翻訳文一時ファイルを生成 (01_generate_raw_chunks.py) を開始します...
echo.

python 01_generate_raw_chunks.py

echo.
echo ------------------------------------------
echo 処理が完了しました。何かキーを押すと閉じます。
pause > nul