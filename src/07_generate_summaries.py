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

# 長文分割（チャンキング）の閾値（文字数）
CHUNK_CHAR_LIMIT = 12000

# 既存の summary がある場合でも強制的に再生成するかどうか
FORCE_REGENERATE = False

# APIキーの設定とローテーション管理
api_keys_str = os.getenv("GEMINI_API_KEYS")
if not api_keys_str:
    raise ValueError("エラー: .envファイルに GEMINI_API_KEYS が設定されていません。")

API_KEYS = [k.strip() for k in api_keys_str.split(",") if k.strip()]
current_key_index = 0
exhausted_keys = set()


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

    # カンマや括弧の軽微な崩れを修復して再試行
    fixed = re.sub(r',\s*([}\]])', r'\1', text)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        return None


def call_gemini_with_retry(prompt, response_mime_type="application/json"):
    """429ローテーション・503リトライ付きのGemini呼び出し"""
    max_retries = len(API_KEYS) * 2 + 2
    for attempt in range(max_retries):
        try:
            client = get_client()
            config = types.GenerateContentConfig(
                temperature=0.2,
                response_mime_type=response_mime_type,
            )
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=config
            )
            return response.text
        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                rotate_key()
                time.sleep(1)
            elif "503" in err_msg or "UNAVAILABLE" in err_msg or "500" in err_msg:
                print(f"⚠️ サーバー一時エラー ({attempt + 1}/{max_retries})。5秒待機します...")
                time.sleep(5)
            else:
                print(f"⚠️ APIエラー: {e}")
                time.sleep(3)
    return None


def extract_transcript_lines(video_id):
    """transcriptファイルから日本語訳（なければ原文）を抽出する"""
    t_path = os.path.join(TRANSCRIPTS_DIR, f"video_{video_id}.json")
    if not os.path.exists(t_path):
        return []
    data = load_json(t_path, {})
    transcript = data.get("transcript", [])
    lines = []
    for item in transcript:
        trans = item.get("translations", {})
        ja_text = trans.get("ja", "").strip()
        orig_text = item.get("text", "").strip()
        line = ja_text if ja_text else orig_text
        if line:
            lines.append(line)
    return lines


def split_into_chunks(lines, max_chars=CHUNK_CHAR_LIMIT):
    """文字数上限を超えないようにセリフ配列をチャンク分割する"""
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

    # 6言語すべてのキーが存在し、文字列が入っているか検証・クレンジング
    cleaned_summary = {}
    for lang in TARGET_LANGS:
        val = parsed.get(lang)
        if not isinstance(val, str) or not val.strip():
            return None
        # 万が一改行や半角スラッシュ区切りになっていた場合の正規化
        val = val.replace("\n", " ／ ").strip()
        val = re.sub(r'\s*/\s*', ' ／ ', val)
        cleaned_summary[lang] = val

    return cleaned_summary


def is_summary_complete(video_obj):
    summary = video_obj.get("summary")
    if not isinstance(summary, dict):
        return False
    return all(isinstance(summary.get(lang), str) and summary.get(lang).strip() for lang in TARGET_LANGS)


def main():
    print(f"🚀 多言語サマリー生成を開始します (使用モデル: {MODEL_NAME})")
    videos = load_json(VIDEOS_JSON_PATH, [])
    if not videos:
        print(f"❌ {VIDEOS_JSON_PATH} が見つからないか空です。")
        return

    status_data = load_json(STATUS_FILE_PATH, {})
    videos_status = status_data.get("videos", {})

    # priority順にソート（指定がない場合は末尾へ）
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

        # 非表示動画はスキップ
        if "#HIDDEN" in keywords:
            skipped_count += 1
            continue

        # 完了済みスキップ
        if not FORCE_REGENERATE and is_summary_complete(video):
            skipped_count += 1
            continue

        lines = extract_transcript_lines(vid)
        if not lines:
            print(f"⏭️ [{vid}] 文字起こしデータが空のためスキップします。")
            skipped_count += 1
            continue

        full_text = "\n".join(lines)
        total_chars = len(full_text)
        print(f"\n🎬 処理中: [{vid}] {original_title} ({total_chars:,}文字)")

        try:
            if total_chars <= CHUNK_CHAR_LIMIT:
                # 通常処理（1回で6言語生成）
                summary_dict = generate_multilingual_summary(original_title, full_text, is_merged_notes=False)
            else:
                # 長文処理（分割 -> パート要約 -> 統合6言語生成）
                chunks = split_into_chunks(lines, CHUNK_CHAR_LIMIT)
                print(f"   📚 長文のため {len(chunks)} パートに分割して要約します...")
                part_notes = []
                for c_idx, chunk_text in enumerate(chunks, start=1):
                    print(f"   - パート {c_idx}/{len(chunks)} を要約中...")
                    note = summarize_chunk_ja(original_title, chunk_text, c_idx, len(chunks))
                    if note:
                        part_notes.append(f"【パート{c_idx}】\n{note}")
                    time.sleep(1)

                if not part_notes:
                    print(f"❌ [{vid}] パート要約の生成に失敗しました。")
                    continue

                merged_text = "\n\n".join(part_notes)
                print(f"   🔗 全パートの要点を統合して6言語サマリーを生成中...")
                summary_dict = generate_multilingual_summary(original_title, merged_text, is_merged_notes=True)

            if summary_dict:
                videos[idx]["summary"] = summary_dict
                # 1動画ごとに即時保存（途中中断しても進捗を失わないようにする）
                save_json(VIDEOS_JSON_PATH, videos)
                updated_count += 1
                print(f"✅ [{vid}] 保存完了:")
                print(f"   JA: {summary_dict['ja']}")
            else:
                print(f"⚠️ [{vid}] 6言語サマリーのパースまたは生成に失敗しました。")

            time.sleep(1.5)

        except RuntimeError as re_err:
            print(f"\n{re_err}")
            break
        except Exception as e:
            print(f"❌ [{vid}] 予期せぬエラー: {e}")

    print(f"\n🎉 処理終了: 新規更新 {updated_count} 件 / スキップ {skipped_count} 件")


if __name__ == "__main__":
    main()