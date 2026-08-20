import json
import os
import re
import sys
import time
import random
import glob
import argparse
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import ServerError

# 02の結合処理をモジュールとしてインポート
import importlib
build_module = importlib.import_module("02_build_final_json")
build_final_json = build_module.build_final_json

# ルート階層にある .env を明示的に読み込む
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
dotenv_path = os.path.join(BASE_DIR, "..", ".env")
load_dotenv(dotenv_path)

# GitHub Secrets / 環境変数からキー群を取得。github secretsに「GEMINI_API_KEYS」の名前で改行区切りで複数設定すること。
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
# 1-1. 設定項目
# ==========================================
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


# ==========================================
# 1-2. 起動引数（スイッチ）の解析
# ==========================================
parser = argparse.ArgumentParser(
    description="YouTube字幕のタイ語・カタカナ翻訳スクリプト"
)
parser.add_argument(
    "--lite",
    "-l",
    action="store_true",
    help="Gemini Flash Lite モードで高速・大量生成します",
)
parser.add_argument(
    "--force",
    "-f",
    action="store_true",
    help="ランクや完了ステータスを無視して強制再生成します",
)
args = parser.parse_args()

# ランク定義 (数字が大きいほど優先度高)
MODE_RANK = {"none": 0, "lite": 1, "standard": 2}

# 現在のモードとランクの確定
current_mode = "lite" if args.lite else "standard"
current_rank = MODE_RANK[current_mode]

# スイッチによって設定を動的に変更
if args.lite:
    print(
        "⚡ 【Liteモード起動】 gemini-3.5-flash-lite で高速・大量生成を実行します。"
    )
    AVAILABLE_MODELS = ["gemini-3.5-flash-lite"]
    CHUNK_SIZE = 50  # Liteは処理が軽いので1度に50件処理してさらに高速化
else:
    print("🌟 【高精度モード起動】 賢い Gemini 3.7 Flash 等で高品質生成を実行します。")
    AVAILABLE_MODELS = [
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
    ]
    CHUNK_SIZE = 30  # 精度重視で30件ずつ

# リペア用軽量モデルを定数として定義
LIGHT_MODEL_NAME = "gemini-3.5-flash-lite"

current_model_index = 0

DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "data"))
TEMP_DIR = os.path.join(DATA_DIR, "temp")
status_file = os.path.abspath(os.path.join(BASE_DIR, "..", "pipeline_status.json"))
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

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


