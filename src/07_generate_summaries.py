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
OUTPUT_JSON_PATH = '../data/07_summaries.json'

MODEL_NAME = "gemini-3.5-flash-lite"

# レート制限設定（1分に15回以内 = 1リクエストあたり最低4秒間隔）
API_SLEEP_SECONDS = 4.0

# 1回のAPI通信で直接処理する最大文字数（これを超える超長尺のみ分割処理）
SINGLE_PASS_CHAR_LIMIT = 45000
CHUNK_CHAR_LIMIT = 15000

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


def save_sorted_summaries(path, summaries_dict):
    """published_at の降順（新しい順）にソートして保存する"""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    sorted_items = sorted(
        summaries_dict.items(),
        key=lambda kv: str(kv[1].get("published_at") or ""),
        reverse=True
    )
    sorted_dict = dict(sorted_items)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(sorted_dict, f, ensure_ascii=False, indent=2)


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
                temperature=0.15,
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
    """transcriptファイルからID付きセリフ一覧を抽出する"""
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
            "id": len(items),
            "start": start_sec,
            "orig": orig_text,
            "text": display_text,
            "has_laugh": has_laugh
        })
    return items


def build_dialogue_log(items, rejected_starts=None):
    """AIに渡す [ID: 数字] (MM:SS) セリフ 形式のログを作成する"""
    rejected_starts = rejected_starts or []
    lines = []
    for x in items:
        ts = format_timestamp(x["start"])
        tags = []
        if x["has_laugh"]:
            tags.append("【笑い(555)】")
        # 除外対象タイムスタンプの前後20秒には除外マークを付与
        if any(abs(x["start"] - r_sec) <= 20.0 for r_sec in rejected_starts):
            tags.append("【ハイライト除外区間】")
        tag_str = " " + "".join(tags) if tags else ""
        lines.append(f"[ID: {x['id']}] ({ts}) {x['text']}{tag_str}")
    return "\n".join(lines)


def split_lines_into_chunks(lines, max_chars=CHUNK_CHAR_LIMIT):
    """文字数上限を超えないように文字列リストを分割する"""
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


def extract_chunk_notes(original_title, chunk_text, part_idx, total_parts):
    """45,000文字を超える超長尺動画用：パートごとの事実メモと候補IDを抽出"""
    prompt = f"""あなたはタイの5人組ボーイズグループ「PERSES」のアーカイブ編集者です。
以下は動画（原題: {original_title}）のID付き会話ログの分割パート（{part_idx}/{total_parts}）です。
このパートの内容を整理し、以下の3点を日本語のメモとして簡潔に出力してください。

1. 【このパートの話題と結論】：話されていた主なトピックと、会話がどう着地したか（事実のみ）。
2. 【笑いシーン候補ID】：面白かったやり取りがあれば、その開始ID〜終了ID（最大4行）と簡単な状況メモ（※【ハイライト除外区間】は避けること）。なければ「なし」。
3. 【感動・名言シーン候補ID】：熱い想いや感謝など心に響く場面があれば、その開始ID〜終了ID（最大4行）と簡単な状況メモ（※【ハイライト除外区間】は避けること）。なければ「なし」。

【会話ログ（パート {part_idx}/{total_parts}）】
{chunk_text}
"""
    return call_gemini_with_retry(prompt, response_mime_type="text/plain")


