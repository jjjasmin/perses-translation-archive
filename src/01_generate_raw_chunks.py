import json
import os
import re
import sys
import time
import random
from dotenv import load_dotenv
from google import genai
from google.genai.errors import ServerError
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    CouldNotRetrieveTranscript,
)
from yt_dlp import YoutubeDL

# 02の結合処理をモジュールとしてインポート
import importlib
build_module = importlib.import_module("02_build_final_json")
build_final_json = build_module.build_final_json

# ルート階層にある .env を明示的に読み込む
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
dotenv_path = os.path.join(BASE_DIR, "..", ".env")
load_dotenv(dotenv_path)

# その後で API キーを取得
raw_keys = os.getenv("GEMINI_API_KEYS", "")


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
    "https://www.youtube.com/watch?v=Hsk88zgDK8Q",
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
status_file = os.path.abspath(os.path.join(BASE_DIR, "..", "pipeline_status.json"))
DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "data"))
os.makedirs(DATA_DIR, exist_ok=True)

CHUNK_SIZE = 50
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

def check_id_completeness(raw_text: str, expected_ids: set) -> bool:
    # よりシンプル＆確実に「[1,」や「1:」などのIDを抽出する正規表現
    found_ids = set(map(int, re.findall(r'(?:\[|^\s*|"\s*)(\d{1,4})\s*(?:,||:|\]|\s)', raw_text, re.MULTILINE)))
    
    missing = expected_ids - found_ids
    if missing:
        # どのIDが検出できなかったかを画面に出力して原因特定
        print(f"    🔍 [デバッグ] 期待ID: {sorted(list(expected_ids))} | 検出ID: {sorted(list(found_ids))}")
        print(f"    ❌ [デバッグ] 不足しているID: {sorted(list(missing))}")
        
    return expected_ids.issubset(found_ids)

def validate_chunk_data(data, expected_ids: set, is_first_chunk: bool = False) -> bool:
    """JSONの構造とIDの完全性を検証"""
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
        
        # ★ ここを修正！ IDが文字列("1")で返ってきても数値(1)にキャスト（変換）して許容する
        try:
            item_id = int(item_id)
        except (ValueError, TypeError):
            return False

        if not str(kana).strip() or not str(trans).strip():
            return False
            
        found_ids.add(item_id)

    return expected_ids.issubset(found_ids)

def call_gemini_api_with_retry(prompt: str, chunk_info: str = "", model_name: str = "gemini-3.5-flash-lite"):
    global current_key_index, client
    max_retries = 5
    base_backoff = 5

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=prompt,
                # call_gemini_api_with_retry 内の config を更新
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
                                    "items": {"type": "STRING"} # [ID, カタカナ, 日本語]
                                }
                            }
                        },
                        "required": ["items"]
                    },
                    "max_output_tokens": 8192
                }
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
    repair_prompt = f"""以下のテキストは不完全または文法エラーのあるJSONです。厳格に正しいJSONに修正して出力してください。

【厳格ルール】
1. 行や要素（items内の配列データ）を【絶対に削除・省略・追加しない】こと。
2. 各アイテムの [ID, カタカナ, 日本語訳] の3要素構造を必ず維持すること。
3. 日本語訳やカタカナの中に含まれるダブルクォーテーション（"）は、すべて「」や『』などの鍵かっこに置換するか、\" にエスケープしてください。
4. 思考プロセスや解説、```json などの枠組みは一切出力せず、JSONのみを出力してください。

【対象テキスト】
{broken_raw_text}"""
    try:
        return call_gemini_api_with_retry(repair_prompt, chunk_info=f"{chunk_info}-Repair", model_name="gemini-3.5-flash-lite")
    except Exception as e:
        print(f"    ⚠️ リペアAPI実行エラー: {e}")
        return ""

def get_video_metadata(video_id: str):
    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {"quiet": True, "skip_download": True, "no_warnings": True}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info.get("title", ""), info.get("upload_date", ""), info.get("tags", []) or []

