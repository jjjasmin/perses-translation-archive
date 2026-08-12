import glob
import json
import os
import re
import sys
from youtube_transcript_api import YouTubeTranscriptApi
from yt_dlp import YoutubeDL

try:
    from pythainlp.tokenize import word_tokenize  # ★追加
    from pythainlp.transliterate import romanize
    HAS_PYTHAINLP = True
except ImportError:
    HAS_PYTHAINLP = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "data"))
TRANSCRIPTS_DIR = os.path.join(DATA_DIR, "transcripts")
status_file = os.path.abspath(os.path.join(BASE_DIR, "..", "pipeline_status.json"))
videos_file = os.path.join(DATA_DIR, "videos.json")
tags_file = os.path.join(DATA_DIR, "tags.json")

# フォルダが存在しなければ自動作成
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)

def load_allowed_tag_map():
    if os.path.exists(tags_file):
        try:
            with open(tags_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

ALLOWED_TAG_MAP = load_allowed_tag_map()


def get_thai_romanization(text: str) -> str:
    if not HAS_PYTHAINLP or not text.strip():
        return ""
    try:
        # 1. タイ語を単語ごとに分割
        words = word_tokenize(text, engine="newmm")
        
        romanized_words = []
        for w in words:
            if not w.strip():
                continue
            
            # 2. 単語ごとにローマ字変換
            r_word = romanize(w, engine="royin")
            
            # 3. 変換されずに残ったタイ文字（็ などの結合記号含む）を消去
            clean_word = re.sub(r'[\u0E00-\u0E7F]', '', r_word)
            
            if clean_word.strip():
                romanized_words.append(clean_word.strip())
        
        # 4. 単語間を半角スペースで結合
        return " ".join(romanized_words)
    except Exception:
        return ""

# ★【追加】YouTubeからメタデータ（タイトル・投稿日・タグ）を取得する関数
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


def build_final_json(
    video_id: str,
    original_title: str = "",
    upload_date: str = "",
    raw_tags: list = None,
    transcript_list: list = None,
):
    """temp_raw_chunks_{video_id}.json からデータを読み込んで最終JSONおよび videos.json を構築する"""
    print(f"\n⚙️ [ビルド処理] VIDEO_ID = {video_id} のデータ結合を開始します...")

    temp_chunk_file = os.path.join(DATA_DIR, f"temp_raw_chunks_{video_id}.json")
    temp_source_file = os.path.join(DATA_DIR, f"temp_source_{video_id}.json")  # ★【追加】元データ一時ファイル
    output_file = os.path.join(TRANSCRIPTS_DIR, f"video_{video_id}.json")

    if not os.path.exists(temp_chunk_file):
        print(f"❌ 一時ファイル 『{temp_chunk_file}』 が存在しません。処理をスキップします。")
        return False

    with open(temp_chunk_file, "r", encoding="utf-8") as f:
        parsed_chunks_data = json.load(f)

    # ==========================================
    # ★【修正】temp_source_*.json から不足しているデータを優先復元
    # ==========================================
    if os.path.exists(temp_source_file):
        try:
            with open(temp_source_file, "r", encoding="utf-8") as f:
                src_data = json.load(f)
                if not transcript_list:
                    transcript_list = src_data.get("transcript", [])
                if not original_title:
                    original_title = src_data.get("original_title", "")
                if not upload_date:
                    upload_date = src_data.get("upload_date", "")
                if not raw_tags:
                    raw_tags = src_data.get("raw_tags", [])
        except Exception as e:
            print(f"⚠️ raw字幕データの読み込みに失敗しました: {e}")

    # ==========================================
    # ★【修正】temp_source に不足がある場合、YouTubeから直接フォールバック取得
    # ==========================================
    if not transcript_list or not original_title or not upload_date:
        print(f"🌐 欠損データがあるため、YouTubeから直接メタデータ・字幕を取得します...")
        try:
            if not original_title or not upload_date:
                yt_title, yt_date, yt_tags = get_video_metadata(video_id)
                original_title = original_title or yt_title
                upload_date = upload_date or yt_date
                raw_tags = raw_tags or yt_tags

            if not transcript_list:
                fetched = fetch_transcript(video_id)
                transcript_list = []
                for idx, item in enumerate(fetched, 1):
                    start_val = item.get("start", 0.0) if isinstance(item, dict) else getattr(item, "start", 0.0)
                    duration_val = item.get("duration", 0.0) if isinstance(item, dict) else getattr(item, "duration", 0.0)
                    text_val = item.get("text", "") if isinstance(item, dict) else getattr(item, "text", "")
                    
                    transcript_list.append({
                        "id": idx,
                        "start": round(start_val, 2),
                        "end": round(start_val + duration_val, 2),
                        "text": re.sub(r'>>\s*', '', str(text_val)).strip()
                    })
        except Exception as e:
            print(f"⚠️ YouTubeからの再取得に失敗しました: {e}")

    # 万が一YouTube通信エラー等の場合のための最終安全網
    if not original_title:
        original_title = f"Video_{video_id}"
    raw_tags = raw_tags or []
    transcript_list = transcript_list or []

    translated_title = original_title
    res_map = {}
    has_parse_error = False

    for idx, c_data in enumerate(parsed_chunks_data):
        if c_data is None or isinstance(c_data, str):
            has_parse_error = True
            continue

        if idx == 0 and isinstance(c_data, dict):
            translated_title = c_data.get("title") or original_title
            items = c_data.get("items", [])
        elif isinstance(c_data, dict):
            items = c_data.get("items", [])
        elif isinstance(c_data, list):
            items = c_data
        else:
            items = []

        for row in items:
            if isinstance(row, list) and len(row) >= 3:
                # キーを str(row[0]) に明確化
                res_map[str(row[0])] = (str(row[1]).strip(), str(row[2]).strip())

    # transcript_list がそれでも見つからない場合は ID だけから最小構築
    if not transcript_list:
        transcript_list = [
            {"id": int(k) if str(k).isdigit() else k, "start": 0.0, "end": 0.0, "text": ""}
            for k in sorted(res_map.keys(), key=lambda x: int(x) if str(x).isdigit() else x)
        ]

    final_transcript = []
    missing_data_count = 0

    for item in transcript_list:
        i_id = item["id"]
        th_text = item.get("text", "")
        roman_val = get_thai_romanization(th_text)

        # 💡 【重要修正点】 i_id が int でも str でも確実に引けるように変換して検索
        kana_val, trans_val = res_map.get(str(i_id), res_map.get(i_id, ("", "")))

        if not kana_val or not trans_val:
            missing_data_count += 1

        final_transcript.append(
            {
                "id": i_id,
                "start": item.get("start", 0.0),
                "end": item.get("end", 0.0),
                "speaker": item.get("speaker", ""),
                "text": th_text,
                "pronunciation_kana": kana_val,
                "pronunciation_roman": roman_val,
                "translation": trans_val,
            }
        )

    is_need_fix = has_parse_error or (missing_data_count > 0)

    formatted_date = (
        f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
        if len(upload_date) == 8
        else ""
    )

    final_json_data = {
        "video_id": video_id,
        "title": translated_title,
        "published_at": formatted_date,
        "thumbnail_url": f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
        "members": [],
        "transcript": final_transcript,
    }

    filename = f"video_{video_id}.json"

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(final_json_data, f, ensure_ascii=False, indent=2)

    # --- (以下略: キーワード抽出・videos.json・status更新処理はそのまま) ---

    # キーワード抽出
    extracted_tags = []

    title_lower = original_title.lower()
    for tag, keywords in ALLOWED_TAG_MAP.items():
        for kw in keywords:
            if re.search(r'\b' + re.escape(kw) + r'\b', title_lower):
                extracted_tags.append(tag)
                break

    if is_need_fix:
        extracted_tags.append("#NEED_FIX")

    final_keywords = list(dict.fromkeys(extracted_tags))
    file_relative_path = f"{filename}"

    # videos.json 更新
    videos_list = []
    if os.path.exists(videos_file):
        try:
            with open(videos_file, "r", encoding="utf-8") as f:
                videos_list = json.load(f)
        except Exception:
            videos_list = []

    is_updated = False
    for item in videos_list:
        if item.get("id") == video_id:
            item["title"] = translated_title
            item["original_title"] = original_title
            item["file"] = file_relative_path
            merged = item.get("keywords", []) + final_keywords
            item["keywords"] = [t for t in dict.fromkeys(merged) if t in ALLOWED_TAG_MAP or t == "#NEED_FIX"]
            is_updated = True
            break

    if not is_updated:
        videos_list.append({
            "id": video_id,
            "title": translated_title,
            "original_title": original_title,
            "file": file_relative_path,
            "subtitle_source": "処理完了",
            "keywords": final_keywords,
        })

    with open(videos_file, "w", encoding="utf-8") as f:
        json.dump(videos_list, f, ensure_ascii=False, indent=2)

    # pipeline_status 更新
    status_data = {}
    if os.path.exists(status_file):
        try:
            with open(status_file, "r", encoding="utf-8") as f:
                status_data = json.load(f)
        except Exception:
            pass

    if video_id in status_data:
        status_data[video_id]["title"] = translated_title
        status_data[video_id]["generate"] = "completed"
        status_data[video_id]["file"] = file_relative_path
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(status_data, f, ensure_ascii=False, indent=2)

    if is_need_fix:
        print(f"⚠️ 一部エラー・空欄があるため `#NEED_FIX` 付与で 『{output_file}』 を出力しました。")
    else:
        print(f"✅ 『{output_file}』 および 『videos.json』 の保存に成功しました！")
        
    # 一時ファイルの削除処理
    for temp_file in (temp_chunk_file, temp_source_file):
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception as e:
                print(f"⚠️ 一時ファイルの削除に失敗しました ({os.path.basename(temp_file)}): {e}")

    return True


if __name__ == "__main__":
    # 手動単体実行時：dataディレクトリ内のすべての temp_raw_chunks_*.json を検出して一括ビルド
    temp_files = glob.glob(os.path.join(DATA_DIR, "temp_raw_chunks_*.json"))
    if not temp_files:
        print("ℹ️ 処理対象の `temp_raw_chunks_*.json` が見つかりませんでした。")
    else:
        print(f"🛠️ 手動モード: {len(temp_files)} 件の一時ファイルからビルドを実行します...")
        for t_file in temp_files:
            v_id = os.path.basename(t_file).replace("temp_raw_chunks_", "").replace(".json", "")
            build_final_json(v_id)
        print("\n✨ すべての手動ビルド処理が完了しました！")
