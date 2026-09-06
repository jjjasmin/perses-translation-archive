import json
import glob
import os
import re
import sys
import time
from dotenv import load_dotenv
from google import genai
from google.genai.errors import ServerError

# ------------------------------------------
# 実行オプション設定
# ------------------------------------------
TARGET_PRIORITY = 888  # 特定priorityのみ実行する場合は数字を指定（例: 888）

# ------------------------------------------
# 1. APIキー・設定
# ------------------------------------------
load_dotenv()
raw_keys = os.getenv("GEMINI_API_KEYS", "")

if raw_keys:
    API_KEYS = [k.strip() for k in raw_keys.replace(",", "\n").splitlines() if k.strip()]
else:
    print("❌ エラー: APIキーが設定されていません。(.env を確認してください)")
    sys.exit(1)

print(f"🔑 合計 {len(API_KEYS)} 個のAPIキーを読み込みました。")

current_key_index = 0

def get_client(key_index: int):
    api_key = API_KEYS[key_index]
    return genai.Client(api_key=api_key)

client = get_client(current_key_index)
BATCH_SIZE = 20

# ------------------------------------------
# 2. 補助関数
# ------------------------------------------
def parse_and_fix_json(json_str: str):
    cleaned_str = re.sub(r"^```json\s*", "", json_str)
    cleaned_str = re.sub(r"\s*```$", "", cleaned_str)

    try:
        return json.loads(cleaned_str)
    except json.JSONDecodeError:
        last_valid = cleaned_str.rfind("}")
        if last_valid != -1:
            truncated = cleaned_str[: last_valid + 1]
            if not truncated.rstrip().endswith("]"):
                truncated += "\n]"
            try:
                return json.loads(truncated)
            except json.JSONDecodeError:
                pass
        raise ValueError("JSONの復元に失敗しました。")

def call_gemini_api_with_retry(prompt: str):
    global current_key_index, client
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-3.5-flash-lite",
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            return response.text.strip()
        except Exception as e:
            err_msg = str(e)
            is_429 = "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg
            is_503 = "503" in err_msg or isinstance(e, ServerError)

            if is_429 and current_key_index + 1 < len(API_KEYS):
                print("⚠️ クォータ制限を検知。APIキーを切り替えます...")
                current_key_index += 1
                client = get_client(current_key_index)
                try:
                    response = client.models.generate_content(
                        model="gemini-3.5-flash-lite",
                        contents=prompt,
                        config={"response_mime_type": "application/json"},
                    )
                    return response.text.strip()
                except Exception:
                    pass

            if is_503 or is_429:
                print(f"⚠️ リトライ中 ({attempt}/{max_retries}) 5秒待機...")
                time.sleep(5)
            else:
                print(f"❌ 予期せぬAPIエラー: {e}")
                sys.exit(1)

    raise RuntimeError("APIの試行回数が上限に達しました。")

# ------------------------------------------
# 3. パス設定・データ読み込み
# ------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MAIN_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
DATA_DIR = os.path.join(MAIN_DIR, "data", "transcripts")
STATUS_FILE = os.path.join(MAIN_DIR, "pipeline_status.json")
MASTER_FILE = os.path.join(MAIN_DIR, "data", "words_master.json")

# 単語マスタのロード
words_master = {}
if os.path.exists(MASTER_FILE):
    with open(MASTER_FILE, "r", encoding="utf-8") as f:
        try:
            words_master = json.load(f)
            print(f"📖 既存の単語マスタを読み込みました: {len(words_master)} 語")
        except json.JSONDecodeError:
            print("⚠️ 単語マスタの読み込みに失敗したため、空で開始します。")

status_data = {}
if os.path.exists(STATUS_FILE):
    with open(STATUS_FILE, "r", encoding="utf-8") as f:
        try:
            status_data = json.load(f)
        except json.JSONDecodeError:
            pass

def get_priority(filepath):
    v_id = os.path.basename(filepath).replace("video_", "").replace(".json", "")
    return status_data.get(v_id, {}).get("priority", 999)

target_files = sorted(glob.glob(os.path.join(DATA_DIR, "video_*.json")), key=get_priority)

