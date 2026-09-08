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
TARGET_PRIORITY = 2  # 特定priorityのみ実行する場合は数字を指定（例: 888）

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

import re

def calculate_thai_tone(word: str) -> str:
    """
    タイ語単語の表記ルールから声調(mid, low, falling, high, rising)を自動計算する関数
    前立字(ห/อนำ)・二重子音・二音節前立字・ローハン(รร)・特殊母音・促音/平語尾を完全網羅
    """
    if not word or not isinstance(word, str):
        return "mid"

    # 1. 完全固定の例外テーブル（不規則変化・特殊発音など）
    EXCEPTIONS = {
        "ก็": "falling",
        "คะ": "high",
        "ค่ะ": "falling",
        "เพชร": "high",    # พ + short vowel + -t
        "จริง": "mid",     # จ (中子音) + 平語尾
        "สบาย": "mid",     # サ-バイ (二音節平語尾)
    }
    if word in EXCEPTIONS:
        return EXCEPTIONS[word]

    # 文字種定義
    HIGH_CONS = set("ขฃฉฐถผฝศษสห")
    MID_CONS = set("กจดตฎฏบปอ")
    LOW_CONS = set("คฅฆงชซฌญฑฒณทธนบพฟภมยรลวฬฮ")
    SINGLE_LOW = set("งนมยรลวณญฬ")  # 前立字（ห/อ/高子音）の影響を直接受ける低子音単体

    DEAD_FINALS = set("กขคฆดตถทธศษสบพฟภ")  # 死語尾末子音
    LIVE_FINALS = set("งนมยรลว")           # 平語尾末子音

    TONE_MARKS = {
        '\u0e48': 'mai_ek',   # ่
        '\u0e49': 'mai_tho',  # ้
        '\u0e4a': 'mai_tri',  # ๊
        '\u0e4b': 'mai_chat'  # ๋
    }

    # 黙字（ガラン ์ 付きの文字とその前の文字）を除去
    clean_word = re.sub(r'.\u0e4c', '', word)

    # ------------------------------------------
    # 2. 声調記号がある場合
    # ------------------------------------------
    tone_mark = None
    tone_mark_idx = -1
    for i, char in enumerate(clean_word):
        if char in TONE_MARKS:
            tone_mark = TONE_MARKS[char]
            tone_mark_idx = i

    if tone_mark:
        cons_class = "mid"
        for j in range(tone_mark_idx - 1, -1, -1):
            c = clean_word[j]
            if c in HIGH_CONS or c in MID_CONS or c in LOW_CONS:
                # ห นำ / อ นำ 判定
                if j > 0 and clean_word[j-1] == 'ห' and c in SINGLE_LOW:
                    cons_class = "high"
                elif j > 0 and clean_word[j-1] == 'อ' and clean_word.startswith(("อย่า", "อยู่", "อย่าง", "อยาก")):
                    cons_class = "mid"
                elif c in HIGH_CONS:
                    cons_class = "high"
                elif c in MID_CONS:
                    cons_class = "mid"
                else:
                    cons_class = "low"
                break

        if tone_mark == 'mai_ek':
            return "falling" if cons_class == "low" else "low"
        elif tone_mark == 'mai_tho':
            return "high" if cons_class == "low" else "falling"
        elif tone_mark == 'mai_tri':
            return "high"
        elif tone_mark == 'mai_chat':
            return "rising"

    # ------------------------------------------
    # 3. 声調記号がない場合：頭子音クラス判定
    # ------------------------------------------
    found_cons = [(i, c) for i, c in enumerate(clean_word) if c in HIGH_CONS or c in MID_CONS or c in LOW_CONS]

    cons_class = "mid"
    if found_cons:
        first_idx, first_char = found_cons[0]
        
        if len(found_cons) >= 2:
            second_idx, second_char = found_cons[1]
            
            # 1) ห นำ (例: หรือ, หมา)
            if first_char == 'ห' and second_char in SINGLE_LOW:
                cons_class = "high"
            # 2) อ นำ (4単語限定: อยาก, อย่าง, อยู่, อย่า)
            elif first_char == 'อ' and clean_word.startswith(("อย่า", "อยู่", "อย่าง", "อยาก")):
                cons_class = "mid"
            # 3) 二音節前立字（例: ตลาด, สนาม, สวรรค์）
            #    ※ 第1子音が高/中子音 ＋ 第2子音が SINGLE_LOW (ง, น, ม, ย, ร, ล, ว) の場合のみ引き継ぐ
            elif (first_char in HIGH_CONS or first_char in MID_CONS) and second_char in SINGLE_LOW and (second_idx - first_idx == 1):
                cons_class = "high" if first_char in HIGH_CONS or first_char == 'ห' else "mid"
            else:
                # 4) 二重子音 (กร, ปล, คว) または通常の多音節語
                cons_class = "high" if first_char in HIGH_CONS else ("mid" if first_char in MID_CONS else "low")
        else:
            cons_class = "high" if first_char in HIGH_CONS else ("mid" if first_char in MID_CONS else "low")

    # ------------------------------------------
    # 4. 平語尾（คำเป็น） / 死語尾（คำตาย）の判定
    # ------------------------------------------
    # 特殊平語尾母音 (ำ, ใ, ไ, เ-า) が含まれる場合は平語尾
    if any(c in clean_word for c in "ำใไ") or "เอา" in clean_word:
        is_dead = False
    # รร (ローハン) の判定
    elif "รร" in clean_word:
        rr_idx = clean_word.find("รร")
        # รร の後に子音（末子音）があるか
        after_rr = clean_word[rr_idx+2:] if rr_idx + 2 < len(clean_word) else ""
        if after_rr and after_rr[0] in DEAD_FINALS:
            is_dead = True
        elif after_rr and after_rr[0] in LIVE_FINALS:
            is_dead = False
        else:
            # 末子音なしの รร は母音 [-an] 扱い＝平語尾
            is_dead = False
    else:
        last_char = clean_word[-1] if clean_word else ""
        if last_char in DEAD_FINALS:
            is_dead = True
        elif any(c in clean_word for c in "ะัิึุ็") or clean_word.endswith("ะ"):
            is_dead = True
        else:
            is_dead = False

    # ------------------------------------------
    # 5. 声調計算ルールの適用
    # ------------------------------------------
    if not is_dead:  # คำเป็น (平語尾)
        if cons_class == "high":
            return "rising"
        return "mid"
    else:  # คำตาย (死語尾)
        if cons_class in ("high", "mid"):
            return "low"  # 高/中子音 + 促音 = low (例: สิทธิ์, เจ็ด, แปด, หก, ตลาด)
        else:  # 低子音 + 促音
            # 短母音（รัก, คิด, พบ, พรรค）か 長母音（โคตร）かの判定
            if "รร" in clean_word:
                is_short = True
            else:
                is_short = any(c in "ะัิึุ็" for c in clean_word)
                if not is_short:
                    # 短母音化するセット（เ-ะ, แ-ะ, โ-ะ 等）
                    if clean_word.endswith("ะ"):
                        is_short = True
                    # 長母音記号が含まれているか
                    elif any(v in clean_word for v in ["โ", "เ", "แ", "า", "ี", "ื", "ู"]):
                        is_short = False
                    else:
                        # 母音記号なしで死語尾（例: พบ, นก, รก）は隠れた短母音 [o]/[a]
                        is_short = True

            return "high" if is_short else "falling"

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
2. `tokens` 配列には、スペース（空白文字）や記号を除いた純粋な単語文字列のみを出力してください。
3. `words_info` 配列には、そのバッチに含まれる各単語の辞書情報を入れてください。
4. `breakdown_explanation` は以下の条件に該当する場合のみ日本語で簡潔に記載し、それ以外（単一の基本語や直感的にわかる語）は必ず空文字 "" にしてください。
   - 複数のパーツに分解できる「複合語」（例: 「ทำ（行う）」＋「บุญ（徳）」＝ 徳を積む）
   - 比喩表現・成句・直訳と意味が異なる慣用表現・語源が複雑で分かりづらい語

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
      "text": "ไป",
      "meaning": "行く",
      "pronunciation_kana": "パイ",
      "pos": "verb",
      "components": [],
      "breakdown_explanation": ""
    }},
    {{
      "text": "ทำบุญ",
      "meaning": "徳を積む・参拝する",
      "pronunciation_kana": "タム・ブン",
      "pos": "verb",
      "components": [
        {{ "text": "ทำ", "meaning": "行う" }},
        {{ "text": "บุญ", "meaning": "徳" }}
      ],
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
                            raw_tokens = line.get("tokens", [])
                            # 空白文字や空文字列を除外してクリーンなトークンのみ保持
                            clean_tokens = [t.strip() for t in raw_tokens if isinstance(t, str) and t.strip()]
                            transcript_map[line_id]["tokens"] = clean_tokens

                    # 2. 単語マスタ（words_info）の追加
                    words_info = parsed_res.get("words_info", [])
                    for item in words_info:
                        word_text = item.get("text")
                        if word_text and word_text not in words_master:
                            words_master[word_text] = {
                                "meaning": item.get("meaning", ""),
                                "pronunciation_kana": item.get("pronunciation_kana", ""),
                                "pos": item.get("pos", ""),
                                "components": item.get("components", []),
                                "tone": calculate_thai_tone(word_text),  # 👈 Python関数で自動判定した結果を格納
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