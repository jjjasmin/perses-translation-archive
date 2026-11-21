import os
import json
import time
import re
from google import genai
from google.genai import types
from dotenv import load_dotenv

# .envの読み込み
load_dotenv()

# === 設定 ===
VIDEOS_JSON_PATH = '../data/videos.json'
TRANSCRIPTS_DIR = '../data/transcripts'
STATUS_FILE_PATH = '../pipeline_status.json'

MODEL_NAME = "gemini-3.5-flash-lite"
TARGET_LANGS = ["ja", "en", "ko", "zh-TW", "id", "pt"]

# レート制限設定（1分に15回以内 = 1リクエストあたり最低4秒間隔）
API_SLEEP_SECONDS = 4.0

# 長文分割（チャンキング）の閾値（文字数）
CHUNK_CHAR_LIMIT = 12000
HIGHLIGHT_FULL_PASS_LIMIT = 35000

# 既存データがある場合でも強制的に再生成するかどうか
FORCE_REGENERATE_SUMMARY = False
FORCE_REGENERATE_HIGHLIGHTS = False

# APIキーの設定とローテーション管理（改行・リテラル\n・カンマ区切りに対応）
api_keys_str = os.getenv("GEMINI_API_KEYS")
if not api_keys_str:
    raise ValueError("エラー: .envファイルに GEMINI_API_KEYS が設定されていません。")

normalized_keys_str = api_keys_str.replace("\\n", "\n")
API_KEYS = [k.strip(' "\'') for k in re.split(r'[,\r\n]+', normalized_keys_str) if k.strip(' "\'')]

if not API_KEYS:
    raise ValueError("エラー: 有効なAPIキーが1つも読み込めませんでした。")

current_key_index = 0
exhausted_keys = set()
last_api_call_time = 0.0


def wait_for_rate_limit():
    """前回のAPI通信から API_SLEEP_SECONDS（4秒）経過するまで待機する"""
    global last_api_call_time
    if last_api_call_time > 0:
        elapsed = time.time() - last_api_call_time
        if elapsed < API_SLEEP_SECONDS:
            time.sleep(API_SLEEP_SECONDS - elapsed)
    last_api_call_time = time.time()


def get_client():
    global current_key_index
    if len(exhausted_keys) >= len(API_KEYS):
        raise RuntimeError("⛔ すべてのAPIキーのクォータ（制限）に達しました。処理を中断します。")
    while current_key_index in exhausted_keys:
        current_key_index = (current_key_index + 1) % len(API_KEYS)
    return genai.Client(api_key=API_KEYS[current_key_index])


def rotate_key():
    global current_key_index
    print(f"🔄 APIキー #{current_key_index + 1} が制限に達したため切り替えます...")
    exhausted_keys.add(current_key_index)
    if len(exhausted_keys) >= len(API_KEYS):
        raise RuntimeError("⛔ すべてのAPIキーのクォータ（制限）に達しました。処理を中断します。")
    while current_key_index in exhausted_keys:
        current_key_index = (current_key_index + 1) % len(API_KEYS)
    print(f"✅ APIキー #{current_key_index + 1} に切り替えました。")


def load_json(path, default_val=None):
    if not os.path.exists(path):
        return default_val if default_val is not None else {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_and_fix_json(raw_text):
    """不完全なJSONやマークダウン装飾を修復してパースする"""
    if not raw_text:
        return None
    text = raw_text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fixed = re.sub(r',\s*([}\]])', r'\1', text)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        return None


def call_gemini_with_retry(prompt, response_mime_type="application/json"):
    """4秒間隔のレート制限・429ローテーション・503リトライ付きのGemini呼び出し"""
    global last_api_call_time
    max_retries = len(API_KEYS) * 2 + 2
    for attempt in range(max_retries):
        try:
            wait_for_rate_limit()
            client = get_client()
            config = types.GenerateContentConfig(
                temperature=0.35,
                response_mime_type=response_mime_type,
            )
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=config
            )
            last_api_call_time = time.time()
            return response.text
        except Exception as e:
            last_api_call_time = time.time()
            err_msg = str(e)
            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                rotate_key()
            elif "503" in err_msg or "UNAVAILABLE" in err_msg or "500" in err_msg:
                print(f"⚠️ サーバー一時エラー ({attempt + 1}/{max_retries})。5秒待機します...")
                time.sleep(5)
            else:
                print(f"⚠️ APIエラー: {e}")
                time.sleep(API_SLEEP_SECONDS)
    return None