def build_highlight_from_ids(hl_raw, items, rejected_starts=None, exclude_range=None):
    """AIが指定した start_id 〜 end_id から、本物の字幕テキストを結合してハイライトを作る"""
    if not isinstance(hl_raw, dict):
        return None

    summary_text = hl_raw.get("summary")
    start_id = hl_raw.get("start_id")
    end_id = hl_raw.get("end_id")

    if not isinstance(summary_text, str) or not summary_text.strip():
        return None
    if not isinstance(start_id, int) or not isinstance(end_id, int):
        return None

    max_idx = len(items) - 1
    if start_id < 0 or start_id > max_idx:
        return None
    if end_id < start_id:
        end_id = start_id
    # 長くなりすぎないよう最大4行までに制限
    if end_id - start_id > 3:
        end_id = start_id + 3
    if end_id > max_idx:
        end_id = max_idx

    chosen_start = items[start_id]["start"]

    # 除外タイムスタンプと被っている場合は弾く
    if rejected_starts and any(abs(chosen_start - r_sec) <= 15.0 for r_sec in rejected_starts):
        return None

    # もう一方のハイライトと範囲が丸被りしている場合は弾く
    if exclude_range and not (end_id < exclude_range[0] or start_id > exclude_range[1]):
        return None

    # 実字幕からセリフを抽出して「〜」「〜」形式で結合
    quote_parts = []
    for i in range(start_id, end_id + 1):
        line_text = items[i]["text"].strip()
        # 二重括弧防止
        line_text = line_text.strip("「」")
        if line_text:
            quote_parts.append(f"「{line_text}」")

    if not quote_parts:
        return None

    clean_summary = summary_text.replace("\n", " ").strip()
    clean_summary = re.sub(r'「.*」$', '', clean_summary).strip()

    return {
        "timestamp": format_timestamp(chosen_start),
        "start": round(chosen_start, 2),
        "summary": clean_summary,
        "dialogue": "".join(quote_parts),
        "_id_range": (start_id, end_id)
    }


def generate_video_summary_one_pass(original_title, items, rejected_starts=None):
    """1回のAPI通信で topics, episode_digest, laugh, quote を一括生成する"""
    full_log = build_dialogue_log(items, rejected_starts=rejected_starts)

    if len(full_log) <= SINGLE_PASS_CHAR_LIMIT:
        source_block = full_log
        source_title = "動画全体のID付き会話ログ"
    else:
        log_lines = full_log.split("\n")
        chunks = split_lines_into_chunks(log_lines, CHUNK_CHAR_LIMIT)
        print(f"   📚 超長尺（{len(full_log):,}文字）のため {len(chunks)} パートに整理してから統合します...")
        notes = []
        for idx, c_text in enumerate(chunks, start=1):
            print(f"   - パート {idx}/{len(chunks)} を解析中...")
            note = extract_chunk_notes(original_title, c_text, idx, len(chunks))
            if note:
                notes.append(f"=== パート {idx} ===\n{note}")
        if not notes:
            return None
        source_block = "\n\n".join(notes)
        source_title = "各パートの整理メモ（ID付き）"

    reject_note = ""
    if rejected_starts:
        ts_list = ", ".join(format_timestamp(s) for s in rejected_starts)
        reject_note = f"\n※注意：【ハイライト除外区間】タグが付いている時間帯（{ts_list} 付近）は、すでにボツになったシーンのため `laugh` と `quote` のどちらにも絶対に選ばないでください。"

    prompt = f"""あなたはタイの5人組ボーイズグループ「PERSES」のファン向けアーカイブ＆SNS企画担当の編集者です。
以下の動画情報をもとに、日本語の要点データを作成し、指定のJSON形式のみで出力してください。

【動画の原題】
{original_title}

【{source_title}】
{source_block}

【作成する4項目と厳守ルール】
1. `topics`（主なトピック：文字列の配列）:
   - 動画内で実際に話されている主な話題・見どころを3〜5個の短いフレーズ（体言止め）で挙げること。
2. `episode_digest`（エピソードダイジェスト：文字列）:
   - `topics` よりも詳しく、「どんな話題・出来事から始まり → どんなやり取りを経て → 最終的にどういう結論・オチになったか」という**会話の流れと結果**までを、3〜4文（130〜200文字程度）の自然な日本語でまとめること。SNS投稿のネタとして使えるよう、出来事の着地点を明確に書くこと。
3. `laugh`（一番笑える・面白いシーン：オブジェクト または `null`）:
   - 【笑い(555)】タグがある場面はもちろん、555が無くてもメンバー同士のボケ・ツッコミ・無茶振り・天然発言・焦りなどのやり取りから一番面白い場面を1つ選ぶこと。
   - **短い告知動画や完全にシリアスな回で、面白い要素が全く無い場合は無理に選ばず必ず `null` にすること。**
   - 選ぶ場合は、その場面の短い状況説明（`summary`：20文字前後）と、会話ログにある実際の行番号（`start_id` と `end_id`、連続する1〜4行）を指定すること。
4. `quote`（一番感動する・グッとくる名言シーン：オブジェクト または `null`）:
   - メンバーの熱い想い、ファン（PIECES）への感謝、グループへの覚悟や絆など、心に響く言葉がある場面を1つ選ぶこと。
   - **軽い雑談や告知のみで、感動・名言らしい発言が全く無い場合は無理に選ばず必ず `null` にすること。**
   - 選ぶ場合は、その場面の短い状況説明（`summary`：20文字前後）と、会話ログにある実際の行番号（`start_id` と `end_id`、連続する1〜4行）を指定すること。

【共通の絶対ルール（捏造禁止）】
- ログに書かれていない事実・展開・オチを絶対に創作しないこと。
- 会話ログ内にメンバーの名前が明記されていない限り、「JUNGが〜」「KRITTINが〜」のように誰の発言か推測でメンバー名を書かないこと（不明な場合は「メンバーが〜」「トークの中で〜」と書くこと）。
- グループ名・メンバー名・ファンダム名などの固有名詞は英語表記で統一すること（PERSES, PIECES, JUNG, NAY, KRITTIN, PALM, PLUGGY）。
- `laugh` と `quote` は必ず別々の時間帯（異なるID）から選ぶこと。{reject_note}

【出力JSONフォーマット】
{{
  "topics": [
    "トピック1",
    "トピック2",
    "トピック3"
  ],
  "episode_digest": "話題の始まりから途中のやり取り、最終的な結論やオチまでをまとめた3〜4文の文章。",
  "laugh": {{
    "summary": "やり取りの短い概要",
    "start_id": 120,
    "end_id": 122
  }},
  "quote": {{
    "summary": "場面の短い概要",
    "start_id": 245,
    "end_id": 247
  }}
}}
"""
    raw_res = call_gemini_with_retry(prompt, response_mime_type="application/json")
    parsed = parse_and_fix_json(raw_res)
    if not isinstance(parsed, dict):
        return None

    # topics の検証
    raw_topics = parsed.get("topics")
    topics = [str(t).strip() for t in raw_topics if str(t).strip()] if isinstance(raw_topics, list) else []

    # episode_digest の検証
    digest = parsed.get("episode_digest")
    digest = digest.strip() if isinstance(digest, str) and digest.strip() else ""

    if not topics or not digest:
        return None

    # laugh と quote の実字幕結合
    laugh_obj = build_highlight_from_ids(parsed.get("laugh"), items, rejected_starts=rejected_starts)
    laugh_range = laugh_obj.pop("_id_range", None) if laugh_obj else None

    quote_obj = build_highlight_from_ids(
        parsed.get("quote"),
        items,
        rejected_starts=rejected_starts,
        exclude_range=laugh_range
    )
    if quote_obj:
        quote_obj.pop("_id_range", None)

    return {
        "topics": topics,
        "episode_digest": digest,
        "laugh": laugh_obj,
        "quote": quote_obj
    }


