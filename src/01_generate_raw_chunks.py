import json
import os
import re
import sys
import time
from dotenv import load_dotenv
from google import genai
from google.genai.errors import ServerError
from youtube_transcript_api import YouTubeTranscriptApi
from yt_dlp import YoutubeDL

# 02の結合処理をモジュールとしてインポート
import importlib
build_module = importlib.import_module("02_build_final_json")
build_final_json = build_module.build_final_json


# ==========================================
# 0. トークン管理クラス
# ==========================================
class TokenTracker:
    def __init__(self):
        self.total_input = 0
        self.total_output = 0

    def log(self, response, prefix=""):
        usage = getattr(response, "usage_metadata", None)
        if usage:
            in_tok = getattr(usage, "prompt_token_count", 0) or 0
            out_tok = getattr(usage, "candidates_token_count", 0) or 0
            total_tok = getattr(usage, "total_token_count", in_tok + out_tok)

            self.total_input += in_tok
            self.total_output += out_tok

            tag = f" [{prefix}]" if prefix else ""
            print(f"📊 [トークン消費{tag}] Input: {in_tok:,} | Output: {out_tok:,} | Sum: {total_tok:,}")
            return in_tok, out_tok
        return 0, 0

    def print_summary(self):
        grand_total = self.total_input + self.total_output
        print("\n" + "=" * 45)
        print("📈 【今回の処理におけるトークン消費合計】")
        print(f"  ・Input トークン  : {self.total_input:,}")
        print(f"  ・Output トークン : {self.total_output:,}")
        print(f"  ・総合計トークン  : {grand_total:,}")
        print("=" * 45)

tracker = TokenTracker()


# ==========================================
# 1. 設定項目
# ==========================================
# GitHub Secrets / 環境変数からキー群を取得。github secretsに「GEMINI_API_KEYS」の名前で改行区切りで複数設定すること。
raw_keys = os.getenv("GEMINI_API_KEYS", "")

if raw_keys:
    # 改行やカンマで分割し、空行や余計な空白を除去してリスト化
    API_KEYS = [k.strip() for k in raw_keys.replace(",", "\n").splitlines() if k.strip()]
else:
    print("❌ エラー: APIキーが設定されていません。(.env または GitHub Secrets を確認してください)")
    sys.exit(1)

print(f"🔑 合計 {len(API_KEYS)} 個のAPIキーを読み込みました。")

current_key_index = 0

def get_client(key_index: int):
    api_key = API_KEYS[key_index]
    print(f"🔑 APIキーを使用中 (Index: {key_index})")
    return genai.Client(api_key=api_key)

client = get_client(current_key_index)

TARGET_URLS = [
    "https://www.youtube.com/watch?v=NvrHLb-4lkk",
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
status_file = os.path.abspath(os.path.join(BASE_DIR, "..", "pipeline_status.json"))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "data"))
os.makedirs(DATA_DIR, exist_ok=True)

CHUNK_SIZE = 60
CONTEXT_SIZE = 3


