import json
import os
import re
import sys
import time
import random
import urllib.request
import urllib.error
from yt_dlp import YoutubeDL
from logger import setup_logger

# 取得したい再生リストやチャンネルのURL
TARGET_SOURCES = [
    #"https://www.youtube.com/playlist?list=PLeucYdwFQU5Q&si=ajobTeQpmmZiQq3S", # 最優先
    #"https://www.youtube.com/playlist?list=PLKEeHm120d8k-KF25SU907XjSvI8ImEXG", # PERSES ARCHIVE
    #"https://www.youtube.com/playlist?list=PLXZZW-VJ5hCs&si=ETLN-YlraZb8XmSE", # 001翻訳する動画リスト
    "https://www.youtube.com/@PERSES_OFFICIAL/videos",  # PERSES 例: チャンネル動画一覧(ショート除く)
    # "https://www.youtube.com/@PERSES_OFFICIAL/shorts",  # 例: チャンネル動画一覧(ショートのみ)
    # "https://www.youtube.com/watch?v=NvrHLb-4lkk",  # 特定の個別の動画だけを指定
]

# --- パス定義と ディレクトリ設定 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "data"))
TEMP_DIR = os.path.join(DATA_DIR, "temp")
ARCHIVE_DIR = os.path.join(DATA_DIR, "temp_archive")
URL_TXT_FILE = os.path.join(DATA_DIR, "urls.txt")
URL_CACHE_FILE = os.path.join(TEMP_DIR, "fetched_urls.json")
NO_TRANSCRIPT_FILE = os.path.join(TEMP_DIR, "no_transcript_ids.json")
COOKIE_FILE = os.path.join(DATA_DIR, "cookies.txt")

os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(ARCHIVE_DIR, exist_ok=True)

def load_no_transcript_ids() -> set:
    if os.path.exists(NO_TRANSCRIPT_FILE):
        try:
            with open(NO_TRANSCRIPT_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_no_transcript_id(video_id: str):
    no_ids = load_no_transcript_ids()
    no_ids.add(video_id)
    with open(NO_TRANSCRIPT_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(no_ids)), f, ensure_ascii=False, indent=2)

def extract_video_id(url: str) -> str:
    match = re.search(r"(?:v=|\/([0-9A-Za-z_-]{11})(?:[?&]|$)|youtu\.be\/)([0-9A-Za-z_-]{11})", url)
    return match.group(1) or match.group(2) if match else ""

def is_rate_limit_error(e: Exception) -> bool:
    """429 Too Many Requests や Rate Limit エラーかどうかを判定"""
    err_str = str(e).lower()
    if isinstance(e, urllib.error.HTTPError) and e.code in (429, 403):
        return True
    if "429" in err_str or "too many requests" in err_str or "rate limit" in err_str or "http error 429" in err_str:
        return True
    return False



