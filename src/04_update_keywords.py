import json
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "data"))
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


def extract_keywords_from_titles(original_title: str, translated_title: str, allowed_tag_map: dict) -> list:
    """タイトルテキストからタグ定義マップに基づいてキーワードを抽出する"""
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
    # 重複を除去して返す
    return list(dict.fromkeys(extracted_tags))


def update_all_video_keywords():
    if not os.path.exists(videos_file):
        print(f"❌ '{videos_file}' が見つかりませんでした。")
        return

    allowed_tag_map = load_allowed_tag_map()
    if not allowed_tag_map:
        print(f"⚠️ '{tags_file}' が空であるか読み込めませんでした。")

    with open(videos_file, "r", encoding="utf-8") as f:
        videos_list = json.load(f)

    updated_count = 0
    for item in videos_list:
        original_title = item.get("original_title", "")
        translated_title = item.get("title", "")

        # #NEED_FIX フラグが既存キーワードにあれば保持する
        existing_keywords = item.get("keywords", [])
        has_need_fix = "#NEED_FIX" in existing_keywords

        # キーワードの再抽出
        new_keywords = extract_keywords_from_titles(
            original_title, translated_title, allowed_tag_map
        )

        if has_need_fix and "#NEED_FIX" not in new_keywords:
            new_keywords.append("#NEED_FIX")

        # キーワード領域を完全に置き換え（再生成）
        item["keywords"] = new_keywords
        updated_count += 1

    with open(videos_file, "w", encoding="utf-8") as f:
        json.dump(videos_list, f, ensure_ascii=False, indent=2)

    print(f"✅ {updated_count} 件の動画のキーワード（keywords）を更新しました。")


if __name__ == "__main__":
    update_all_video_keywords()