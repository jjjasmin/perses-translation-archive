import os
import json
import subprocess
import urllib.request
from dotenv import load_dotenv  # ★追加

# ルート階層にある .env を確実に読み込む
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
dotenv_path = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path)

def push_to_github():
    """data/transcripts, data/videos.json, pipeline_status.json に差分がある場合のみGitへ自動プッシュ"""
    target_files = ["data/transcripts", "data/videos.json", "pipeline_status.json"]
    try:
        status = subprocess.check_output(
            ["git", "status", "--porcelain"] + target_files,
            text=True
        ).strip()

        if not status:
            print("ℹ️ Git: 対象ファイルに変更がないため、プッシュをスキップします。")
            return False

        print("🚀 Git: 変更を検知しました。GitHubへ自動プッシュを開始します...")
        subprocess.run(["git", "add"] + target_files, check=True)
        
        commit_msg = "auto: update transcripts, videos.json, and pipeline_status"
        subprocess.run(["git", "commit", "-m", commit_msg], check=True)
        subprocess.run(["git", "push", "origin", "main"], check=True)

        print("✅ Git: GitHubへの自動プッシュが正常に完了しました！")
        return True

    except subprocess.CalledProcessError as e:
        print(f"⚠️ Git: 自動プッシュ中にエラーが発生しました: {e}")
        return False


def trigger_cloudflare_build_if_needed(processed_count: int, threshold: int = 3):
    """今回完了した動画数が threshold (3件) 以上の場合のみ Deploy Hook を叩く"""
    if processed_count < threshold:
        print(f"ℹ️ Cloudflare: 今回完了した動画は {processed_count} 件です（閾値: {threshold} 件）。自動ビルドをスキップします。")
        return

    deploy_hook_url = os.getenv("CLOUDFLARE_DEPLOY_HOOK_URL")
    if not deploy_hook_url:
        print("⚠️ Cloudflare: CLOUDFLARE_DEPLOY_HOOK_URL が .env に設定されていないためスキップします。")
        return

    print(f"🚀 Cloudflare: {processed_count} 件の更新が完了したため、Deploy Hook を呼び出して自動ビルドを開始します...")
    
    try:
        req = urllib.request.Request(deploy_hook_url, method="POST")
        with urllib.request.urlopen(req) as response:
            res_body = response.read().decode("utf-8")
            res_json = json.loads(res_body)
            if res_json.get("success"):
                print("🎉 Cloudflare: 自動ビルドが正常に起動しました！")
            else:
                print(f"⚠️ Cloudflare: ビルド起動レスポンスエラー: {res_body}")
    except Exception as e:
        print(f"❌ Cloudflare: Deploy Hook 呼び出しエラー: {e}")