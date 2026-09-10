import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "data"))
TEMP_DIR = os.path.join(DATA_DIR, "temp")
TRANSCRIPTS_DIR = os.path.join(DATA_DIR, "transcripts")
status_file = os.path.abspath(os.path.join(BASE_DIR, "..", "pipeline_status.json"))
videos_file = os.path.join(DATA_DIR, "videos.json")

def build_multi_lang_json(
    video_id: str,
    lang_code: str,
    original_title: str = "",
    upload_date: str = "",
    raw_tags: list = None,
    transcript_list: list = None
):
    print(f"⚙️ [多言語ビルド] VIDEO_ID = {video_id} (言語: {lang_code}) のデータ結合を開始...")

    temp_lang_chunk_file = os.path.join(TEMP_DIR, f"temp_raw_chunks_{video_id}_{lang_code}.json")
    target_video_file = os.path.join(TRANSCRIPTS_DIR, f"video_{video_id}.json")

    if not os.path.exists(temp_lang_chunk_file):
        print(f"❌ 一時ファイル {temp_lang_chunk_file} が存在しません。")
        return False

    with open(temp_lang_chunk_file, "r", encoding="utf-8") as f:
        parsed_chunks_data = json.load(f)

    # 1. 翻訳結果マッピングの作成
    translated_title = original_title
    res_map = {}

    for idx, c_data in enumerate(parsed_chunks_data):
        if not isinstance(c_data, dict):
            continue
        if idx == 0 and c_data.get("title"):
            translated_title = c_data.get("title")
        
        items = c_data.get("items", {})
        if isinstance(items, dict):
            for k, v in items.items():
                res_map[str(k)] = str(v).strip()

    # 2. 既存の video_XX.json の読み込み（存在しなければ新規生成）
    if os.path.exists(target_video_file):
        with open(target_video_file, "r", encoding="utf-8") as f:
            video_data = json.load(f)
    else:
        video_data = {
            "video_id": video_id,
            "title": {},
            "thumbnail_url": f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
            "members": [],
            "transcript": []
        }

    # 3. タイトルの多言語オブジェクト更新
    if "title" not in video_data or not isinstance(video_data["title"], dict):
        video_data["title"] = {}
    video_data["title"][lang_code] = translated_title

    # 4. 字幕アイテム（transcript）への言語追加
    transcript_items = video_data.get("transcript", [])
    
    # 既存の transcript が空の場合は transcript_list から骨組みを作成
    if not transcript_items and transcript_list:
        for item in transcript_list:
            transcript_items.append({
                "id": item["id"],
                "start": item.get("start", 0.0),
                "end": item.get("end", 0.0),
                "speaker": item.get("speaker", ""),
                "text": item.get("text", ""),
                "pronunciation_kana": "",
                "pronunciation_roman": "",
                "translations": {}
            })
        video_data["transcript"] = transcript_items

    # 字幕各行に translations[lang_code] を追加・更新
    for t_item in video_data["transcript"]:
        i_id = str(t_item["id"])
        if "translations" not in t_item or not isinstance(t_item["translations"], dict):
            t_item["translations"] = {}
        
        t_item["translations"][lang_code] = res_map.get(i_id, "")

    # video_XX.json へ保存
    with open(target_video_file, "w", encoding="utf-8") as f:
        json.dump(video_data, f, ensure_ascii=False, indent=2)

    # 5. videos.json の更新
    videos_list = []
    if os.path.exists(videos_file):
        try:
            with open(videos_file, "r", encoding="utf-8") as f:
                videos_list = json.load(f)
        except Exception:
            videos_list = []

    is_updated = False
    for v_item in videos_list:
        if v_item.get("id") == video_id:
            if "title" not in v_item or not isinstance(v_item["title"], dict):
                v_item["title"] = {"ja": v_item.get("title", "")} if isinstance(v_item.get("title"), str) else {}
            v_item["title"][lang_code] = translated_title
            is_updated = True
            break

    if not is_updated:
        formatted_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}" if len(upload_date) == 8 else ""
        videos_list.append({
            "id": video_id,
            "title": {lang_code: translated_title},
            "original_title": original_title,
            "published_at": formatted_date,
            "file": f"video_{video_id}.json",
            "subtitle_source": "処理完了",
            "keywords": []
        })

    with open(videos_file, "w", encoding="utf-8") as f:
        json.dump(videos_list, f, ensure_ascii=False, indent=2)

    # 6. pipeline_status.json の更新
    status_data = {}
    if os.path.exists(status_file):
        try:
            with open(status_file, "r", encoding="utf-8") as f:
                status_data = json.load(f)
        except Exception:
            pass

    if video_id in status_data:
        if "title" not in status_data[video_id] or not isinstance(status_data[video_id]["title"], dict):
            status_data[video_id]["title"] = {}
        status_data[video_id]["title"][lang_code] = translated_title

        status_data[video_id].setdefault("status", {}).setdefault(lang_code, {})
        status_data[video_id]["status"][lang_code]["generate"] = "completed"

        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(status_data, f, ensure_ascii=False, indent=2)

    print(f"✅ [{lang_code}] データの統合が完了しました: video_{video_id}.json")
    return True