def fetch_all_video_urls(sources):
    """
    1. urls.txt（ブックマークレット出力）
    2. fetched_urls.json（自動キャッシュ）
    3. yt-dlpでの自動取得（最終フォールバック）
    の優先順位でURLリストを取得・生成する
    """
    # 1. 手動取得した urls.txt があれば最優先で読み込み（通信 0 回）
    if os.path.exists(URL_TXT_FILE):
        with open(URL_TXT_FILE, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
            unique_lines = list(dict.fromkeys(lines))
            if unique_lines:
                print(f"📄 手動ファイル (urls.txt) から {len(unique_lines)} 件のURLを読み込みました！（通信なし）")
                return unique_lines

    # 2. 自動保存されたキャッシュがあれば読み込み（通信 0 回）
    if os.path.exists(URL_CACHE_FILE):
        try:
            with open(URL_CACHE_FILE, "r", encoding="utf-8") as f:
                urls = json.load(f)
                print(f"📦 キャッシュから {len(urls)} 件のURLを読み込みました！（通信なし）")
                return urls
        except Exception:
            pass

    # 3. ファイルが無い場合のみ yt-dlp で取得（最新2件に制限）
    print("🔍 YouTubeから最新データを取得中...")
    ydl_opts = {'extract_flat': True, 'quiet': True, 'skip_download': True, 'playlistend': 2}

    # 👈 追加: URL一覧取得時にも Cookie があれば適用
    if os.path.exists(COOKIE_FILE):
        ydl_opts["cookiefile"] = COOKIE_FILE

    video_urls = []
    with YoutubeDL(ydl_opts) as ydl:
        for source in sources:
            try:
                info = ydl.extract_info(source, download=False)
                if 'entries' in info:
                    video_urls.extend([e['url'] for e in info['entries'] if e and 'url' in e])
                else:
                    video_urls.append(info.get('webpage_url', source))
            except Exception as e:
                print(f"⚠️ URL取得エラー ({source}): {e}")

    unique_urls = list(dict.fromkeys(video_urls))
    if unique_urls:
        with open(URL_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(unique_urls, f, ensure_ascii=False, indent=2)

    print(f"🎉 キャッシュを更新しました！（合計 {len(unique_urls)} 件）")
    print("☕ 通信負荷緩和のため 10 秒待機します...")
    time.sleep(10)

    return unique_urls

def fetch_video_data_with_ytdlp(video_id: str):
    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "no_warnings": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["all"],  # 全言語を取得対象にする
        "impersonate": "chrome",
        "sleep_subtitles": 2,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"]
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8,th;q=0.7",
        }
    }

    if os.path.exists(COOKIE_FILE):
        ydl_opts["cookiefile"] = COOKIE_FILE

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    title = info.get("title", "")
    upload_date = info.get("upload_date", "")
    tags = info.get("tags", []) or []

    subs = info.get("subtitles", {})
    auto_subs = info.get("automatic_captions", {})

    # 字幕キーの探索（th, en を前方一致などで柔軟に探す）
    def find_target_lang(sub_dict):
        for lang_code in sub_dict.keys():
            if lang_code.startswith("th"):  # th, th-TH, th-orig 等
                return sub_dict[lang_code]
        for lang_code in sub_dict.keys():
            if lang_code.startswith("en"):  # en, en-US 等
                return sub_dict[lang_code]
        # いずれも無ければ最初の字幕データを使う（フォールバック）
        if sub_dict:
            return list(sub_dict.values())[0]
        return None

    target_formats = find_target_lang(subs) or find_target_lang(auto_subs)

    if not target_formats:
        raise RuntimeError("字幕データ（subtitles/automatic_captions）が存在しません。")

    # json3優先、なければ vtt / srv1 等を探す
    json3_url = next((f["url"] for f in target_formats if f.get("ext") == "json3"), None)
    
    # json3 が取れなかった場合は、任意のフォーマットURLをそのまま使う等の処理
    if not json3_url:
        json3_url = target_formats[0].get("url")

    if not json3_url:
        raise RuntimeError("有効な字幕URLが見つかりませんでした。")

    # 字幕JSONのダウンロード
    req = urllib.request.Request(json3_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as res:
        sub_data = json.loads(res.read().decode("utf-8"))

    transcript_list = []
    t_idx = 1
    
    # events構造があるか確認（json3形式）
    events = sub_data.get("events", [])
    for event in events:
        segs = event.get("segs")
        if not segs:
            continue

        text = "".join([s.get("utf8", "") for s in segs]).strip()
        text = re.sub(r'>>\s*', '', text).strip()
        if not text or text == "\n":
            continue

        start_ms = event.get("tStartMs", 0)
        duration_ms = event.get("dDurationMs", 0)

        transcript_list.append({
            "id": t_idx,
            "start": round(start_ms / 1000.0, 2),
            "end": round((start_ms + duration_ms) / 1000.0, 2),
            "text": text
        })
        t_idx += 1

    if not transcript_list:
        raise RuntimeError("字幕テキストの抽出結果が空でした。")

    return title, upload_date, tags, transcript_list

def main():
    setup_logger()

    urls = fetch_all_video_urls(TARGET_SOURCES)
    if not urls:
        print("❌ 有効な動画URLが取得できませんでした。")
        sys.exit(0)

    urls = urls
    saved_video_ids = []

    print("\n📥 各動画のメタデータ・字幕の事前取得を開始します...")
    no_transcript_ids = load_no_transcript_ids()
    request_count = 0  # 実際の通信発生回数をカウント

    for idx, url in enumerate(urls, 1):
        video_id = extract_video_id(url)
        if not video_id:
            continue

        # 1. 字幕なしリストに記録済みの場合は即座にスキップ
        if video_id in no_transcript_ids:
            print(f"⏩ [{idx}/{len(urls)}] VIDEO_ID: {video_id} は字幕なしとして記録済みのためスキップします。")
            continue

        # 2. temp または temp_archive 内にファイルが存在する場合はスキップ
        temp_source_file = os.path.join(TEMP_DIR, f"temp_source_{video_id}.json")
        archive_source_file = os.path.join(ARCHIVE_DIR, f"temp_source_{video_id}.json")

        if os.path.exists(temp_source_file) or os.path.exists(archive_source_file):
            location_name = "temp_archive" if os.path.exists(archive_source_file) else "temp"
            print(f"⏩ [{idx}/{len(urls)}] VIDEO_ID: {video_id} の一時ファイル（{location_name}）が存在するためスキップします。")
            saved_video_ids.append(video_id)
            continue

        # 直前に実際の通信を行っている場合の待機処理
        if request_count > 0:
            # 5件ごとに10分（615秒）の予防冷却
            if request_count % 5 == 0:
                print(f"\n☕ [予防冷却] 5件取得したため 10 分間程 (815秒) 休憩します...")
                time.sleep(815)
            else:
                # 通常時の通信間隔（バッチ冷却を入れるため 20〜40秒 で十分です）
                wait_sec = random.uniform(60, 80)
                print(f"☕ 通信間隔調整のため {wait_sec:.1f} 秒待機します...")
                time.sleep(wait_sec)

        print(f"\n==========================================")
        print(f"🎬 取得中 [{idx}/{len(urls)}]: VIDEO_ID = {video_id}")
        print(f"==========================================")

        request_count += 1

        max_rate_limit_retries = 2
        rate_limit_retry_count = 0

        while rate_limit_retry_count < max_rate_limit_retries:
            try:
                original_title, upload_date, raw_tags, transcript_list = fetch_video_data_with_ytdlp(video_id)

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
                break

            except Exception as e:
                if is_rate_limit_error(e):
                    rate_limit_retry_count += 1
                    print(f"\n⚠️ [429 / アクセス制限検知] YouTubeからIP制限を受けた可能性があります。")
                    print(f"🛑1時間 (3720秒) 処理を完全に停止して冷却します... (再試行 {rate_limit_retry_count}/{max_rate_limit_retries})")
                    time.sleep(3720)
                    print("🔄 1時間経過しました。処理を再開します...\n")
                else:
                    err_msg = str(e).splitlines()[0] if str(e) else "字幕なし/取得エラー"
                    print(f"⏩ 💡 スキップ: VIDEO_ID = {video_id} ({err_msg})")
                    save_no_transcript_id(video_id)
                    no_transcript_ids.add(video_id)
                    break

    print(f"\n🎉 計 {len(saved_video_ids)} 件の事前データ蓄積が完了しました！")

    if not saved_video_ids:
        print("❌ 処理可能な動画データが存在しません。")
        sys.exit(0)

    ## 01_generate_raw_chunks を呼び出して翻訳フェーズへ移行
    # import importlib
    # step01 = importlib.import_module("01_generate_raw_chunks")
    # from git_utils import push_to_github, trigger_cloudflare_build_if_needed

    # print("\n🚀 01_generate_raw_chunks.py を実行します...\n")
    # completed_count = step01.main(saved_video_ids)

    ## 処理完了後にGitプッシュ & Cloudflareビルド判定
    # push_to_github()
    # trigger_cloudflare_build_if_needed(completed_count, threshold=3)

if __name__ == "__main__":
    main()