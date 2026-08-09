import json
import glob
import os
import re
import sys
import time
from google import genai
from google.genai.errors import ServerError

# ==========================================
# 1. APIキー・設定
# ==========================================
API_KEYS = [
    "AQ.Ab8RN6LMel5f9eDauo2yWZ_nn1-gDBqOVnbS6KU_lZreQ-YZAQ",
    "AQ.Ab8RN6IckkMNj2e0Z97sG324h91XwBYWIfx8p-hYoN6wb1F9-w",
    "AQ.Ab8RN6KIOFkUXB2ZebeQTHy6vZXSqxiO4v-9vEcbF86Q_sql8Q",
    "AQ.Ab8RN6Jr04MDsopctb0CE09haL-J0ymOt_xdV6JbN6azaZ01Ig",
]

current_key_index = 0

def get_client(key_index: int):
    api_key = API_KEYS[key_index]
    print(f"🔑 APIキーを使用中 (Index: {key_index})")
    return genai.Client(api_key=api_key)

client = get_client(current_key_index)

# 1回のリクエストでGeminiに単語分解させる行数
# （単語分解専用プロンプトのため、20〜25行まとめて渡してもハングアップしません）
BATCH_SIZE = 25

# ==========================================
# 2. 補助関数
# ==========================================
def parse_and_fix_json(json_str: str):
    """Geminiの出力からJSON配列を取り出して復元"""
    cleaned_str = re.sub(r"^```json\s*", "", json_str)
    cleaned_str = re.sub(r"\s*```$", "", cleaned_str)

    try:
        return json.loads(cleaned_str)
    except json.JSONDecodeError:
        print("⚠️ 途中で切れた不完全なJSONを検知しました。補正処理を実行します...")
        last_valid_object_index = cleaned_str.rfind("}")
        if last_valid_object_index != -1:
            truncated = cleaned_str[: last_valid_object_index + 1]
            if not truncated.rstrip().endswith("]"):
                truncated += "\n]"
            try:
                return json.loads(truncated)
            except json.JSONDecodeError:
                pass
        raise ValueError("JSONの復元に失敗しました。")

def call_gemini_api_with_retry(prompt: str):
    """Gemini API呼び出し（429キー切り替え & 503リトライ処理）"""
    global current_key_index, client
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model="gemini-3.5-flash",
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            return response.text.strip()
        except Exception as e:
            err_msg = str(e)
            is_429 = "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg
            is_503 = "503" in err_msg or isinstance(e, ServerError)

            if is_429:
                print("⚠️ [429 RESOURCE_EXHAUSTED] クォータ制限を検知。APIキーを切り替えます...")
                if current_key_index + 1 < len(API_KEYS):
                    current_key_index += 1
                    client = get_client(current_key_index)
                    try:
                        response = client.models.generate_content(
                            model="gemini-3.5-flash",
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

# ==========================================
# 3. メイン処理：video_*.json を全件スキャンして words を追加
# ==========================================
target_files = sorted(glob.glob("video_*.json"))

if not target_files:
    print("❌ 対象となる `video_*.json` ファイルが見つかりませんでした。")
    sys.exit(0)

print(f"📁 処理対象ファイル: {len(target_files)} 件 ({target_files})")

for filepath in target_files:
    print(f"\n==========================================")
    print(f"🎬 単語追加処理の開始: {filepath}")
    print(f"==========================================")

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    transcript = data.get("transcript", [])
    if not transcript:
        print("⚠️ transcript が空のためスキップします。")
        continue

    # すでに単語追加が完了しているかチェック（先頭行に `words` があれば完了とみなす）
    if "words" in transcript[0] and transcript[0]["words"]:
        print("⏩ すでに `words` が追加済みのためスキップします。")
        continue

    total_items = len(transcript)
    print(f"📦 全 {total_items} 行の字幕に対して、{BATCH_SIZE} 行ごとに単語分解を付与します...")

    # id をキーにしたマッピング用の辞書を作成
    words_map = {}

    for i in range(0, total_items, BATCH_SIZE):
        batch = transcript[i : i + BATCH_SIZE]
        batch_ids = [item["id"] for item in batch]
        
        # APIに渡す最小限の入力データ（id, text, translation）
        input_data_for_prompt = [
            {
                "id": item["id"],
                "text": item["text"],
                "translation": item["translation"]
            }
            for item in batch
        ]

        print(f"🔄 バッチ処理中: ID {batch_ids[0]} 〜 {batch_ids[-1]} (全 {total_items} 行中)")

        prompt = f"""あなたはタイ語の形態素解析および言語学習用辞書作成の専門家です。

提供されたタイ語テキスト（text）と日本語訳（translation）のペアを分析し、各文に含まれる単語を分解して指定のJSON配列形式のみを出力してください。

【厳格なルール】
1. テキストに含まれる文字・単語のみを順番にそのまま分解してください。
2. 前後の文脈から存在しない主語や単語、省略された文脈を勝手に補完・推測・捏造することは【厳禁】です。
3. 各単語の「meaning」には、文脈による過度な意訳ではなく、その単語が単体で持つ「標準的な辞書的意味（基本義）」を記載してください。
4. 元のタイ語テキストの文字を絶対に省略・変更しないでください。
5. 出力は入力の各要素の `id` と、分解した `words` 配列のみを持つシンプルなJSON配列にしてください。

【出力フォーマット】
Markdownの枠（```json）も含めず、純粋なJSON配列のみを出力してください。
[
  {{
    "id": 1,
    "words": [
      {{
        "text": "タイ語単語",
        "kana": "カタカナ発音",
        "meaning": "辞書的な基本意味"
      }}
    ]
  }}
]

【入力データ】
{json.dumps(input_data_for_prompt, ensure_ascii=False, indent=2)}
"""

        raw_text = call_gemini_api_with_retry(prompt)

        try:
            parsed_words_batch = parse_and_fix_json(raw_text)
            if isinstance(parsed_words_batch, list):
                for res_item in parsed_words_batch:
                    item_id = res_item.get("id")
                    words_list = res_item.get("words", [])
                    if item_id is not None:
                        words_map[item_id] = words_list
            else:
                print("⚠️ 出力が配列形式ではありませんでした。")
        except Exception as e:
            print(f"❌ バッチ（ID {batch_ids[0]}〜{batch_ids[-1]}）の単語分解パースに失敗しました: {e}")
            continue

        time.sleep(1) # APIレートリミット対策

    # 元の transcript データに words をマージ（挿入）
    updated_count = 0
    for item in transcript:
        item_id = item.get("id")
        if item_id in words_map:
            item["words"] = words_map[item_id]
            updated_count += 1
        else:
            item["words"] = []

    # 上書き保存
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✅ 『{filepath}』を上書き更新しました！（`words` 追加成功: {updated_count}/{total_items} 行）")

print("\n🎉 全てのファイルの単語付与処理が完了しました！")