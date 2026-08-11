import glob
import json
import os
import re
import sys

try:
    from pythainlp.transliterate import romanize
    HAS_PYTHAINLP = True
except ImportError:
    HAS_PYTHAINLP = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
videos_file = os.path.abspath(os.path.join(BASE_DIR, "..", "videos.json"))
status_file = os.path.abspath(os.path.join(BASE_DIR, "..", "pipeline_status.json"))
tags_file = os.path.abspath(os.path.join(BASE_DIR, "..", "allowed_tags.json"))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "data"))


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
        return romanize(text, engine="royin")
    except Exception:
        return ""


def build_final_json(video_id: str, original_title: str = "", upload_date: str = "", raw_tags: list = None, transcript_list: list = None):
    """temp_raw_chunks_{video_id}.json からデータを読み込んで最終JSONおよび videos.json を構築する"""
    print(f"\n⚙️ [ビルド処理] VIDEO_ID = {video_id} のデータ結合を開始します...")

    temp_chunk_file = os.path.join(DATA_DIR, f"temp_raw_chunks_{video_id}.json")
    if not os.path.exists(temp_chunk_file):
        print(f"❌ 一時ファイル 『{temp_chunk_file}』 が存在しません。処理をスキップします。")
        return False

    with open(temp_chunk_file, "r", encoding="utf-8") as f:
        parsed_chunks_data = json.load(f)

    # 単体実行時のバックアップ補填
    if not original_title:
        original_title = f"Video_{video_id}"
    raw_tags = raw_tags or []

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
                res_map[row[0]] = (row[1], row[2])

    final_transcript = []
    missing_data_count = 0

    # transcript_list が引き継がれていない場合は temp 内の最小限IDから構築
    if not transcript_list:
        transcript_list = [{"id": k, "start": 0.0, "end": 0.0, "text": ""} for k in sorted(res_map.keys())]

    for item in transcript_list:
        i_id = item["id"]
        th_text = item.get("text", "")
        roman_val = get_thai_romanization(th_text)
        kana_val, trans_val = res_map.get(i_id, ("", ""))

        if not kana_val or not trans_val:
            missing_data_count += 1

        final_transcript.append({
            "id": i_id,
            "start": item.get("start", 0.0),
            "end": item.get("end", 0.0),
            "speaker": "",
            "text": th_text,
            "pronunciation_kana": kana_val,
            "pronunciation_roman": roman_val,
            "translation": trans_val,
        })

    is_need_fix = has_parse_error or (missing_data_count > 0)

    formatted_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}" if len(upload_date) == 8 else ""

    final_json_data = {
        "video_id": video_id,
        "title": translated_title,
        "published_at": formatted_date,
        "thumbnail_url": f"[https://img.youtube.com/vi/](https://img.youtube.com/vi/){video_id}/maxresdefault.jpg",
        "members": [],
        "transcript": final_transcript,
    }

    filename = f"video_{video_id}.json"
    output_file = os.path.join(DATA_DIR, filename)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(final_json_data, f, ensure_ascii=False, indent=2)

    # キーワード抽出
    extracted_tags = []
    for tag in raw_tags:
        tag_upper = f"#{tag.strip('#').upper()}"
        if tag_upper in ALLOWED_TAG_MAP:
            extracted_tags.append(tag_upper)

    title_lower = original_title.lower()
    for tag, keywords in ALLOWED_TAG_MAP.items():
        for kw in keywords:
            if re.search(r'\b' + re.escape(kw) + r'\b', title_lower):
                extracted_tags.append(tag)
                break

    if is_need_fix:
        extracted_tags.append("#NEED_FIX")

    final_keywords = list(dict.fromkeys(extracted_tags))
    file_relative_path = f"data/{filename}"

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
            item["title"] = original_title
            item["original_title"] = original_title
            item["file"] = file_relative_path
            merged = item.get("keywords", []) + final_keywords
            item["keywords"] = [t for t in dict.fromkeys(merged) if t in ALLOWED_TAG_MAP or t == "#NEED_FIX"]
            is_updated = True
            break

    if not is_updated:
        videos_list.append({
            "id": video_id,
            "title": original_title,
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