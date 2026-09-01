import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "data"))
MAIN_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
videos_file = os.path.join(DATA_DIR, "videos.json")
tags_file = os.path.join(DATA_DIR, "tags.json")


def load_allowed_tag_map():
    if os.path.exists(tags_file):
        try:
            with open(tags_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def extract_keywords_from_titles(
    original_title: str,
    translated_title: str,
    allowed_tag_map: dict,
    published_at: str = "",
) -> list:
    """タイトルテキストおよび公開年からタグ定義マップに基づいてキーワードを抽出する"""
    extracted_tags = []
    search_target_text = f"{original_title} {translated_title}".lower()

    def extract_matching_tags(tag_data):
        for key, value in tag_data.items():
            if isinstance(value, dict):
                # 子カテゴリがある場合は再帰的に探索
                extract_matching_tags(value)
            elif isinstance(value, list):
                tag_name = key
                keywords = value

                for kw in keywords:
                    if not kw:
                        continue
                    if kw.lower() in search_target_text:
                        extracted_tags.append(tag_name)
                        break

    extract_matching_tags(allowed_tag_map)

    # published_at の年判定処理を追加
    if published_at and len(published_at) >= 4:
        year_str = published_at[:4]
        if year_str.isdigit():
            year = int(year_str)
            if 2020 <= year <= 2030:
                extracted_tags.append(f"#{year}")
            elif year <= 2019:
                extracted_tags.append("#before2019")

    # 重複を除去して返す
    return list(dict.fromkeys(extracted_tags))


def update_all_video_keywords():
    if not os.path.exists(videos_file):
        print(f"❌ '{videos_file}' が見つかりませんでした。")
        return

    allowed_tag_map = load_allowed_tag_map()
    if not allowed_tag_map:
        print(f"⚠️ '{tags_file}' が空であるか読み込めませんでした。")

    # pipeline_status.json のパスを DATA_DIR 配下に変更
    pipeline_status_file = os.path.join(MAIN_DIR, "pipeline_status.json")
    pipeline_status = {}

    if os.path.exists(pipeline_status_file):
        try:
            with open(pipeline_status_file, "r", encoding="utf-8") as f:
                pipeline_status = json.load(f)

        except Exception as e:
            print(f"⚠️ 'pipeline_status.json' の読み込みに失敗しました: {e}")

    with open(videos_file, "r", encoding="utf-8") as f:
        videos_list = json.load(f)

    updated_count = 0
    for item in videos_list:
        video_id = item.get("id") or item.get("video_id")
        original_title = item.get("original_title", "")
        translated_title = item.get("title", "")
        published_at = item.get("published_at", "")

        # #NEED_FIX フラグが既存キーワードにあれば保持する
        existing_keywords = item.get("keywords", [])
        has_need_fix = "#NEED_FIX" in existing_keywords

        # キーワードの再抽出（published_atを追加）
        new_keywords = extract_keywords_from_titles(
            original_title, translated_title, allowed_tag_map, published_at
        )

        # pipeline_status.json に基づく管理用キーワードの付与
        if video_id and video_id in pipeline_status:
            status_info = pipeline_status[video_id]

            # mode の判定 (#仮データ / #完成データ)
            mode = status_info.get("mode")
            if mode == "lite":
                new_keywords.append("#仮データ")
            elif mode == "standard":
                new_keywords.append("#完成データ")

            # add_words の判定 (#単語辞書つき)
            if status_info.get("add_words") == "completed":
                new_keywords.append("#単語辞書つき")

        if has_need_fix and "#NEED_FIX" not in new_keywords:
            new_keywords.append("#NEED_FIX")

        # 重複を除去してキーの並びを維持
        new_keywords = list(dict.fromkeys(new_keywords))

        # キーワード領域を完全に置き換え（再生成）
        item["keywords"] = new_keywords
        updated_count += 1

    with open(videos_file, "w", encoding="utf-8") as f:
        json.dump(videos_list, f, ensure_ascii=False, indent=2)

    print(f"✅ {updated_count} 件の動画のキーワード（keywords）を更新しました。")


if __name__ == "__main__":
    update_all_video_keywords()