# ------------------------------------------
# 4. メイン処理：単語分解・マスタ統合・video_xx.json更新
# ------------------------------------------
for filepath in target_files:
    filename = os.path.basename(filepath)
    video_id = filename.replace("video_", "").replace(".json", "")
    status_info = status_data.get(video_id, {})

    if TARGET_PRIORITY is not None and status_info.get("priority") != TARGET_PRIORITY:
        continue
    if status_info.get("mode") != "standard":
        continue
    if status_info.get("words_extracted") == "completed":
        print(f"⏩ Video {video_id}: 単語抽出済みのためスキップします。")
        continue

    print(f"\n==========================================")
    print(f"🎬 単語抽出・分かち書き開始: Video ID: {video_id}")
    print(f"==========================================")

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_transcript = data.get("transcript", [])
    if not raw_transcript:
        continue

    # ----------------------------------------------------
    # 🔥 1. クレンジング＆初期化処理
    # id と text が正常に存在する要素のみ抽出し、tokens をリセットする
    # ----------------------------------------------------
    transcript = []
    transcript_map = {}

    for item in raw_transcript:
        if isinstance(item, dict) and item.get("id") is not None and item.get("text"):
            item["tokens"] = []  # 古い・壊れた tokens をクリアして初期化
            transcript.append(item)
            transcript_map[item["id"]] = item

    if not transcript:
        print(f"⚠️ Video {video_id}: クレンジング後の有効データが0件のためスキップします。")
        continue
    
    total_items = len(transcript)
    extracted_in_this_video = 0
    parse_failed = False

    for i in range(0, total_items, BATCH_SIZE):
        batch = transcript[i : i + BATCH_SIZE]
        input_data = [{"id": item["id"], "text": item["text"], "translation": item.get("translation", "")} for item in batch]

        prompt = f"""あなたはタイ語の形態素解析および言語学習用辞書作成の専門家です。
提供されたタイ語テキスト（text）と日本語訳（translation）から、以下の2点を行ってください。

1. 元のテキストを正確な単語単位（Tokens）に分割する。
2. 登場した単語ごとの文脈や解説情報（Words Info）を出力する。

【厳格なルール】
1. テキストに含まれる文字・単語のみを順番にそのまま分解してください。省略や捏造は禁止です。
2. `tokens` 配列には、テキストを出現順にそのまま区切った単語文字列の配列を入れてください。
3. `words_info` 配列には、そのバッチに含まれる各単語の辞書情報を入れてください。

【出力フォーマット】
Markdown枠なしの純粋なJSONオブジェクトを出力してください。
{{
  "lines": [
    {{
      "id": 1,
      "tokens": ["ไป", "ทำบุญ", "ตักบาตร", "ด้วยกัน"]
    }}
  ],
  "words_info": [
    {{
      "text": "ทำบุญ",
      "pos": "verb",
      "components": [
        {{ "text": "ทำ", "meaning": "行う" }},
        {{ "text": "บุญ", "meaning": "徳" }}
      ],
      "tone": "mid",
      "breakdown_explanation": "「ทำ（行う）」＋「บุญ（徳）」で、徳を積むことを意味します。"
    }}
  ]
}}

【入力データ】
{json.dumps(input_data, ensure_ascii=False, indent=2)}
"""

        batch_success = False
        for attempt in range(1, 11):
            try:
                raw_text = call_gemini_api_with_retry(prompt)
                parsed_res = parse_and_fix_json(raw_text)

                if isinstance(parsed_res, dict):
                    # 1. video_xx.json 内の各行に tokens を付与
                    lines = parsed_res.get("lines", [])
                    for line in lines:
                        line_id = line.get("id")
                        if line_id in transcript_map:
                            transcript_map[line_id]["tokens"] = line.get("tokens", [])

                    # 2. 単語マスタ（words_info）の追加
                    words_info = parsed_res.get("words_info", [])
                    for item in words_info:
                        word_text = item.get("text")
                        if word_text and word_text not in words_master:
                            words_master[word_text] = {
                                "pos": item.get("pos", ""),
                                "components": item.get("components", []),
                                "tone": item.get("tone", ""),
                                "breakdown_explanation": item.get("breakdown_explanation", "")
                            }
                            extracted_in_this_video += 1
                    batch_success = True
                    break
            except Exception as e:
                print(f"⚠️ 試行 {attempt}/10 パース失敗: {e}")
            time.sleep(1)

        if not batch_success:
            parse_failed = True
            break

        time.sleep(1)

    if parse_failed:
        print(f"⚠️ Video {video_id}: パースエラーが発生したため保存せずにスキップします。")
        continue

    # ----------------------------------------------------
    # 🔥 2. クレンジングされたデータで video_xx.json を上書き保存
    # ----------------------------------------------------
    data["transcript"] = transcript
    with open(filepath, "w", encoding="utf-8") as vf:
        json.dump(data, vf, ensure_ascii=False, indent=2)

    # 3. 単語マスタを保存
    with open(MASTER_FILE, "w", encoding="utf-8") as f:
        json.dump(words_master, f, ensure_ascii=False, indent=2)

    # 4. ステータス更新
    if video_id not in status_data:
        status_data[video_id] = {}
    status_data[video_id]["words_extracted"] = "completed"

    with open(STATUS_FILE, "w", encoding="utf-8") as sf:
        json.dump(status_data, sf, ensure_ascii=False, indent=2)

    print(f"✅ Video {video_id}: クレンジング＆tokens付与・動画JSON更新完了 ＆ 新規単語 {extracted_in_this_video} 件マスタ追加")

print("\n🎉 ステップ1（単語抽出・tokens付与）が完了しました！")