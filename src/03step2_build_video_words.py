import json
import glob
import os
import sys

# ------------------------------------------
# 1. パス設定
# ------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
DATA_DIR = os.path.join(MAIN_DIR, "data", "transcripts")
OUTPUT_WORDS_DIR = os.path.join(MAIN_DIR, "data", "words")
STATUS_FILE = os.path.join(MAIN_DIR, "pipeline_status.json")
VIDEOS_FILE = os.path.join(MAIN_DIR, "data", "videos.json")
MASTER_FILE = os.path.join(MAIN_DIR, "data", "words_master.json")

os.makedirs(OUTPUT_WORDS_DIR, exist_ok=True)

# ------------------------------------------
# 2. 単語マスタのロード
# ------------------------------------------
if not os.path.exists(MASTER_FILE):
    print(f"❌ エラー: 単語マスタ 『{MASTER_FILE}』 が存在しません。ステップ1を先に実行してください。")
    sys.exit(1)

with open(MASTER_FILE, "r", encoding="utf-8") as f:
    words_master = json.load(f)

status_data = {}
if os.path.exists(STATUS_FILE):
    with open(STATUS_FILE, "r", encoding="utf-8") as f:
        try:
            status_data = json.load(f)
        except json.JSONDecodeError:
            pass

target_files = glob.glob(os.path.join(DATA_DIR, "video_*.json"))

# ------------------------------------------
# 3. メイン処理：完全ローカルでの高速ビルド
# ------------------------------------------
for filepath in target_files:
    filename = os.path.basename(filepath)
    video_id = filename.replace("video_", "").replace(".json", "")

    status_info = status_data.get(video_id, {})
    if status_info.get("words_extracted") != "completed":
        continue

    print(f"⚙️ ビデオ別単語JSON生成中: Video ID {video_id}...")

    with open(filepath, "r", encoding="utf-8") as f:
        video_data = json.load(f)

    transcript = video_data.get("transcript", [])
    video_words_list = []

    for line in transcript:
        line_id = line.get("id")
        tokens = line.get("tokens", [])
        
        matched_words = []
        for token in tokens:
            # マスタに存在する場合は詳細情報を付与
            if token in words_master:
                matched_words.append({
                    "text": token,
                    **words_master[token]
                })
            else:
                matched_words.append({
                    "text": token,
                    "pos": "unknown",
                    "components": [],
                    "tone": "",
                    "breakdown_explanation": ""
                })

        video_words_list.append({
            "id": line_id,
            "words": matched_words
        })

    # /data/words/word_{video_id}.json として出力
    output_filepath = os.path.join(OUTPUT_WORDS_DIR, f"word_{video_id}.json")
    with open(output_filepath, "w", encoding="utf-8") as f:
        json.dump({"video_id": video_id, "transcript_words": video_words_list}, f, ensure_ascii=False, indent=2)

    print(f"  └─ 📄 保存完了: {output_filepath}")

    # videos.json に `#単語辞書つき` キーワードを追加
    if os.path.exists(VIDEOS_FILE):
        try:
            with open(VIDEOS_FILE, "r", encoding="utf-8") as vf:
                videos_data = json.load(vf)
            
            updated_v = False
            for v_item in videos_data:
                if v_item.get("id") == video_id:
                    keywords = v_item.get("keywords", [])
                    if "#単語辞書つき" not in keywords:
                        keywords.append("#単語辞書つき")
                        v_item["keywords"] = keywords
                        updated_v = True
                    break

            if updated_v:
                with open(VIDEOS_FILE, "w", encoding="utf-8") as vf:
                    json.dump(videos_data, vf, ensure_ascii=False, indent=2)
                print(f"  └─ 🏷️ 『videos.json』のキーワードを更新しました。")
        except Exception as e:
            print(f"  └─ ⚠️ videos.json 更新エラー: {e}")

    # pipeline_status.json の更新
    if video_id not in status_data:
        status_data[video_id] = {}
    status_data[video_id]["words_built"] = "completed"

    with open(STATUS_FILE, "w", encoding="utf-8") as sf:
        json.dump(status_data, sf, ensure_ascii=False, indent=2)

print("\n🎉 ビデオ別単語JSONのビルドがすべて完了しました！")