def main():
    print(f"🚀 日本語サマリー＆ツイートネタ帳生成を開始します (モデル: {MODEL_NAME} / 間隔: {API_SLEEP_SECONDS}秒)")
    videos = load_json(VIDEOS_JSON_PATH, [])
    if not videos:
        print(f"❌ {VIDEOS_JSON_PATH} が見つからないか空です。")
        return

    status_data = load_json(STATUS_FILE_PATH, {})
    videos_status = status_data.get("videos", {})

    # 既存の 07_summaries.json を読み込み
    summaries = load_json(OUTPUT_JSON_PATH, {})
    if not isinstance(summaries, dict):
        summaries = {}

    # 処理順は priority 順（保存時は published_date 降順にソート）
    def get_priority(v):
        vid = v.get("id", "")
        return videos_status.get(vid, {}).get("priority", 999999)

    sorted_videos = sorted(videos, key=get_priority)

    updated_count = 0
    skipped_count = 0

    for video in sorted_videos:
        vid = video.get("id")
        if not vid:
            continue

        keywords = video.get("keywords", [])
        if "#HIDDEN" in keywords:
            skipped_count += 1
            continue

        original_title = video.get("original_title", "")
        published_at = video.get("published_at") or ""

        existing = summaries.get(vid)

        # 初回生成 or 部分やり直し（null へ手動変更された項目）の判定
        need_topics = True
        need_digest = True
        need_laugh = True
        need_quote = True
        rejected_starts = []
        prev_starts = {}

        if isinstance(existing, dict):
            done_fields = set(existing.get("_done_fields", []))
            prev_starts = dict(existing.get("_prev_starts", {}))
            rejected_starts = list(existing.get("rejected_timestamps", []))

            need_topics = not bool(existing.get("topics"))
            need_digest = not bool(existing.get("episode_digest"))

            # laugh: 中身があれば完了。nullの場合、過去に生成済み(_done_fieldsにある)なら手動やり直しと判定
            if isinstance(existing.get("laugh"), dict):
                need_laugh = False
            elif "laugh" in done_fields:
                need_laugh = True
                if "laugh" in prev_starts and prev_starts["laugh"] not in rejected_starts:
                    rejected_starts.append(prev_starts["laugh"])
            else:
                need_laugh = True

            # quote: 同上
            if isinstance(existing.get("quote"), dict):
                need_quote = False
            elif "quote" in done_fields:
                need_quote = True
                if "quote" in prev_starts and prev_starts["quote"] not in rejected_starts:
                    rejected_starts.append(prev_starts["quote"])
            else:
                need_quote = True

        if not (need_topics or need_digest or need_laugh or need_quote):
            # published_at が未反映、または旧キー published_date が残っている場合だけ同期
            if isinstance(existing, dict) and (
                existing.get("published_at") != published_at or "published_date" in existing
            ):
                existing.pop("published_date", None)
                existing["published_at"] = published_at
                save_sorted_summaries(OUTPUT_JSON_PATH, summaries)
            skipped_count += 1
            continue
            
        items = extract_transcript_items(vid)
        if not items:
            print(f"⏭️ [{vid}] 文字起こしデータが空のためスキップします。")
            skipped_count += 1
            continue

        target_labels = [
            name for name, flag in [
                ("topics", need_topics),
                ("digest", need_digest),
                ("laugh", need_laugh),
                ("quote", need_quote)
            ] if flag
        ]
        print(f"\n🎬 処理中: [{vid}] {original_title}")
        print(f"   対象項目: {', '.join(target_labels)}" + (f" (除外秒数: {rejected_starts})" if rejected_starts else ""))

        try:
            result = generate_video_summary_one_pass(
                original_title=original_title,
                items=items,
                rejected_starts=rejected_starts
            )
            if not result:
                print(f"   ⚠️ 生成またはJSONパースに失敗しました。")
                continue

            # 既存データがある場合は、やり直し対象（nullだった項目）だけを差し替える
            if isinstance(existing, dict):
                merged = dict(existing)
            else:
                merged = {}

            merged.pop("published_date", None)
            merged["published_at"] = published_at
            merged["original_title"] = original_title

            if need_topics:
                merged["topics"] = result["topics"]
            if need_digest:
                merged["episode_digest"] = result["episode_digest"]
            if need_laugh:
                merged["laugh"] = result["laugh"]
                if result["laugh"]:
                    prev_starts["laugh"] = result["laugh"]["start"]
            if need_quote:
                merged["quote"] = result["quote"]
                if result["quote"]:
                    prev_starts["quote"] = result["quote"]["start"]

            merged["rejected_timestamps"] = rejected_starts
            merged["_prev_starts"] = prev_starts
            merged["_done_fields"] = ["topics", "episode_digest", "laugh", "quote"]

            summaries[vid] = merged
            save_sorted_summaries(OUTPUT_JSON_PATH, summaries)
            updated_count += 1

            print(f"   ✅ 保存完了 ({published_at}):")
            print(f"      📌 トピック: {' ／ '.join(merged['topics'])}")
            print(f"      📝 ダイジェスト: {merged['episode_digest']}")
            if merged["laugh"]:
                l_data = merged["laugh"]
                print(f"      😂 笑い [{l_data['timestamp']}]: {l_data['summary']} -> {l_data['dialogue']}")
            else:
                print(f"      😂 笑い: なし (null)")
            if merged["quote"]:
                q_data = merged["quote"]
                print(f"      ✨ 感動 [{q_data['timestamp']}]: {q_data['summary']} -> {q_data['dialogue']}")
            else:
                print(f"      ✨ 感動: なし (null)")

        except RuntimeError as re_err:
            print(f"\n{re_err}")
            break
        except Exception as e:
            print(f"❌ [{vid}] 予期せぬエラー: {e}")

    print(f"\n🎉 処理終了: 更新 {updated_count} 件 / スキップ {skipped_count} 件")


if __name__ == "__main__":
    main()