def fetch_transcript(video_id: str):
    try:
        ytt_api = YouTubeTranscriptApi()
        if hasattr(ytt_api, "list"):
            transcript_list = ytt_api.list(video_id)
        elif hasattr(ytt_api, "list_transcripts"):
            transcript_list = ytt_api.list_transcripts(video_id)
        else:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
    except AttributeError:
        transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

    # 優先順位 1: タイ語字幕（公式手動）
    try:
        return transcript_list.find_manually_created_transcript(["th"]).fetch()
    except Exception:
        pass

    # 優先順位 2: タイ語字幕（自動生成）
    try:
        return transcript_list.find_generated_transcript(["th"]).fetch()
    except Exception:
        pass

    # 優先順位 3: 英語字幕（公式手動）
    try:
        return transcript_list.find_manually_created_transcript(["en"]).fetch()
    except Exception:
        pass

    raise RuntimeError("対象の字幕（タイ語手動・タイ語自動・英語手動）が見つかりませんでした。")

# ==========================================
# 3. メイン生成ループ
# ==========================================
def main(urls=None):
    # urlsが渡されなければ直書きの TARGET_URLS を使用
    target_list = urls if urls is not None else TARGET_URLS

    status_data = load_pipeline_status()
    need_fix_videos = []
    completed_count = 0
    
    for url in target_list:
        video_id = extract_video_id(url)
        if not video_id:
            continue
    
        v_status = status_data.get(video_id, {})
        if v_status.get("generate") == "completed":
            print(f"⏩ VIDEO_ID: {video_id} は処理完了済みのためスキップします。")
            continue

        # ★★★ 【ここから追加】アクセス制限回避のためのランダム待機 ★★★
        wait_sec = random.uniform(40, 60) # 3〜6秒のランダム待機
        print(f"☕ アクセス制限回避のため {wait_sec:.1f} 秒待機します...")
        time.sleep(wait_sec)
        # ★★★ 【ここまで追加】 ★★★
    
        print(f"\n==========================================")
        print(f"🎬 処理開始: VIDEO_ID = {video_id}")
        print(f"==========================================")
    
        try:
            original_title, upload_date, raw_tags = get_video_metadata(video_id)
            fetched = fetch_transcript(video_id)
    
            transcript_list = []
            for idx, item in enumerate(fetched, 1):
                # 辞書型(dict)かオブジェクト(FetchedTranscriptSnippet)かでアクセスを切り替え
                if isinstance(item, dict):
                    start_val = item.get("start", 0.0)
                    duration_val = item.get("duration", 0.0)
                    text_val = item.get("text", "")
                else:
                    start_val = getattr(item, "start", 0.0)
                    duration_val = getattr(item, "duration", 0.0)
                    text_val = getattr(item, "text", "")

                transcript_list.append({
                    "id": idx,
                    "start": round(start_val, 2),
                    "end": round(start_val + duration_val, 2),
                    "text": re.sub(r'>>\s*', '', str(text_val)).strip()
                })

            # ==========================================
            # ★【追加】YouTubeから取得した生データを別ファイルとして保存
            # ==========================================
            temp_source_file = os.path.join(DATA_DIR, f"temp_source_{video_id}.json")
            with open(temp_source_file, "w", encoding="utf-8") as f:
                json.dump({
                    "video_id": video_id,
                    "original_title": original_title,
                    "upload_date": upload_date,
                    "raw_tags": raw_tags,
                    "transcript": transcript_list
                }, f, ensure_ascii=False, indent=2)

        except (TranscriptsDisabled, NoTranscriptFound, CouldNotRetrieveTranscript) as e:
            print(f"⏩ 💡 スキップ: VIDEO_ID = {video_id} は字幕が無効または存在しません。")
            continue
        except Exception as e:
            # その他の予期せぬエラーは最初の1行だけスッキリ表示
            err_first_line = str(e).splitlines()[0] if str(e) else "不明なエラー"
            print(f"❌ 字幕データ取得失敗 (VIDEO_ID = {video_id}): {err_first_line}")
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

            output_format = f'{{\n  "title": "{original_title} 　を自然な日本語に翻訳してタイトルを作成してください。",\n  "items": [[行ID, "発音カタカナ", "日本語訳"]]\n}}' if chunk_idx == 0 else '{{\n  "items": [[行ID, "発音カタカナ", "日本語訳"]]\n}}'

            prompt = f"""あなたはタイのボーイズグループ「PERSES」のコンテンツを日本語に翻訳・解析する専門家です。

指定されたJSONフォーマットに従って完全なJSONデータのみを出力してください。

【出力配列構造の厳格ルール】
配列の各要素は必ず [行ID, "発音カタカナ", "日本語訳"] の3要素で構成してください。

1. 行ID: 入力データの "id" の数値をそのまま保持（例: 1）
2. 発音カタカナ: タイ語原文の発音を【100%日本語のカタカナのみ】で表記してください。
   ・タイ文字（ก〜ฮ、スラー等）やアルファベット、タイ語原文を混ぜることは【絶対厳禁】です。
   ・単語や音節の区切りには「・」（中黒）を入れてください。
   ・原文のタイ語をそのまま残すのではなく、必ず全ての音をカタカナに変換してください。
3. 日本語訳: 自然で親しみやすい日本語会話体に翻訳してください。

【文字・記号の厳格禁止ルール】
・【絶対厳禁】発音カタカナの中にタイ文字（例: แลว, เพื่อน 等）や英語を残すこと。
・【絶対厳禁】発音カタカナの中にタイ語原文（例: พุ่งขึ้นไป...）とカタカナを併記すること。
・【絶対厳禁】出力内の全テキストで半角ダブルクォーテーション " を使用すること（引用やカッコは「」や『』を使用）。

【正しく出力するための正解例（Few-Shot）】
以下のような形式・クオリティで出力してください。

・入力タイ語: "พุ่งขึ้นไปให้ดินไปเหอะ"
  正解出力: [302, "プン・クン・パイ・ハイ・ディン・パイ・ホック", "思いっきり盛り上げていこう"]
  ❌不可例1（原文混入）: [302, "พุ่งขึ้นไปให้ดินไปเหอะ・プン・クン・パイ...", "..."]
  ❌不可例2（タイ文字残存）: [302, "プン・クン・パイ・ให้・ディン...", "..."]

・入力タイ語: "ไม่มีอีกแล้วนะคนแบบฉัน"
  正解出力: [3, "マイ・ミー・イーク・レオ・ナ・コン・ベーップ・チャン", "もう僕みたいな人なんて他にいないよ"]
  ❌不可例（タイ文字残存）: [3, "マイ・ミー・イーク・แลว・ナ...", "..."]

【グループおよび基本情報】
・グループ名: PERSES（読み方はパーセス） ／ ファンダム名: PIECES（読み方はピーセス）／所属会社: GNEST（読み方はジーネスト）
■ メンバー識別・呼称:
1. 🦥 ジャン（JUNG）
   ・愛称/呼称: ジャン、ピジャン、ウィコーン
2. 🐒 ネー（NAY）
   ・愛称/呼称: ネー、ピネー、ナラン、ナランヴィク
3. 🦈 クリッティン（KRITTIN）
   ・愛称/呼称: クリッティン、クリット
4. 🐶 パーム（PALM）
   ・愛称/呼称: パーム、ノンパーム、トンパーム、ピラウィッチ
5. 🐱 プラッギー（PLUGGY）
   ・愛称/呼称: プラッギー、ギー、ノンギー、ギーギー、タラコーン

【メンバーの呼称・愛称の表記ルール】
・日本語訳内でのメンバー呼称は、定義された呼称リストの表記を厳格に守ってください。
・「พี่จั๋ง（ピー・ジャン）」は「ピ・ジャン」のように中黒（・）やスペースを入れず、必ず「ピジャン」と表記してください。
・「บักคี้（バック・キー）」や「น้องกี้（ノン・ギー）」などの呼び方は、中黒を入れず「プラッギー」や「ノンギー」等、指定の表記に統一してください。

【日本語訳のトーン・表現ルール】
・一人称は全メンバー共通で原則「僕」または「僕たち」に統一してください。ただし、自分の名前を一人称にしている愛嬌表現（例：「ギーはね〜」）は、ニュアンスを殺さずそのまま「〇〇は〜」と訳してください。
・個別の過度な役割語（キャラクター口調）は適用せず、タイ語原文のニュアンス（敬語・タメ口、感情の高ぶり）に忠実で、自然な日本語会話体に翻訳してください。
・文末に「クラップ/カー（ครับ/ค่ะ）」がついている発言や丁寧な単語が使われている場合は、日本語でも丁寧語・敬語（〜です/〜ます/〜ですね）を維持してください。
・日本語訳では、タイ語文字（เอ้ย, เฮ้ย, อุ้ย, อู้ว など）を出力することを【絶対厳禁】とします。
・リアクションや感嘆詞は【カタカナ音声】に変換してください。
・笑いの表現は「555」を使用してください。
・基本はタメ口で話している途中に急に文末詞をつけて「敬語」に戻った場合（照れ、皮肉、改まった雰囲気など）は、その落差（ギャップ）が日本語テロップでも伝わるように表現してください。

【タイ語のカタカナ表記推奨リスト（タイ沼・ファン向け）】
視聴者がタイカルチャーに親しみがあることを前提に、以下の定番単語・挨拶・リアクションは日本語に直訳（「こんにちは」「かわいい」等）せず、指定のカタカナ表記にカッコ書きでキャラクターに合わせた翻訳文を添えて出力してください。
発音カタカナを出力する際は、単語や意味の区切り、語尾とのつなぎ目に「・」（中黒）を入れて読みやすく区切ってください。（例: 「サワッディー・クラップ」「ナーラック・ジャン」）
※文末詞（ナ、ジャン、ルーイ、ア等）が伴う場合は、語尾まであわせてカタカナ化し、ニュアンスをカッコ内に反映してください。
※「サワッディー」「コップン」などの超定番挨拶は、認知度が高いためカッコ書きの補足は不要です。

■ 挨拶・感謝・返事:
・สวัสดี（サワッディー / サワディー・クラップ）※英語の「ハロー！」等に訳すのは禁止。補足訳は不要。
・ขอบคุณ（コップン / コップン・クラップ / コップン・ナ）※感謝表現。補足訳は不要。
・ใช่（チャイ）※語尾も含めてカタカナ化（例: 「チャイ・ナ（そうだよ〜）」「チャイ・シ！（もちろん！）」）
・ไม่ใช่（マイチャイ）※語尾も含めてカタカナ化（例: 「マイチャイ・ナ（ちがうよ〜）」「マイチャイ・クラップ（違います）」）
・ครับ（クラップ）※返事・丁寧な文末表現（例: 「クラップ（はい）」）

■ 呼称・感情・形容詞:
・พี่（ピー）※敬称（例: ピジャン、ピネー）※補足訳は不要。
・น้อง（ノン）※敬称（例: ノンパーム、ノンギー）※補足訳は不要。
・น่ารัก（ナーラック）※語尾も含めてカタカナ化（例: 「ナーラック・ジャン〜（すごくかわいいね）」「ナーラック・ルーイ！（めちゃくちゃかわいい！）」）

【外来語・英語由来の言葉の翻訳ルール】
・日本語でも同じ感覚・文脈で通じる外来語（例: Amazing → アメイジング、 touch → タッチ）は、テンションに合わせてカタカナ表記で出力してください。
・日本人に通じない・誤解を与える外来語（例: 英語由来だがタイ語で「おしゃれ・粋」の意味で使われる gay / เก๋ など）はカタカナ化せず、意味の通じる日本語（例: 「おしゃれ〜」など）に翻訳してください。

【タイ語特有の文化・表現に対する「注釈」の挿入】
・日本人にとって馴染みのない文化、スラング、商品名、人名、タイ語特有の言葉遊び（ダジャレ等）が出た場合は、カッコ等で直後に簡潔な注釈を入れてください。
  例：「〜なんだよ（※タイの人気SNSで話題のフレーズ）」
  例：「ソムタム（※パパイヤの辛いサラダ）食べたい！」

---

【直前文脈】\n{json.dumps(context_before, ensure_ascii=False)}\n【直後文脈】\n{json.dumps(context_after, ensure_ascii=False)}
【生成対象】\n{json.dumps(minimal_input, ensure_ascii=False)}
【出力フォーマット】\n{output_format}"""
    
            parsed_res = None
            is_chunk_success = False
            failed_raw_history = []  # メイン翻訳APIの失敗生データ（最大3回分）を蓄積

            # メイン翻訳API（最大3回試行）
            for main_attempt in range(1, 4):
                chunk_info = f"Chunk {chunk_idx + 1}/{total_chunks} (メイン試行 {main_attempt}/3)"
                print(f"🔄 処理中: {chunk_info} (ID {start_id}〜{end_id})")

                try:
                    raw_text = call_gemini_api_with_retry(prompt, chunk_info=chunk_info)
                    failed_raw_history.append(f"#NEED_FIX [メイン試行 {main_attempt}]\n" + raw_text)
                except Exception as e:
                    print(f"  ❌ APIエラー: {e}")
                    continue

                candidate = parse_chunk_response(raw_text)

                # 1. 完璧に成功した場合
                if validate_chunk_data(candidate, expected_ids, is_first_chunk=(chunk_idx == 0)):
                    print(f"  ✅ 一次パース ＆ 全ID検証成功！")
                    parsed_res, is_chunk_success = candidate, True
                    break

                # 2. IDが不足している（データ欠落）場合 ➔ リペアせず即メイン再走
                # 修正後：生のレスポンス文字列(raw_text)をそのまま渡す！
                has_all_ids = check_id_completeness(raw_text, expected_ids)
                if not has_all_ids:
                    print(f"  ⚠️ IDの欠落（データ抜け）を検知しました。リペアは行わずメイン翻訳APIを再実行します...")
                    continue

                # 3. IDは全揃いだがJSONが壊れている場合 ➔ リペアAPI（最大3回試行）
                print(f"  ⚠️ ID全揃い・JSON構造不全。リペアAPI（最大3回）を実行します...")
                repair_success = False
                for repair_attempt in range(1, 4):
                    r_info = f"{chunk_info}-Repair_{repair_attempt}"
                    repaired_text = repair_json_with_light_model(raw_text, chunk_info=r_info)
                    if repaired_text:
                        candidate_repaired = parse_chunk_response(repaired_text)
                        if validate_chunk_data(candidate_repaired, expected_ids, is_first_chunk=(chunk_idx == 0)):
                            print(f"  🎉 リペア (試行 {repair_attempt}/3) ＆ 検証に成功しました！")
                            parsed_res, is_chunk_success, repair_success = candidate_repaired, True, True
                            break
                    print(f"    ❌ リペア試行 {repair_attempt}/3 失敗")

                if repair_success:
                    break
                else:
                    print(f"  ❌ リペア3回失敗。メイン翻訳APIからやり直します...")

            # 結果の保存判定
            if is_chunk_success and parsed_res is not None:
                parsed_chunks_data.append(parsed_res)
            else:
                print(f"❌ チャンク {chunk_idx + 1} はメイン試行3回・リペア試行を経て正常化できませんでした。#NEED_FIX 生テキスト履歴を保持します。")
                parsed_chunks_data.append({
                    "status": "#NEED_FIX",
                    "chunk": chunk_idx + 1,
                    "raw_history": failed_raw_history
                })
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
            if build_final_json(video_id, original_title, upload_date, raw_tags, transcript_list):
                completed_count += 1  # ★追加: 正常完了時に+1
    
    tracker.print_summary()
    
    if need_fix_videos:
        print(f"\n⚠️ 以下の動画は手作業の修正が必要です: {need_fix_videos}")
        print("手修正後に `python 02_build_final_json.py` を実行してください。")
    
    print("\n🎉 全URLの API生成処理が完了しました！")

    return completed_count

# 01 単体で直接実行された場合のみ呼び出す
if __name__ == "__main__":
    main()