def format_timestamp(seconds):
    """秒数を MM:SS または H:MM:SS 形式に変換"""
    total_sec = max(0, int(round(float(seconds))))
    h = total_sec // 3600
    m = (total_sec % 3600) // 60
    s = total_sec % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def extract_transcript_items(video_id):
    """transcriptファイルからタイムスタンプ・原文・日本語訳を抽出する"""
    t_path = os.path.join(TRANSCRIPTS_DIR, f"video_{video_id}.json")
    if not os.path.exists(t_path):
        return []
    data = load_json(t_path, {})
    transcript = data.get("transcript", [])
    items = []
    for item in transcript:
        trans = item.get("translations", {})
        ja_text = trans.get("ja", "").strip()
        orig_text = item.get("text", "").strip()
        display_text = ja_text if ja_text else orig_text
        if not display_text:
            continue

        start_val = item.get("start", 0)
        try:
            start_sec = float(start_val)
        except (ValueError, TypeError):
            start_sec = 0.0

        has_laugh = bool(re.search(r'5{3,}', orig_text))

        items.append({
            "start": start_sec,
            "orig": orig_text,
            "text": display_text,
            "has_laugh": has_laugh
        })
    return items


def split_into_chunks(lines, max_chars=CHUNK_CHAR_LIMIT):
    """文字数上限を超えないように文字列配列をチャンク分割する"""
    chunks = []
    current_chunk = []
    current_len = 0
    for line in lines:
        line_len = len(line) + 1
        if current_chunk and (current_len + line_len > max_chars):
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_len = line_len
        else:
            current_chunk.append(line)
            current_len += line_len
    if current_chunk:
        chunks.append("\n".join(current_chunk))
    return chunks


def summarize_chunk_ja(original_title, chunk_text, part_idx, total_parts):
    """長文チャンク（パートごと）の暫定要点を日本語で抽出する"""
    prompt = f"""あなたはタイの5人組ボーイズグループ「PERSES」の動画アーカイブの編集者です。
以下は動画（原題: {original_title}）の文字起こしテキストの分割パート（{part_idx}/{total_parts}）です。
このパートで話されている主なトピック・出来事・見どころを、日本語の箇条書き（3〜5項目程度）で簡潔に抽出してください。

【ルール】
- グループ名・メンバー名・ファンダム名などの固有名詞は必ず英語表記で統一すること（例: PERSES, PIECES, JUNG, NAY, KRITTIN, PALM, PLUGGY）。
- 冗長な文末（「〜について話している」等）は省き、体言止めや短いフレーズにすること。

【文字起こしテキスト（パート {part_idx}/{total_parts}）】
{chunk_text}
"""
    return call_gemini_with_retry(prompt, response_mime_type="text/plain")


