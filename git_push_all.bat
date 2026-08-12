@echo off
chcp 65001 > nul
echo 🚀 全ファイルの変更を main ブランチへプッシュします...
echo.

:: すべての変更をステージング
git add .

:: コミット（変更がない場合はスキップされます）
git commit -m "manual: push all changes to main"

:: mainブランチへプッシュ
git push origin main

echo.
echo ------------------------------------------
echo 処理が完了しました。何かキーを押すと閉じます。
pause > nul