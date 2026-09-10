import json
import os
import re
import sys
import time
import glob
import argparse
from dotenv import load_dotenv
from google import genai
from google.genai import types

import importlib
build_module = importlib.import_module("06_build_multi_lang_json")
build_multi_lang_json = build_module.build_multi_lang_json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
dotenv_path = os.path.join(BASE_DIR, "..", ".env")
load_dotenv(dotenv_path)

raw_keys = os.getenv("GEMINI_API_KEYS", "")

if not raw_keys:
    print("❌ エラー: APIキーが設定されていません。")
    sys.exit(1)

API_KEYS = [k.strip() for k in raw_keys.replace(",", "\n").splitlines() if k.strip()]
current_key_index = 0

def get_client(key_index: int):
    return genai.Client(api_key=API_KEYS[key_index])

client = get_client(current_key_index)

# 翻訳対象言語の定義
TARGET_LANGS = {
    "en": "English",
    "ko": "Korean (한국어)",
    "zh-TW": "Traditional Chinese (繁體中文)",
    "id": "Indonesian (Bahasa Indonesia)",
    "pt": "Portuguese (Português)"
}

parser = argparse.ArgumentParser(description="YouTube字幕の多言語翻訳スクリプト")
parser.add_argument("--lite", "-l", action="store_true", help="Gemini Flash Lite モードで高速生成")
parser.add_argument("--force", "-f", action="store_true", help="強制再生成")
parser.add_argument("--lang", type=str, help="特定言語のみ実行する場合（例: en, ko, zh-TW, id, pt）")
args = parser.parse_args()

AVAILABLE_MODELS = ["gemini-3.5-flash-lite"] if args.lite else ["gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.7-flash"]
CHUNK_SIZE = 50 if args.lite else 30
current_model_index = 0

DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "data"))
TRANSCRIPTS_DIR = os.path.join(DATA_DIR, "transcripts")
TEMP_DIR = os.path.join(DATA_DIR, "temp")
status_file = os.path.abspath(os.path.join(BASE_DIR, "..", "pipeline_status.json"))

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