def generate_multilingual_summary(original_title, content_text, is_merged_notes=False):
    """6言語一括の要点まとめJSONを生成する"""
    source_desc = "各パートから抽出した要点メモ" if is_merged_notes else "動画の文字起こしテキスト"

    prompt = f"""あなたはタイの5人組ボーイズグループ「PERSES」の多言語ファンアーカイブの編集者です。
以下の動画情報をもとに、動画の主なトピック・見どころ（3〜5個程度）を抽出し、指定された6言語すべてで意味が一致する要点まとめを生成してください。

【動画の原題】
{original_title}

【{source_desc}】
{content_text}

【厳守ルール】
1. 出力形式は必ず以下の6言語キー（"ja", "en", "ko", "zh-TW", "id", "pt"）を持つ純粋なJSONオブジェクトのみにすること。
2. すべての言語において、グループ名・メンバー名・ファンダム名などの固有名詞は翻訳・音写せず、必ず英語表記のまま統一すること（例: PERSES, PIECES, JUNG, NAY, KRITTIN, PALM, PLUGGY）。
3. 各トピックは簡潔な短いフレーズ（日本語なら体言止め）にし、「〜について話しています」のような冗長な説明は省くこと。
4. 各トピックの区切り文字には、必ず全角スペースを含む「 ／ 」を使用し、1行の文字列として結合すること（箇条書き記号や改行は入れないこと）。
5. 元のテキストが長い場合でも、重複を省いて動画全体のハイライトが網羅されるように3〜5トピックにまとめること。

【出力JSONフォーマット例】
{{
  "ja": "GQ賞受賞への感謝 ／ 新曲での新たな挑戦とプレッシャー ／ 過去最大規模のフルバンド台北公演の予告 ／ NAYの誕生日エピソード",
  "en": "Gratitude for the GQ Award ／ New challenges and pressure with the upcoming song ／ Announcement of their largest full-band Taipei show ／ NAY's birthday episode",
  "ko": "GQ 수상에 대한 감사 ／ 신곡에서의 새로운 도전과 부담감 ／ 역대 최대 규모의 풀밴드 타이베이 공연 예고 ／ NAY의 생일 에피소드",
  "zh-TW": "感謝獲得GQ獎項 ／ 新歌的全新挑戰與壓力 ／ 預告史上最大規模的全樂團台北演出 ／ NAY的生日小插曲",
  "id": "Rasa syukur atas penghargaan GQ ／ Tantangan baru dan tekanan untuk lagu terbaru ／ Pengumuman konser full-band terbesar di Taipei ／ Cerita ulang tahun NAY",
  "pt": "Gratidão pelo prêmio GQ ／ Novos desafios e pressão com a nova música ／ Anúncio do maior show com banda completa em Taipei ／ Episódio de aniversário de NAY"
}}
"""
    raw_res = call_gemini_with_retry(prompt, response_mime_type="application/json")
    parsed = parse_and_fix_json(raw_res)
    if not isinstance(parsed, dict):
        return None

    cleaned_summary = {}
    for lang in TARGET_LANGS:
        val = parsed.get(lang)
        if not isinstance(val, str) or not val.strip():
            return None
        val = val.replace("\n", " ／ ").strip()
        val = re.sub(r'\s*/\s*', ' ／ ', val)
        cleaned_summary[lang] = val

    return cleaned_summary


def validate_single_highlight(hl_data, min_start, max_start):
    """laugh または quote の1項目分のデータを検証・整形する"""
    if not isinstance(hl_data, dict):
        return None

    text_obj = hl_data.get("text")
    if not isinstance(text_obj, dict):
        return None

    cleaned_text = {}
    for lang in TARGET_LANGS:
        val = text_obj.get(lang)
        if not isinstance(val, str) or not val.strip():
            return None
        cleaned_text[lang] = val.replace("\n", " ").strip()

    try:
        chosen_start = float(hl_data.get("start", min_start))
        if not (min_start <= chosen_start <= max_start):
            chosen_start = min_start
    except (ValueError, TypeError):
        chosen_start = min_start

    return {
        "start": round(chosen_start, 2),
        "timestamp": format_timestamp(chosen_start),
        "text": cleaned_text
    }


