@echo off
chcp 65001 > nul
echo 🚀 GitHub への手動プッシュを開始します...
echo.

python -c "from git_utils import push_to_github; push_to_github()"

echo.
echo ------------------------------------------
echo 処理が完了しました。何かキーを押すと閉じます。
pause > nul