def call_gemini_api_with_retry(prompt: str, chunk_info: str = "", default_model_idx: int = None, model_name: str = None):
    global current_key_index, current_model_index, client
    
    max_retries = 5
    base_backoff = 5

    for attempt in range(1, max_retries + 1):
        # 1. 直接 model_name が指定されている場合はそれを優先（リペア時など）
        # 2. default_model_idx が指定されている場合はそのインデックスのモデルを使用
        # 3. 指定がなければグローバルの current_model_index を使用
        if model_name:
            target_model = model_name
            model_idx = current_model_index
        elif default_model_idx is not None:
            model_idx = default_model_idx
            target_model = AVAILABLE_MODELS[model_idx]
        else:
            model_idx = current_model_index
            target_model = AVAILABLE_MODELS[model_idx]

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
                    # 👇 ここを追加してセーフティブロックを解除
                    "safety_settings": [
                        types.SafetySetting(
                            category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                            threshold=types.HarmBlockThreshold.BLOCK_NONE,
                        ),
                        types.SafetySetting(
                            category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                            threshold=types.HarmBlockThreshold.BLOCK_NONE,
                        ),
                        types.SafetySetting(
                            category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                            threshold=types.HarmBlockThreshold.BLOCK_NONE,
                        ),
                        types.SafetySetting(
                            category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                            threshold=types.HarmBlockThreshold.BLOCK_NONE,
                        ),
                    ],
                }
            )
            tracker.log(response, prefix=f"{chunk_info} ({target_model})")
            return response.text.strip()

        except Exception as e:
            err_msg = str(e)
            is_429 = "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg
            is_503 = "503" in err_msg or isinstance(e, ServerError)

            if is_429:
                print(f"⚠️ [429 制限検知] モデル: {target_model} | キーIndex: {current_key_index}")
                
                # ① まだ次のAPIキーが残っている場合 ➔ キーを切り替え
                if current_key_index + 1 < len(API_KEYS):
                    current_key_index += 1
                    print(f"🔄 APIキーを Index {current_key_index} に切り替えて再試行します...")
                    client = get_client(current_key_index)
                    time.sleep(5)  # 念のため2秒待機
                    # 💡 【重要】キーを切り替えたら、リトライ上限で落ちないようにこのループをやり直す
                    # forループの外で while や再帰を使うか、一時的にループ上限を回避する
                    return call_gemini_api_with_retry(prompt, chunk_info, default_model_idx, model_name)
                
                # ② 全キー使い切り ＆ まだ次のモデルがある場合 ➔ モデルを変更してキーをIndex 0に戻す
                elif model_idx + 1 < len(AVAILABLE_MODELS):
                    model_idx += 1
                    current_model_index = model_idx
                    current_key_index = 0
                    client = get_client(current_key_index)
                    print(f"🔀 【モデル切り替え】 次のモデル 『{AVAILABLE_MODELS[model_idx]}』 (キーIndex: 0) へ切替えて続行します！")
                    time.sleep(5)
                    return call_gemini_api_with_retry(prompt, chunk_info, default_model_idx, model_name)
                
                # ③ 全モデル ＆ 全キーを使い切った場合 ➔ 60秒待機して再試行（または中断）
                else:
                    print("⏳ 全キー・全モデルが制限に達しました。60秒待機して再試行します...")
                    time.sleep(60)
                    current_key_index = 0
                    client = get_client(current_key_index)
                    return call_gemini_api_with_retry(prompt, chunk_info, default_model_idx, model_name)

            if is_503:
                # 503は指数バックオフで長めに待機 (5秒、10秒、20秒...)
                # sleep_time = base_backoff * (2 ** (attempt - 1))
                # print(f"⚠️ [503 サーバー混雑] {sleep_time}秒待機して再試行します (試行 {attempt}/{max_retries})...")
                # time.sleep(sleep_time)
                print(f"⚠️ [503 サーバー混雑] 5秒待機して再試行します (試行 {attempt}/{max_retries})...")
                time.sleep(8)
            else:
                print(f"❌ 予期せぬAPIエラー: {e}")
                time.sleep(5)

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
        return call_gemini_api_with_retry(repair_prompt, chunk_info=f"{chunk_info}-Repair", model_name=LIGHT_MODEL_NAME)
    except Exception as e:
        print(f"    ⚠️ リペアAPI実行エラー: {e}")
        return ""


def process_batch_with_split(
    batch,
    transcript_list,
    original_title,
    is_first_chunk=False,
    depth=0,
    chunk_label="",
):
    """
    バッチ（ID群）の翻訳を実行。
    ハングアップ（タイムアウト）やJSON崩れ、リペア失敗時は自動でバッチを2分割（ID半減）して再帰実行する。
    """
    if not batch:
        return []

    start_id, end_id = batch[0]["id"], batch[-1]["id"]
    expected_ids = set(item["id"] for item in batch)
    indent = "  " * depth

    if chunk_label:
        display_label = f"{chunk_label} (ID {start_id}〜{end_id})" if depth == 0 else f"{chunk_label} [分割] (ID {start_id}〜{end_id})"
    else:
        display_label = f"ID {start_id}〜{end_id}"

    minimal_input = [{"id": item["id"], "text": item["text"]} for item in batch]
    context_before = [{"id": item["id"], "text": item["text"]} for item in transcript_list if item["id"] < start_id][-CONTEXT_SIZE:]
    context_after = [{"id": item["id"], "text": item["text"]} for item in transcript_list if item["id"] > end_id][:CONTEXT_SIZE]

    output_format = f'{{\n  "title": "{original_title}  を自然な日本語に翻訳してタイトルを作成してください。",\n  "items": [[行ID, "発音カタカナ", "日本語訳"]]\n}}' if is_first_chunk else '{{\n  "items": [[行ID, "発音カタカナ", "日本語訳"]]\n}}'

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