def extract_dual_candidates_from_chunk(original_title, chunk_text, part_idx, total_parts):
    """長尺動画のチャンクから「面白シーン候補」と「名言シーン候補」を1コールで同時メモ抽出"""
    prompt = f"""あなたはタイの5人組ボーイズグループ「PERSES」のファンサイトの編集者です。
以下は動画（原題: {original_title}）のタイムスタンプ付き会話ログの分割パート（{part_idx}/{total_parts}）です。
このパートの中から、以下の2種類のハイライト候補をそれぞれ最大1つずつ日本語で短く抜き出してください（該当がなければ「なし」と記載）。

1. 【面白シーン候補】: 【笑い(555)】タグがある場面はもちろん、555が無くてもメンバーのボケ・ツッコミ・無茶振り・天然発言・イジり・焦るリアクションなど、会話としてクスッと笑える場面。
2. 【名言シーン候補】: メンバーの熱い想い、ファン（PIECES）への感謝、グループへの覚悟、仲間の絆など、心に響く言葉がある場面。

※どちらも該当箇所の `start` 秒数、前後の流れ、一番輝いている発言をメモしてください。

【会話ログ（パート {part_idx}/{total_parts}）】
{chunk_text}
"""
    return call_gemini_with_retry(prompt, response_mime_type="text/plain")


def generate_dual_highlights(original_title, items):
    """「面白ハイライト(laugh)」と「名言ハイライト(quote)」の2種類を同時に6言語で生成する"""
    timestamped_lines = []
    for x in items:
        ts = format_timestamp(x["start"])
        laugh_tag = " 【笑い(555)】" if x["has_laugh"] else ""
        timestamped_lines.append(f"[{ts}] (start={x['start']}) {x['text']}{laugh_tag}")

    full_dialogue = "\n".join(timestamped_lines)

    if len(full_dialogue) <= HIGHLIGHT_FULL_PASS_LIMIT:
        dialogue_source = full_dialogue
        source_label = "動画全体の会話ログ"
    else:
        chunks = split_into_chunks(timestamped_lines, CHUNK_CHAR_LIMIT)
        print(f"   📚 長尺のため {len(chunks)} パートから面白＆名言候補を抽出中...")
        candidates = []
        for c_idx, c_text in enumerate(chunks, start=1):
            cand = extract_dual_candidates_from_chunk(original_title, c_text, c_idx, len(chunks))
            if cand:
                candidates.append(f"【パート{c_idx}候補】\n{cand}")
        if not candidates:
            return None
        dialogue_source = "\n\n".join(candidates)
        source_label = "各パートから抽出されたハイライト候補"

    prompt = f"""あなたはタイの5人組ボーイズグループ「PERSES」のファンサイトの編集者です。
以下は動画（原題: {original_title}）の{source_label}です。
この中から、ファンが見たくなる次の**2種類のハイライト**を両方（別々のシーンから）抽出し、指定のJSON形式で出力してください。

【抽出する2種類のハイライト】
1. `laugh`（一番笑える・面白いシーン）:
   - 【笑い(555)】タグが付いている場面だけでなく、**555が一切無くても、メンバー同士のボケ・ツッコミ・無茶振り・天然発言・イジり・焦りや照れなどのやりとりから、会話として一番面白い場面**を選ぶこと。
   - 完全にシリアスな内容で面白い要素が全く無い動画の場合は、無理に作らず `null` にすること。
2. `quote`（一番グッとくる名言シーン）:
   - メンバーの熱い想い、ファン（PIECES）への感謝、努力やグループへの覚悟、絆など、心に響く言葉が語られている場面を選ぶこと。
   - 短い告知や軽い雑談のみで名言らしい発言が全く無い動画の場合は、無理に作らず `null` にすること。

【厳守ルール】
1. 出力は必ず `"laugh"` と `"quote"` をトップレベルのキーに持つ純粋なJSONオブジェクトのみとすること（片方または両方が該当なしの場合は `null`）。
2. `start` には、選んだシーンの会話が始まる行の `start` の数値をそのまま選ぶこと。`laugh` と `quote` は必ず異なるシーン（別の `start`）から選ぶこと。
3. `text` の各言語の文字列は、必ず【状況の一言説明（体言止め）】の直後に、そのシーンで一番輝いている発言を【「」で囲んで追記】する構成にすること。
   - 構成イメージ: 状況の短い説明「一番輝いているセリフ」
   - 引用するセリフは会話ログから核心部分だけを短く切り抜くこと（日本語で15〜20文字前後、全体で35〜40文字前後を目安）。
4. 6言語（"ja", "en", "ko", "zh-TW", "id", "pt"）すべてで意味が一致するようにし、引用の括弧は全言語共通で「」を使用すること。
5. すべての言語で、グループ名・メンバー名などの固有名詞は必ず英語表記で統一すること（例: PERSES, PIECES, JUNG, NAY, KRITTIN, PALM, PLUGGY）。

【出力JSONフォーマット例】
{{
  "laugh": {{
    "start": 865.2,
    "text": {{
      "ja": "PLUGGYの無茶振りにタジタジになるKRITTIN「これ放送できるの！？」",
      "en": "KRITTIN getting flustered by PLUGGY's sudden dare 「Can we even air this!?」",
      "ko": "PLUGGY의 무리한 부탁에 당황하는 KRITTIN 「이거 방송에 나가도 돼!?」",
      "zh-TW": "被PLUGGY的無理要求弄得手足無措的KRITTIN「這真的能播出嗎！？」",
      "id": "KRITTIN salah tingkah karena tantangan mendadak PLUGGY 「Ini boleh ditayangin!?」",
      "pt": "KRITTIN ficando sem jeito com o desafio repentino de PLUGGY 「Isso pode ir ao ar!?」"
    }}
  }},
  "quote": {{
    "start": 1120.5,
    "text": {{
      "ja": "ファンへの感謝を真っ直ぐに語るJUNG「PIECESがいなければ今の僕たちはいない」",
      "en": "JUNG expressing heartfelt gratitude to fans 「Without PIECES, we wouldn't be here today」",
      "ko": "팬들에게 진심 어린 감사를 전하는 JUNG 「PIECES가 없었다면 지금의 우리도 없었을 것」",
      "zh-TW": "真誠向粉絲表達感謝的JUNG「沒有PIECES就沒有現在的我們」",
      "id": "JUNG menyampaikan rasa terima kasih tulus untuk fans 「Tanpa PIECES, kami tidak akan ada di sini」",
      "pt": "JUNG expressando profunda gratidão aos fãs 「Sem PIECES, não estaríamos aqui hoje」"
    }}
  }}
}}
"""
    raw_res = call_gemini_with_retry(prompt, response_mime_type="application/json")
    parsed = parse_and_fix_json(raw_res)
    if not isinstance(parsed, dict):
        return None

    min_start = items[0]["start"]
    max_start = items[-1]["start"]

    laugh_obj = validate_single_highlight(parsed.get("laugh"), min_start, max_start)
    quote_obj = validate_single_highlight(parsed.get("quote"), min_start, max_start)

    return {
        "laugh": laugh_obj,
        "quote": quote_obj
    }


