import os
import sys
import importlib
from yt_dlp import YoutubeDL

# 取得したい再生リストやチャンネルのURL
TARGET_SOURCES = [
    "https://www.youtube.com/@PERSES_OFFICIAL/videos",  # 例: チャンネル動画一覧
    # "https://www.youtube.com/playlist?list=PLxxxxxx", # 例: 再生リスト
]

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

if __name__ == "__main__":
    urls = fetch_all_video_urls(TARGET_SOURCES)

    if not urls:
        print("❌ 有効な動画URLが取得できませんでした。")
        sys.exit(0)

    # 01_generate_raw_chunks.py を呼び出し
    step01 = importlib.import_module("01_generate_raw_chunks")

    print("\n🚀 01_generate_raw_chunks.py を実行します...\n")
    step01.TARGET_URLS = urls