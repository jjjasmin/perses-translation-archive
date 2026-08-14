import json
import os
import re
import sys
import time
import random
from yt_dlp import YoutubeDL
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    CouldNotRetrieveTranscript,
)
from logger import setup_logger

# 取得したい再生リストやチャンネルのURL
TARGET_SOURCES = [
    "https://www.youtube.com/playlist?list=PLeucYdwFQU5Q&si=ajobTeQpmmZiQq3S", # 最優先
    "https://www.youtube.com/playlist?list=PLKEeHm120d8k-KF25SU907XjSvI8ImEXG", # PERSES ARCHIVE
    "https://www.youtube.com/playlist?list=PLXZZW-VJ5hCs&si=ETLN-YlraZb8XmSE", # 001翻訳する動画リスト
    "https://www.youtube.com/@PERSES_OFFICIAL/videos",  # PERSES　例: チャンネル動画一覧(ショート除く)
    # "https://www.youtube.com/@PERSES_OFFICIAL/shorts",  # 例: チャンネル動画一覧(ショートのみ)
    # "https://www.youtube.com/watch?v=NvrHLb-4lkk",  # 特定の個別の動画だけを指定
]

# --- [追加] パス定義と temp ディレクトリの自動作成 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "data"))
TEMP_DIR = os.path.join(DATA_DIR, "temp")
os.makedirs(TEMP_DIR, exist_ok=True)

def extract_video_id(url: str) -> str:
    match = re.search(r"(?:v=|\/([0-9A-Za-z_-]{11})(?:[?&]|$)|youtu\.be\/)([0-9A-Za-z_-]{11})", url)
    return match.group(1) or match.group(2) if match else ""

def fetch_all_video_urls(sources):
    video_urls = []
    ydl_opts = {
        'extract_flat': True,
        'quiet': True,
        'skip_download': True,
    }

    print("🔍 YouTubeから動画URLを取得中...")
    with YoutubeDL(ydl_opts) as ydl:
        for source in sources:
            try:
                info = ydl.extract_info(source, download=False)
                if 'entries' in info:
                    for entry in info['entries']:
                        if entry and 'url' in entry:
                            video_urls.append(entry['url'])
                else:
                    video_urls.append(info.get('webpage_url', source))
            except Exception as e:
                print(f"⚠️ URL取得エラー ({source}): {e}")

    unique_urls = list(dict.fromkeys(video_urls))
    print(f"🎉 計 {len(unique_urls)} 件の動画URLを取得しました！")
    return unique_urls

def get_video_metadata(video_id: str):
    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {"quiet": True, "skip_download": True, "no_warnings": True}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info.get("title", ""), info.get("upload_date", ""), info.get("tags", []) or []

def fetch_transcript(video_id: str):
    try:
        ytt_api = YouTubeTranscriptApi()
        if hasattr(ytt_api, "list"):
            transcript_list = ytt_api.list(video_id)
        elif hasattr(ytt_api, "list_transcripts"):
            transcript_list = ytt_api.list_transcripts(video_id)
        else:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
    except AttributeError:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

    # 優先順位 1: タイ語字幕（公式手動）
    try:
        return transcript_list.find_manually_created_transcript(["th"]).fetch()
    except Exception:
        pass

    # 優先順位 2: タイ語字幕（自動生成）
    try:
        return transcript_list.find_generated_transcript(["th"]).fetch()
    except Exception:
        pass

    # 優先順位 3: 英語字幕（公式手動）
    try:
        return transcript_list.find_manually_created_transcript(["en"]).fetch()
    except Exception:
        pass

    raise RuntimeError("対象の字幕（タイ語手動・タイ語自動・英語手動）が見つかりませんでした。")

def main():
    setup_logger()

    urls = fetch_all_video_urls(TARGET_SOURCES)
    if not urls:
        print("❌ 有効な動画URLが取得できませんでした。")
        sys.exit(0)

    # テスト用件数調整（必要に応じて変更）
    urls = urls[:3]
    saved_video_ids = []

    print("\n📥 各動画のメタデータ・字幕の事前取得を開始します...")

    for idx, url in enumerate(urls, 1):
        video_id = extract_video_id(url)
        if not video_id:
            continue

        temp_source_file = os.path.join(TEMP_DIR, f"temp_source_{video_id}.json")
        if os.path.exists(temp_source_file):
            print(f"⏩ [{idx}/{len(urls)}] VIDEO_ID: {video_id} の一時ファイルは存在するためスキップします。")
            saved_video_ids.append(video_id)
            continue

        # IP制限対策: 1件ごとに3分〜5分（180〜300秒）のランダム待機
        if idx > 1:
            wait_sec = random.uniform(180, 300)
            print(f"☕ IP制限回避のため {wait_sec:.1f} 秒待機します...")
            time.sleep(wait_sec)

        print(f"\n==========================================")
        print(f"🎬 取得中 [{idx}/{len(urls)}]: VIDEO_ID = {video_id}")
        print(f"==========================================")

        try:
            original_title, upload_date, raw_tags = get_video_metadata(video_id)
            fetched = fetch_transcript(video_id)

            transcript_list = []
            for t_idx, item in enumerate(fetched, 1):
                if isinstance(item, dict):
                    start_val = item.get("start", 0.0)
                    duration_val = item.get("duration", 0.0)
                    text_val = item.get("text", "")
                else:
                    start_val = getattr(item, "start", 0.0)
                    duration_val = getattr(item, "duration", 0.0)
                    text_val = getattr(item, "text", "")

                transcript_list.append({
                    "id": t_idx,
                    "start": round(start_val, 2),
                    "end": round(start_val + duration_val, 2),
                    "text": re.sub(r'>>\s*', '', str(text_val)).strip()
                })

            with open(temp_source_file, "w", encoding="utf-8") as f:
                json.dump({
                    "video_id": video_id,
                    "original_title": original_title,
                    "upload_date": upload_date,
                    "raw_tags": raw_tags,
                    "transcript": transcript_list
                }, f, ensure_ascii=False, indent=2)

            print(f"💾 一時ファイルを保存しました: {temp_source_file}")
            saved_video_ids.append(video_id)

        except (TranscriptsDisabled, NoTranscriptFound, CouldNotRetrieveTranscript):
            print(f"⏩ 💡 スキップ: VIDEO_ID = {video_id} は字幕が無効または存在しません。")
            continue
        except Exception as e:
            err_first_line = str(e).splitlines()[0] if str(e) else "不明なエラー"
            print(f"❌ 字幕データ取得失敗 (VIDEO_ID = {video_id}): {err_first_line}")
            continue

    print(f"\n🎉 計 {len(saved_video_ids)} 件の事前データ蓄積が完了しました！")

    if not saved_video_ids:
        print("❌ 処理可能な動画データが存在しません。")
        sys.exit(0)

    # 01_generate_raw_chunks を呼び出して翻訳フェーズへ移行
    import importlib
    step01 = importlib.import_module("01_generate_raw_chunks")
    from git_utils import push_to_github, trigger_cloudflare_build_if_needed

    print("\n🚀 01_generate_raw_chunks.py を実行します...\n")
    completed_count = step01.main(saved_video_ids)

    # 処理完了後にGitプッシュ & Cloudflareビルド判定
    push_to_github()
    trigger_cloudflare_build_if_needed(completed_count, threshold=3)

if __name__ == "__main__":
    main()