def is_summary_complete(video_obj):
    summary = video_obj.get("summary")
    if not isinstance(summary, dict):
        return False
    return all(isinstance(summary.get(lang), str) and summary.get(lang).strip() for lang in TARGET_LANGS)


def is_highlights_complete(video_obj):
    hls = video_obj.get("highlights")
    if not isinstance(hls, dict):
        return False
    return "laugh" in hls and "quote" in hls


def main():
    print(f"🚀 多言語サマリー＆2種ハイライト生成を開始します (モデル: {MODEL_NAME} / 間隔: {API_SLEEP_SECONDS}秒 / APIキー数: {len(API_KEYS)}個)")
    videos = load_json(VIDEOS_JSON_PATH, [])
    if not videos:
        print(f"❌ {VIDEOS_JSON_PATH} が見つからないか空です。")
        return

    status_data = load_json(STATUS_FILE_PATH, {})
    videos_status = status_data.get("videos", {})

    def get_priority(v):
        vid = v.get("id", "")
        return videos_status.get(vid, {}).get("priority", 999999)

    sorted_indices = sorted(range(len(videos)), key=lambda idx: get_priority(videos[idx]))

    updated_count = 0
    skipped_count = 0

    for idx in sorted_indices:
        video = videos[idx]
        vid = video.get("id")
        original_title = video.get("original_title", "")
        keywords = video.get("keywords", [])

        if "#HIDDEN" in keywords:
            skipped_count += 1
            continue

        need_summary = FORCE_REGENERATE_SUMMARY or not is_summary_complete(video)
        need_highlights = FORCE_REGENERATE_HIGHLIGHTS or not is_highlights_complete(video)

        if not need_summary and not need_highlights:
            skipped_count += 1
            continue

        items = extract_transcript_items(vid)
        if not items:
            print(f"⏭️ [{vid}] 文字起こしデータが空のためスキップします。")
            skipped_count += 1
            continue

        lines = [x["text"] for x in items]
        full_text = "\n".join(lines)
        total_chars = len(full_text)
        print(f"\n🎬 処理中: [{vid}] {original_title} ({total_chars:,}文字)")

        video_changed = False

        try:
            # 1. summary の生成
            if need_summary:
                if total_chars <= CHUNK_CHAR_LIMIT:
                    summary_dict = generate_multilingual_summary(original_title, full_text, is_merged_notes=False)
                else:
                    chunks = split_into_chunks(lines, CHUNK_CHAR_LIMIT)
                    print(f"   📚 長文のため {len(chunks)} パートに分割して要約します...")
                    part_notes = []
                    for c_idx, chunk_text in enumerate(chunks, start=1):
                        print(f"   - パート {c_idx}/{len(chunks)} を要約中...")
                        note = summarize_chunk_ja(original_title, chunk_text, c_idx, len(chunks))
                        if note:
                            part_notes.append(f"【パート{c_idx}】\n{note}")

                    if part_notes:
                        merged_text = "\n\n".join(part_notes)
                        print(f"   🔗 全パートの要点を統合して6言語サマリーを生成中...")
                        summary_dict = generate_multilingual_summary(original_title, merged_text, is_merged_notes=True)
                    else:
                        summary_dict = None

                if summary_dict:
                    videos[idx]["summary"] = summary_dict
                    video_changed = True
                    print(f"   ✅ サマリー生成: {summary_dict['ja']}")
                else:
                    print(f"   ⚠️ サマリーの生成に失敗しました。")

            # 2. highlights（面白 & 名言 の2種類同時抽出）
            if need_highlights:
                print(f"   🎬 面白シーン＆名言シーンを同時抽出中...")
                hls_dict = generate_dual_highlights(original_title, items)
                if hls_dict is not None:
                    # 旧形式の単数キー "highlight" が残っていれば整理
                    videos[idx].pop("highlight", None)
                    videos[idx]["highlights"] = hls_dict
                    video_changed = True

                    if hls_dict["laugh"]:
                        l_item = hls_dict["laugh"]
                        print(f"   😂 面白ハイライト: [{l_item['timestamp']}] {l_item['text']['ja']}")
                    else:
                        print(f"   😂 面白ハイライト: 該当なし (null)")

                    if hls_dict["quote"]:
                        q_item = hls_dict["quote"]
                        print(f"   ✨ 名言ハイライト: [{q_item['timestamp']}] {q_item['text']['ja']}")
                    else:
                        print(f"   ✨ 名言ハイライト: 該当なし (null)")
                else:
                    print(f"   ⚠️ ハイライトの生成に失敗しました。")

            if video_changed:
                save_json(VIDEOS_JSON_PATH, videos)
                updated_count += 1

        except RuntimeError as re_err:
            print(f"\n{re_err}")
            break
        except Exception as e:
            print(f"❌ [{vid}] 予期せぬエラー: {e}")

    print(f"\n🎉 処理終了: 更新 {updated_count} 件 / スキップ {skipped_count} 件")


if __name__ == "__main__":
    main()