def load_pipeline_status():
    if os.path.exists(status_file):
        try:
            with open(status_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def clean_raw_chunk_text(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()

def parse_chunk_response(raw_text: str):
    """
    Geminiからのレスポンス文字列からJSONオブジェクトを強力に抽出・パースする
    """
    if not raw_text:
        return None

    cleaned_text = raw_text.strip()

    # 1. マークダウンのコードブロック囲み (```json ... ``` や ``` ... ```) を除去
    cleaned_text = re.sub(r"^```(?:json)?\s*", "", cleaned_text, flags=re.MULTILINE)
    cleaned_text = re.sub(r"\s*```$", "", cleaned_text, flags=re.MULTILINE)
    cleaned_text = cleaned_text.strip()

    # 2. 直接 json.loads を試行
    try:
        return json.loads(cleaned_text)
    except json.JSONDecodeError:
        pass

    # 3. テキスト内の最初に出現する '{' から 最後に出現する '}' を抽出して再試行
    match = re.search(r"\{.*\}", cleaned_text, re.DOTALL)
    if match:
        json_str = match.group(0)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            print(f"   ⚠️ JSONパース失敗 (正規表現抽出後): {e}")
            return None

    print("   ⚠️ JSON構造 ({ ... }) がテキスト内に見つかりませんでした")
    return None

def validate_chunk_data(data, expected_ids: set) -> bool:
    if not isinstance(data, dict):
        print("   ⚠️ Validation Fail: レスポンスが dict ではありません")
        return False

    items = data.get("items")
    if not isinstance(items, dict) or not items:
        print("   ⚠️ Validation Fail: items が dict でないか空です")
        return False

    found_ids = set()
    for k in items.keys():
        try:
            found_ids.add(int(k))
        except (ValueError, TypeError):
            continue

    missing_ids = expected_ids - found_ids
    if missing_ids:
        print(f"   ⚠️ Validation Fail: 一部のIDが不足しています -> 不足ID: {sorted(list(missing_ids))[:5]}...")
        return False

    return True

def call_gemini_api_with_retry(prompt: str, chunk_info: str = ""):
    global current_key_index, current_model_index, client
    max_retries = 5

    for attempt in range(1, max_retries + 1):
        # 15 RPM 制限（1分15回＝4秒に1回）を絶対超えないよう、呼び出し直前に必ずインターバルを置く
        time.sleep(3.5)

        target_model = AVAILABLE_MODELS[current_model_index]
        try:
            response = client.models.generate_content(
                model=target_model,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "max_output_tokens": 8192,
                }
            )
            return response.text.strip()
        except Exception as e:
            print(f"⚠️ APIエラー ({target_model}) [試行 {attempt}/{max_retries}]: {e}")
            err_msg = str(e)

            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                # 1. APIキーの切り替え
                if current_key_index + 1 < len(API_KEYS):
                    current_key_index += 1
                    client = get_client(current_key_index)
                    print(f"🔄 APIキーを切り替えます (Key Index: {current_key_index})")
                    time.sleep(2)
                    continue
                # 2. モデルの切り替え
                elif current_model_index + 1 < len(AVAILABLE_MODELS):
                    current_model_index += 1
                    current_key_index = 0
                    client = get_client(current_key_index)
                    print(f"🔄 モデルを切り替えます ({AVAILABLE_MODELS[current_model_index]})")
                    time.sleep(2)
                    continue

                # 3. 切り替えるキーもモデルもない場合は60秒待機
                print("⏳ 全てのキーで制限に達しました。枠回復のため 60秒 待機します...")
                time.sleep(60)
            else:
                time.sleep(5)

    raise RuntimeError("Gemini APIのリトライ上限に達しました。")

def translate_title(original_title: str, target_lang_name: str) -> str:
    """タイトルの独立翻訳関数"""
    if not original_title:
        return ""
    
    prompt = f"Translate the following YouTube video title into {target_lang_name}. Return ONLY the translated title string.\n\nTitle: {original_title}"
    try:
        raw_text = call_gemini_api_with_retry(prompt, chunk_info="Title")
        # JSONまたはプレーンテキストで返ってきた場合の安全な抽出
        cleaned = clean_raw_chunk_text(raw_text)
        if cleaned.startswith("{") and "title" in cleaned:
            data = json.loads(cleaned)
            return data.get("title", original_title)
        return cleaned.replace('"', '').strip()
    except Exception as e:
        print(f"   ⚠️ タイトル翻訳失敗（原文をフォールバックとして使用）: {e}")
        return original_title

def process_lang_batch(batch, target_lang_code, target_lang_name, max_refill_attempts=2):
    if not batch:
        return None

    expected_ids = set(item["id"] for item in batch)
    id_to_item = {item["id"]: item["text"] for item in batch}
    
    merged_items = {}

    for attempt in range(max_refill_attempts + 1):
        # まだ翻訳できていない ID のみを対象にする
        missing_ids = [i for i in expected_ids if i not in merged_items]
        if not missing_ids:
            break  # 全て揃ったら終了

        if attempt > 0:
            print(f"   🔄 不足している {len(missing_ids)} 件の ID を補填取得します... (試行 {attempt}/{max_refill_attempts})")

        sub_input = [{"id": i, "text": id_to_item[i]} for i in missing_ids]

        prompt = f"""You are a professional subtitle translator. Translate the given YouTube transcript text into {target_lang_name}.

Strict Rules:
1. Return valid JSON only.
2. Structure:
{{
  "items": {{
    "1": "Translated Subtitle Text",
    "2": "Translated Subtitle Text"
  }}
}}
3. CRITICAL: You MUST include EVERY single ID from the input inside "items". DO NOT skip any ID.

[Target Language]: {target_lang_name} ({target_lang_code})
[Input Items]:
{json.dumps(sub_input, ensure_ascii=False)}
"""

        raw_text = call_gemini_api_with_retry(prompt, chunk_info=f"Lang: {target_lang_code}")
        candidate = parse_chunk_response(raw_text)

        if isinstance(candidate, dict) and isinstance(candidate.get("items"), dict):
            for k, v in candidate["items"].items():
                try:
                    k_int = int(k)
                    if k_int in expected_ids:
                        merged_items[k_int] = v
                except (ValueError, TypeError):
                    continue

    # 最終的な網羅率チェック
    missing_ids = expected_ids - set(merged_items.keys())
    if missing_ids:
        print(f"   ⚠️ Validation Fail: 補填後も一部のIDが不足しています -> 不足ID: {sorted(list(missing_ids))[:5]}...")
        return None

    # 既存の期待フォーマットに合わせて返却（キーは文字列）
    return {
        "items": {str(k): v for k, v in merged_items.items()}
    }

def main():
    status_data = load_pipeline_status()
    
    # 既存の video_*.json から取得するように変更
    video_files = glob.glob(os.path.join(TRANSCRIPTS_DIR, "video_*.json"))
    target_langs = {args.lang: TARGET_LANGS[args.lang]} if args.lang and args.lang in TARGET_LANGS else TARGET_LANGS

    for file_path in video_files:
        video_id = os.path.basename(file_path).replace("video_", "").replace(".json", "")
        
        ja_status = status_data.get(video_id, {}).get("status", {}).get("ja", {})
        if ja_status.get("mode") != "standard" or ja_status.get("generate") != "completed":
            print(f"⏭️ スキップ [{video_id}] - 日本語(ja)のstandard完了データが存在しません。")
            continue

        with open(file_path, "r", encoding="utf-8") as f:
            v_data = json.load(f)
            
            # タイトルの初期値取得（jaがあればja、なければ最初に見つかった文字列）
            title_obj = v_data.get("title", {})
            original_title = ""
            if isinstance(title_obj, dict):
                original_title = title_obj.get("ja") or next(iter(title_obj.values()), "")
            elif isinstance(title_obj, str):
                original_title = title_obj

            transcript_list = v_data.get("transcript", [])

        if not transcript_list:
            continue

        for lang_code, lang_name in target_langs.items():
            # ステータスチェック
            v_status = status_data.get(video_id, {}).get("status", {}).get(lang_code, {})
            if v_status.get("generate") == "completed" and not args.force:
                print(f"⏭️ スキップ [{video_id}] - {lang_code} は完了済みです。")
                continue

            print(f"\n🌐 翻訳開始: VIDEO [{video_id}] -> 言語: {lang_name} ({lang_code})")
            
            chunks = [transcript_list[i : i + CHUNK_SIZE] for i in range(0, len(transcript_list), CHUNK_SIZE)]
            parsed_chunks = []
            
            # タイトルを単独で翻訳
            translated_title = translate_title(original_title, lang_name)

            for idx, chunk in enumerate(chunks):
                res = process_lang_batch(chunk, lang_code, lang_name)
                if res:
                    # 1チャンク目のオブジェクトに翻訳済みタイトルをセットして後続処理に渡す
                    if idx == 0:
                        res["title"] = translated_title
                    else:
                        res["title"] = ""
                    parsed_chunks.append(res)
                else:
                    print(f"❌ Chunk {idx+1} ({lang_code}) 翻訳失敗")
                    break
            
            if len(parsed_chunks) == len(chunks):
                # 一時ファイルへ書き出し
                temp_lang_chunk_file = os.path.join(TEMP_DIR, f"temp_raw_chunks_{video_id}_{lang_code}.json")
                with open(temp_lang_chunk_file, "w", encoding="utf-8") as f:
                    json.dump(parsed_chunks, f, ensure_ascii=False, indent=2)

                # 統合実行
                build_multi_lang_json(video_id, lang_code, original_title)

if __name__ == "__main__":
    main()