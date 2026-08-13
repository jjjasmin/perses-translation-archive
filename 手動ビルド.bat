@echo off
chcp 65001 > nul
echo Cloudflare Pages の手動ビルドを開始しています...
echo.

curl.exe -X POST "https://api.cloudflare.com/client/v4/pages/webhooks/deploy_hooks/715b1620-5721-49f7-94e3-4262a4ea9c4f"
echo.
echo.
echo ------------------------------------------
echo 処理が完了しました。何かキーを押すと閉じます。
pause > nul