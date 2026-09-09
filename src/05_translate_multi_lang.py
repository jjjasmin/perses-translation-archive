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
    cleaned = clean_raw_chunk_text(raw_text)
    try:
        return json.loads(cleaned)
    except Exception:
        f_brace, l_brace = cleaned.find("{"), cleaned.rfind("}")
        if f_brace != -1 and l_brace != -1 and l_brace > f_brace:
            try:
                return json.loads(cleaned[f_brace:l_brace + 1])
            except Exception:
                pass
    return None

def validate_chunk_data(data, expected_ids: set, is_first_chunk: bool = False) -> bool:
    if not isinstance(data, dict):
        return False
    if is_first_chunk and not data.get("title"):
        return False

    items = data.get("items")
    if not isinstance(items, list) or not items:
        return False

    found_ids = set()
    for row in items:
        if not isinstance(row, list) or len(row) < 2:
            return False
        try:
            found_ids.add(int(row[0]))
        except (ValueError, TypeError):
            return False

    return expected_ids.issubset(found_ids)

def call_gemini_api_with_retry(prompt: str, chunk_info: str = ""):
    global current_key_index, current_model_index, client
    max_retries = 5

    for attempt in range(1, max_retries + 1):
        target_model = AVAILABLE_MODELS[current_model_index]
        try:
            response = client.models.generate_content(
                model=target_model,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": {
                        "type": "OBJECT",
                        "properties": {
                            "title": {"type": "STRING"},
                            "items": {
                                "type": "ARRAY",
                                "items": {
                                    "type": "ARRAY",
                                    "items": {"type": "STRING"}
                                }
                            }
                        },
                        "required": ["items"]
                    },
                    "max_output_tokens": 8192,
                }
            )
            return response.text.strip()
        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                if current_key_index + 1 < len(API_KEYS):
                    current_key_index += 1
                    client = get_client(current_key_index)
                    time.sleep(3)
                    return call_gemini_api_with_retry(prompt, chunk_info)
                elif current_model_index + 1 < len(AVAILABLE_MODELS):
                    current_model_index += 1
                    current_key_index = 0
                    client = get_client(current_key_index)
                    time.sleep(3)
                    return call_gemini_api_with_retry(prompt, chunk_info)
            time.sleep(5)

    raise RuntimeError("Gemini APIのリトライ上限に達しました。")

def process_lang_batch(batch, target_lang_code, target_lang_name, is_first_chunk=False):
    if not batch:
        return None

    expected_ids = set(item["id"] for item in batch)
    # video_XX.json の id と 原文 text をそのまま取得
    minimal_input = [{"id": item["id"], "text": item["text"]} for item in batch]
    
    prompt = f"""You are a professional translator. Translate the following YouTube transcript text into {target_lang_name}.

Strict Output Rules:
1. Return valid JSON only.
2. Structure: {{"title": "Translated Title", "items": [[ID, "Translated Text"]]}} (title is required only if it's the first chunk).
3. Preserve line IDs accurately.
4. Keep the tone natural, engaging, and conversational appropriate for modern youth and pop culture content.

[Target Language]: {target_lang_name} ({target_lang_code})
[Input Items]:
{json.dumps(minimal_input, ensure_ascii=False)}
"""

    raw_text = call_gemini_api_with_retry(prompt, chunk_info=f"Lang: {target_lang_code}")
    candidate = parse_chunk_response(raw_text)

    if validate_chunk_data(candidate, expected_ids, is_first_chunk=is_first_chunk):
        return candidate
    return None

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
            
            for idx, chunk in enumerate(chunks):
                is_first = (idx == 0)
                res = process_lang_batch(chunk, lang_code, lang_name, is_first_chunk=is_first)
                if res:
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