@echo off
chcp 65001 > nul
echo Cloudflare Pages の手動ビルドを開始しています...
echo.

curl.exe -X POST "https://api.cloudflare.com/client/v4/pages/webhooks/deploy_hooks/c8549dac-a02c-4bea-80fd-5eb60b4a62ba"

echo.
echo.
echo ------------------------------------------
echo 処理が完了しました。何かキーを押すと閉じます。
pause > nul