# ==========================================
# 2. 補助関数（解析・パース・リトライ・検証）
# ==========================================
def load_pipeline_status():
    if os.path.exists(status_file):
        try:
            with open(status_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_pipeline_status(status_data):
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(status_data, f, ensure_ascii=False, indent=2)

def extract_video_id(url: str) -> str:
    match = re.search(r"(?:v=|\/([0-9A-Za-z_-]{11})(?:[?&]|$)|youtu\.be\/)([0-9A-Za-z_-]{11})", url)
    return match.group(1) or match.group(2) if match else ""

def clean_raw_chunk_text(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()

def parse_chunk_response(raw_text: str):
    cleaned = clean_raw_chunk_text(raw_text)

    def try_decode(val):
        if not isinstance(val, str):
            return val
        v_clean = val.strip()
        try:
            res = json.loads(v_clean)
            if isinstance(res, str):
                return try_decode(res)
            return res
        except json.JSONDecodeError:
            f_brace, l_brace = v_clean.find("{"), v_clean.rfind("}")
            f_bracket, l_bracket = v_clean.find("["), v_clean.rfind("]")
            candidate = ""
            if f_brace != -1 and l_brace != -1 and l_brace > f_brace:
                candidate = v_clean[f_brace : l_brace + 1]
            elif f_bracket != -1 and l_bracket != -1 and l_bracket > f_bracket:
                candidate = v_clean[f_bracket : l_bracket + 1]

            if candidate and candidate != v_clean:
                try:
                    res = json.loads(candidate)
                    return try_decode(res) if isinstance(res, str) else res
                except json.JSONDecodeError:
                    pass
        return None

    result = try_decode(cleaned)
    if isinstance(result, dict) and "items" in result and isinstance(result["items"], str):
        result["items"] = try_decode(result["items"])
    return result

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
        if not isinstance(row, list) or len(row) < 3:
            return False
        item_id, kana, trans = row[0], row[1], row[2]
        if not isinstance(item_id, int) or not str(kana).strip() or not str(trans).strip():
            return False
        found_ids.add(item_id)

    return expected_ids.issubset(found_ids)

def call_gemini_api_with_retry(prompt: str, chunk_info: str = "", model_name: str = "gemini-3.5-flash"):
    global current_key_index, client
    max_retries = 5
    base_backoff = 5

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={"response_mime_type": "application/json", "max_output_tokens": 8192},
            )
            tracker.log(response, prefix=chunk_info)
            return response.text.strip()
        except Exception as e:
            err_msg = str(e)
            is_429 = "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg
            is_503 = "503" in err_msg or isinstance(e, ServerError)

            if is_429:
                print(f"⚠️ [429 クォータ制限] (試行 {attempt}/{max_retries})")
                if current_key_index + 1 < len(API_KEYS):
                    current_key_index += 1
                    print(f"🔄 APIキーをIndex {current_key_index} に切り替えます...")
                    client = get_client(current_key_index)
                    time.sleep(base_backoff)
                    continue
                else:
                    wait_time = base_backoff * (2 ** (attempt - 1))
                    time.sleep(wait_time)
                    continue

            if is_503:
                time.sleep(base_backoff * (2 ** (attempt - 1)))
            else:
                print(f"❌ 予期せぬAPIエラー: {e}")
                sys.exit(1)

    raise RuntimeError("Gemini APIのリトライ上限に達しました。")

def repair_json_with_light_model(broken_raw_text: str, chunk_info: str = "") -> str:
    repair_prompt = f"""以下のテキストは不完全なJSONです。文法的に正しい完全なJSONのみを出力してください。
【制約事項】思考プロセス、解説、```json などの枠組みは一切出力しないこと。内容は変更せず、括弧の閉じ忘れやダブルクォーテーションのエスケープ漏れのみを修正すること。

【対象テキスト】
{broken_raw_text}"""
    try:
        return call_gemini_api_with_retry(repair_prompt, chunk_info=f"{chunk_info}-Repair", model_name="gemini-2.5-flash")
    except Exception as e:
        print(f"    ⚠️ リペアAPI実行エラー: {e}")
        return ""

def get_video_metadata(video_id: str):
    url = f"[https://www.youtube.com/watch?v=](https://www.youtube.com/watch?v=){video_id}"
    ydl_opts = {"quiet": True, "skip_download": True, "no_warnings": True}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info.get("title", ""), info.get("upload_date", ""), info.get("tags", []) or []

def fetch_transcript(video_id: str):
    try:
        api = YouTubeTranscriptApi()
        transcript_list = api.list_transcripts(video_id)
        try:
            return transcript_list.find_manually_created_transcript(["th", "en", "ja"]).fetch()
        except Exception:
            return transcript_list.find_generated_transcript(["th"]).fetch()
    except Exception:
        return YouTubeTranscriptApi.get_transcript(video_id, languages=["th", "en", "ja"])


# ==========================================
# 3. メイン生成ループ
# ==========================================
status_data = load_pipeline_status()
need_fix_videos = []

for url in TARGET_URLS:
    video_id = extract_video_id(url)
    if not video_id:
        continue

    v_status = status_data.get(video_id, {})
    if v_status.get("generate") == "completed":
        print(f"⏩ VIDEO_ID: {video_id} は処理完了済みのためスキップします。")
        continue

    print(f"\n==========================================")
    print(f"🎬 処理開始: VIDEO_ID = {video_id}")
    print(f"==========================================")

    try:
        original_title, upload_date, raw_tags = get_video_metadata(video_id)
        fetched = fetch_transcript(video_id)

        transcript_list = []
        for idx, item in enumerate(fetched, 1):
            transcript_list.append({
                "id": idx,
                "start": round(item["start"], 2),
                "end": round(item["start"] + item["duration"], 2),
                "text": re.sub(r'>>\s*', '', item["text"]).strip()
            })
    except Exception as e:
        print(f"❌ 字幕データ取得失敗: {e}")
        continue

    chunks = [transcript_list[i : i + CHUNK_SIZE] for i in range(0, len(transcript_list), CHUNK_SIZE)]
    total_chunks = len(chunks)
    parsed_chunks_data = []

    temp_chunk_file = os.path.join(DATA_DIR, f"temp_raw_chunks_{video_id}.json")
    start_chunk_idx = v_status.get("last_processed_chunk", 0)

    if start_chunk_idx > 0 and os.path.exists(temp_chunk_file):
        try:
            with open(temp_chunk_file, "r", encoding="utf-8") as f:
                parsed_chunks_data = json.load(f)
            print(f"📂 前回の中断データ（{start_chunk_idx}チャンク完了）を復元しました。")
        except Exception:
            parsed_chunks_data, start_chunk_idx = [], 0

    has_unresolved_chunk = False

    for chunk_idx in range(start_chunk_idx, total_chunks):
        target_batch = chunks[chunk_idx]
        start_id, end_id = target_batch[0]["id"], target_batch[-1]["id"]
        expected_ids = set(item["id"] for item in target_batch)

        minimal_input = [{"id": item["id"], "text": item["text"]} for item in target_batch]
        context_before = [{"id": item["id"], "text": item["text"]} for item in transcript_list if item["id"] < start_id][-CONTEXT_SIZE:]
        context_after = [{"id": item["id"], "text": item["text"]} for item in transcript_list if item["id"] > end_id][:CONTEXT_SIZE]

        output_format = f'{{\n  "title": "{original_title} の日本語訳タイトル",\n  "items": [[行ID, "発音カタカナ", "日本語訳"]]\n}}' if chunk_idx == 0 else '{{\n  "items": [[行ID, "発音カタカナ", "日本語訳"]]\n}}'

        prompt = f"""タイのボーイズグループ「PERSES」の字幕翻訳タスクです。指定フォーマットの完全なJSONのみ出力してください。
【直前文脈】\n{json.dumps(context_before, ensure_ascii=False)}\n【直後文脈】\n{json.dumps(context_after, ensure_ascii=False)}
【生成対象】\n{json.dumps(minimal_input, ensure_ascii=False)}
【出力フォーマット】\n{output_format}"""

        parsed_res = None
        is_chunk_success = False
        last_raw_text = ""

        # 再試行パイプライン（最大3回）
        for attempt in range(1, 4):
            chunk_info = f"Chunk {chunk_idx + 1}/{total_chunks} (試行 {attempt}/3)"
            print(f"🔄 処理中: {chunk_info} (ID {start_id}〜{end_id})")

            try:
                raw_text = call_gemini_api_with_retry(prompt, chunk_info=chunk_info)
                last_raw_text = raw_text
            except Exception as e:
                print(f"  ❌ APIエラー: {e}")
                continue

            # 一次パース & 検証
            candidate = parse_chunk_response(raw_text)
            if validate_chunk_data(candidate, expected_ids, is_first_chunk=(chunk_idx == 0)):
                print(f"  ✅ 一次パース ＆ 検証成功！")
                parsed_res, is_chunk_success = candidate, True
                break

            # 軽量LLMリペア
            print(f"  ⚠️ パース失敗。軽量LLMでリペア中...")
            repaired_text = repair_json_with_light_model(raw_text, chunk_info=chunk_info)
            if repaired_text:
                candidate_repaired = parse_chunk_response(repaired_text)
                if validate_chunk_data(candidate_repaired, expected_ids, is_first_chunk=(chunk_idx == 0)):
                    print(f"  🎉 リペア ＆ 検証に成功しました！")
                    parsed_res, is_chunk_success = candidate_repaired, True
                    break

            print(f"  ❌ 試行 {attempt} 失敗。メイン翻訳から再生成します...")

        if is_chunk_success and parsed_res is not None:
            parsed_chunks_data.append(parsed_res)
        else:
            print(f"❌ チャンク {chunk_idx + 1} は3回試行しても正常化できませんでした。生テキストを保持します。")
            parsed_chunks_data.append(last_raw_text)
            has_unresolved_chunk = True

        with open(temp_chunk_file, "w", encoding="utf-8") as f:
            json.dump(parsed_chunks_data, f, ensure_ascii=False, indent=2)

        status_data[video_id] = {
            "title": original_title,
            "generate": "in_progress",
            "last_processed_chunk": chunk_idx + 1,
            "total_chunks": total_chunks,
        }
        save_pipeline_status(status_data)
        time.sleep(1)

    # チャンク生成完了後の自動判定
    if has_unresolved_chunk:
        print(f"\n⚠️ 動画 [{video_id}] に手修正が必要なチャンクが含まれています。")
        print(f"👉 修正用ファイル: 『{temp_chunk_file}』")
        print("💡 手動修正後、`python 02_build_final_json.py` を実行してください。02の自動実行はスキップして次の動画へ進みます。")
        need_fix_videos.append(video_id)
    else:
        print(f"\n🎉 全チャンクが正常に揃いました！ 自動で 02 (データ結合処理) を呼び出します...")
        build_final_json(video_id, original_title, upload_date, raw_tags, transcript_list)

tracker.print_summary()

if need_fix_videos:
    print(f"\n⚠️ 以下の動画は手作業の修正が必要です: {need_fix_videos}")
    print("手修正後に `python 02_build_final_json.py` を実行してください。")

print("\n🎉 全URLの API生成処理が完了しました！")