【直前文脈】\n{json.dumps(context_before, ensure_ascii=False)}
【直後文脈】\n{json.dumps(context_after, ensure_ascii=False)}
【生成対象】\n{json.dumps(minimal_input, ensure_ascii=False)}
【出力フォーマット】\n{output_format}"""

    # メイン翻訳試行 (トークン消費を抑えるため、試行1回で失敗した場合は分割処理)
    for main_attempt in range(1, 2):
        chunk_info = f"{display_label} (メイン試行 {main_attempt})"
        print(f"{indent}🔄 処理中: {chunk_info}")

        try:
            raw_text = call_gemini_api_with_retry(prompt, chunk_info=chunk_info)
        except Exception as e:
            # ★ API通信エラー（429/503/リトライ上限等）の場合は分割へ進めず、呼び出し元に None を返してバッチを保持
            print(f"{indent}  ❌ API通信エラー発生のためバッチを保持します: {e}")
            return None

        candidate = parse_chunk_response(raw_text)

        # 1. 一次検証成功
        if validate_chunk_data(candidate, expected_ids, is_first_chunk=is_first_chunk):
            print(f"{indent}  ✅ 一次パース ＆ 全ID検証成功！")
            return [candidate]

        # 2. ID全揃い時のリペア試行
        if check_id_completeness(raw_text, expected_ids):
            print(f"{indent}  ⚠️ ID全揃い・JSON構造不全。リペアAPIを実行します...")
            for repair_attempt in range(1, 4):
                r_info = f"{chunk_info}-Repair_{repair_attempt}"
                repaired_text = repair_json_with_light_model(raw_text, chunk_info=r_info)
                if repaired_text:
                    candidate_repaired = parse_chunk_response(repaired_text)
                    if validate_chunk_data(candidate_repaired, expected_ids, is_first_chunk=is_first_chunk):
                        print(f"{indent}  🎉 リペア成功！")
                        return [candidate_repaired]

    # ★【通信成功後にフォーマット不全となった場合のみここへ到達する】
    if len(batch) > 1:
        mid = len(batch) // 2
        left_batch = batch[:mid]
        right_batch = batch[mid:]
        print(f"\n{indent}🚨 [JSON構造・ID不全検知 ➔ ID半減] ID {start_id}〜{end_id} ({len(batch)}件) で失敗したため、データ量を半減 ({len(left_batch)}件 / {len(right_batch)}件) して再試行します...")

        res_left = process_batch_with_split(left_batch, transcript_list, original_title, is_first_chunk=is_first_chunk, depth=depth + 1, chunk_label=chunk_label)
        if res_left is None:
            return None

        res_right = process_batch_with_split(right_batch, transcript_list, original_title, is_first_chunk=False, depth=depth + 1)
        if res_right is None:
            return None

        return res_left + res_right
    else:
        print(f"{indent}❌ 最小単位 (ID {start_id}) でも取得に失敗しました。")
        return None


# ==========================================
# 3. メイン生成ループ
# ==========================================
def main(video_ids_or_urls=None):
    status_data = load_pipeline_status()
    completed_count = 0

    # ARCHIVE_DIR は関数冒頭で共通定義しておく
    ARCHIVE_DIR = os.path.join(DATA_DIR, "temp_archive")

    # 入力（URLまたはID）から video_id 一覧を抽出。引数がない場合は temp ディレクトリから自動検出
    if video_ids_or_urls:
        target_ids = [extract_video_id(item) for item in video_ids_or_urls]
    else:
        # 参照元のフォルダ情報を保持しながらファイル一覧を取得
        temp_files = [(f, "temp") for f in glob.glob(os.path.join(TEMP_DIR, "temp_source_*.json"))]
        
        if current_mode == "standard" and os.path.exists(ARCHIVE_DIR):
            temp_files.extend([(f, "archive") for f in glob.glob(os.path.join(ARCHIVE_DIR, "temp_source_*.json"))])

        # ★【解決策A】ソート順: 1. priority(昇順) ➔ 2. フォルダ(temp優先: "temp" < "archive") ➔ 3. video_id(昇順)
        temp_files.sort(
            key=lambda item: (
                status_data.get(os.path.basename(item[0]).replace("temp_source_", "").replace(".json", ""), {}).get("priority", 2),
                0 if item[1] == "temp" else 1,
                os.path.basename(item[0]).replace("temp_source_", "").replace(".json", "")
            )
        )

        # 変数名を f[0] に修正して抽出
        target_ids = [os.path.basename(f[0]).replace("temp_source_", "").replace(".json", "") for f in temp_files]

    # （※ここにあった二重ソートの target_ids.sort(...) は削除）
    for video_id in target_ids:
        if not video_id:
            continue
    
        v_status = status_data.get(video_id, {})
        existing_mode = v_status.get("mode", "none")
        existing_rank = MODE_RANK.get(existing_mode, 0)
        is_completed = v_status.get("generate") == "completed"
        
        # 自動判定（--force 指定時はすべてスルーして実行）
        if not args.force:
            # パターンA: 既に上位のモードで処理されている場合（Lite実行時にStandard既存など）
            if existing_rank > current_rank:
                print(
                    f"⏭️ スキップ: VIDEO_ID [{video_id}] は既に高精度版 ({existing_mode}) で作成済みのため保護されました。"
                )
                continue
        
            # パターンB: 同等モードで既に完了している場合
            if existing_rank == current_rank and is_completed:
                print(
                    f"⏭️ スキップ: VIDEO_ID [{video_id}] は既に {current_mode} モードで完了しています。"
                )
                continue
        
        # ★【追加】アップグレードまたは --force 実行時の初期化フラグ
        is_upgrade_or_force = args.force or (existing_rank < current_rank and existing_rank > 0)
        
        if is_upgrade_or_force and existing_rank > 0:
            print(
                f"🔄 アップグレード: VIDEO_ID [{video_id}] を {existing_mode} ➔ {current_mode} (高品質) へ上書き生成します！"
            )

        # 事前取得済みの一時ファイルを読み込む（TEMP_DIR になければ ARCHIVE_DIR から読み込み）
        temp_source_file = os.path.join(TEMP_DIR, f"temp_source_{video_id}.json")
        if not os.path.exists(temp_source_file):
            archive_source_file = os.path.join(ARCHIVE_DIR, f"temp_source_{video_id}.json")
            if current_mode == "standard" and os.path.exists(archive_source_file):
                temp_source_file = archive_source_file
            else:
                print(f"⚠️ 一時ファイル 『{temp_source_file}』 が見つかりません。スキップします。")
                continue

        try:
            with open(temp_source_file, "r", encoding="utf-8") as f:
                src_data = json.load(f)
                original_title = src_data.get("original_title", "")
                upload_date = src_data.get("upload_date", "")
                raw_tags = src_data.get("raw_tags", [])
                transcript_list = src_data.get("transcript", [])

            # ---------------------------------------------------------
            # 💡 【追加】字幕本文のタイ文字判定（タイ語なし・完全英語はスキップ）
            # ---------------------------------------------------------
            full_text = "".join([item.get("text", "") for item in transcript_list])
            has_thai = bool(re.search(r'[\u0E00-\u0E7F]', full_text))

            if not has_thai:
                print(f"⏭️ スキップ: VIDEO_ID [{video_id}] は完全英語（タイ語なし）字幕のため除外します。")
                status_data[video_id] = {
                    "title": original_title,
                    "generate": "skipped_english_only",
                    "priority": v_status.get("priority", 2)
                }
                save_pipeline_status(status_data)
                continue
            # ---------------------------------------------------------

        except Exception as e:
            print(f"❌ 一時ファイル読み込みエラー (VIDEO_ID = {video_id}): {e}")
            continue

        print(f"\n==========================================")
        print(f"🎬 処理開始: VIDEO_ID = {video_id}")
        print(f"==========================================")
    
        chunks = [transcript_list[i : i + CHUNK_SIZE] for i in range(0, len(transcript_list), CHUNK_SIZE)]
        total_chunks = len(chunks)
        parsed_chunks_data = []
    
        temp_chunk_file = os.path.join(TEMP_DIR, f"temp_raw_chunks_{video_id}.json")

        # ★【修正】アップグレード時や --force 時は、過去の再開位置やキャッシュをリセットする
        if is_upgrade_or_force:
            start_chunk_idx = 0
            parsed_chunks_data = []
            if os.path.exists(temp_chunk_file):
                try:
                    os.remove(temp_chunk_file) # 古いLiteキャッシュを削除
                except Exception:
                    pass
        else:
            # 同じモードでの「再開（レジューム）」処理
            start_chunk_idx = v_status.get("last_processed_chunk", 0)
            parsed_chunks_data = []
            if start_chunk_idx > 0 and os.path.exists(temp_chunk_file):
                try:
                    with open(temp_chunk_file, "r", encoding="utf-8") as f:
                        parsed_chunks_data = json.load(f)
                    print(f"📂 前回の中断データ（{start_chunk_idx}チャンク完了）を復元しました。")
                except Exception:
                    parsed_chunks_data, start_chunk_idx = [], 0
    
        has_unresolved_chunk = False
        unresolved_chunks_info = []
    
        for chunk_idx in range(start_chunk_idx, total_chunks):
            target_batch = chunks[chunk_idx]
            is_first = (chunk_idx == 0)

            # 半減処理に対応したバッチ処理を呼び出し
            results = process_batch_with_split(
                batch=target_batch,
                transcript_list=transcript_list,
                original_title=original_title,
                is_first_chunk=is_first,
                chunk_label=f"Chunk {chunk_idx + 1}/{total_chunks}",
            )

            # 成功した場合（分割された場合は配列で複数返ってくるため extend で結合）
            if results is not None:
                parsed_chunks_data.extend(results)

                with open(temp_chunk_file, "w", encoding="utf-8") as f:
                    json.dump(parsed_chunks_data, f, ensure_ascii=False, indent=2)

                status_data[video_id] = {
                    "title": original_title,
                    "generate": "in_progress",
                    "mode": current_mode,
                    "last_processed_chunk": chunk_idx + 1,
                    "total_chunks": total_chunks,
                    # ★ 既に priority が設定されていればそれを維持し、無ければ初期値　2:中（1:高 / 2:中 / 3:低 / 99:未設定）を設定
                    "priority": v_status.get("priority", 2)
                }
                save_pipeline_status(status_data)
                time.sleep(1)
            else:
                # 分割しても解決しなかった場合のみ中断
                print(f"\n⚠️ チャンク {chunk_idx + 1}/{total_chunks} の取得に失敗しました。")
                print(f"🔄 直前までに成功した {chunk_idx} チャンク分を保持して処理を中断します。")
                break
    
        # チャンク生成完了後の自動判定
        # 元データの最終IDを取得（例: 1654）
        last_expected_id = transcript_list[-1]["id"] if transcript_list else 0

        # これまでに取得した全チャンクの中から「最大のID」を探す
        max_fetched_id = 0
        for chunk in parsed_chunks_data:
            items = chunk.get("items", []) if isinstance(chunk, dict) else []
            for item in items:
                if isinstance(item, list) and len(item) > 0:
                    try:
                        item_id = int(item[0])
                        if item_id > max_fetched_id:
                            max_fetched_id = item_id
                    except (ValueError, TypeError):
                        pass

        # 最終IDまでしっかり取得できている場合のみ 02 を実行
        if max_fetched_id > 0 and max_fetched_id == last_expected_id:
            print(
                f"\n🎉 最終ID ({last_expected_id}) まで全ての翻訳が完了しました！ 自動で 02 (データ結合処理) を呼び出します..."
            )
            if build_final_json(
                video_id, original_title, upload_date, raw_tags, transcript_list
            ):
                status_data[video_id]["generate"] = "completed"
                status_data[video_id]["mode"] = current_mode
                save_pipeline_status(status_data)
                completed_count += 1
        else:
            print(
                f"\n⏸️ 動画 [{video_id}] は未完了です（到達ID: {max_fetched_id} / 最終ID: {last_expected_id}）。02の自動結合をスキップします。"
            )
    
    tracker.print_summary()
    
    print("\n🎉 全URLの API生成処理が完了しました！")

    return completed_count

# 01 単体で直接実行された場合のみ呼び出す
if __name__ == "__main__":
    main()