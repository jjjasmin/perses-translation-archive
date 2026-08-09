import json
import os
import re
import sys
import time
from google import genai
from google.genai.errors import ServerError
from youtube_transcript_api import YouTubeTranscriptApi
from yt_dlp import YoutubeDL

# ==========================================
# 1. 設定項目
# ==========================================

API_KEY = "AQ.Ab8RN6LMel5f9eDauo2yWZ_nn1-gDBqOVnbS6KU_lZreQ-YZAQ"
# API_KEY = "AQ.Ab8RN6IckkMNj2e0Z97sG324h91XwBYWIfx8p-hYoN6wb1F9-w"
# API_KEY = "AQ.Ab8RN6KIOFkUXB2ZebeQTHy6vZXSqxiO4v-9vEcbF86Q_sql8Q"
# API_KEY = "AQ.Ab8RN6Jr04MDsopctb0CE09haL-J0ymOt_xdV6JbN6azaZ01Ig"

# 処理したいYouTube URLのリスト
TARGET_URLS = [
    "https://www.youtube.com/watch?v=rJZ3T3oT6a8",
"https://www.youtube.com/watch?v=IiOBo6ssGPA",
"https://www.youtube.com/watch?v=qjRQkRB9NOw",
"https://www.youtube.com/watch?v=gixs8ZYd-rs",
"https://www.youtube.com/watch?v=oa6LrtfsCSQ",
"https://www.youtube.com/watch?v=EvrRSUP13ss",
"https://www.youtube.com/watch?v=iRikP3GsfMs",
"https://www.youtube.com/watch?v=d1VmMrgdLHo",
"https://www.youtube.com/watch?v=Jz_4GoqCF1Q",
"https://www.youtube.com/watch?v=9OGfM16nlsg",
"https://www.youtube.com/watch?v=SDFCGK8lGeI",
"https://www.youtube.com/watch?v=rFVqcOktMBU",
"https://www.youtube.com/watch?v=iHL7rcpTDAI",
"https://www.youtube.com/watch?v=aBJIYoFsYmA",
"https://www.youtube.com/watch?v=Wf3V2uOdlk8",
"https://www.youtube.com/watch?v=H0ZsoCViNXo",
"https://www.youtube.com/watch?v=93mBEp6Ff60",
"https://www.youtube.com/watch?v=LjDE4QSPLqE",
"https://www.youtube.com/watch?v=7pJMaxloGR8",
"https://www.youtube.com/watch?v=59RkbMpYvmw",
"https://www.youtube.com/watch?v=hBHqDDHQEek",
"https://www.youtube.com/watch?v=Qlu20uwF-no",
"https://www.youtube.com/watch?v=2E34vK_Vwa8",
"https://www.youtube.com/watch?v=DvPPTW0-6cA",
"https://www.youtube.com/watch?v=JaUuEADjuyw",
"https://www.youtube.com/watch?v=0KJhVcA04CU",
"https://www.youtube.com/watch?v=Oac3KF04iDQ"
]

videos_file = "videos.json"

ALLOWED_TAGS = {
    "#JUNG", "#NAY", "#KRITTIN", "#PALM", "#PLUGGY", 
    "#ALL_MEMBER", "#TV", "#VLOG", "#SHORT", "#SHOW", 
    "#MV", "#FANCHANT", "#VIIS", "#TIGGER", "#PROXIE"
}

client = genai.Client(api_key=API_KEY)

# ==========================================
# 2. 補助関数（URL解析・JSON補正・重複チェック）
# ==========================================
def extract_video_id(url: str) -> str:
    """YouTubeの各種URLから11桁の動画IDを抽出"""
    match = re.search(r"(?:v=|\/([0-9A-Za-z_-]{11})(?:[?&]|$)|youtu\.be\/)([0-9A-Za-z_-]{11})", url)
    if match:
        return match.group(1) or match.group(2)
    return ""

def load_existing_video_ids(filepath: str) -> set:
    """videos.json から登録済みの動画ID一覧を取得"""
    if not os.path.exists(filepath):
        return set()
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
            return {item["id"] for item in data if isinstance(item, dict) and "id" in item}
    except Exception:
        return set()

def parse_and_fix_json(json_str: str):
    """Geminiの出力から不完全なJSONを復元"""
    cleaned_str = re.sub(r'^```json\s*', '', json_str)
    cleaned_str = re.sub(r'\s*```$', '', cleaned_str)

    try:
        return json.loads(cleaned_str)
    except json.JSONDecodeError:
        print("⚠️ 途中で切れた不完全なJSONを検知しました。補正処理を実行します...")
        last_valid_object_index = cleaned_str.rfind('}')
        if last_valid_object_index != -1:
            truncated = cleaned_str[:last_valid_object_index + 1]
            if not truncated.rstrip().endswith(']}'):
                truncated += '\n  ]\n}'
            try:
                fixed_data = json.loads(truncated)
                print("✅ 破損していた末尾をカットし、正常なJSONとして復元しました！")
                return fixed_data
            except json.JSONDecodeError:
                pass
        raise ValueError("JSONの復元に失敗しました。")

def get_video_metadata(video_id: str):
    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {'quiet': True, 'skip_download': True}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        title = info.get('title', '')
        upload_date = info.get('upload_date', '')
        raw_tags = info.get('tags', []) or []
        auto_keywords = [f"#{t.strip('#')}" for t in raw_tags if t]
        
        formatted_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}" if len(upload_date) == 8 else ""
        return title, formatted_date, auto_keywords

# ==========================================
# 3. メイン処理ループ
# ==========================================
existing_ids = load_existing_video_ids(videos_file)

for url in TARGET_URLS:
    video_id = extract_video_id(url)
    if not video_id:
        print(f"❌ 不正なURLのためスキップします: {url}")
        continue

    print(f"\n==========================================")
    print(f"🎬 処理開始: VIDEO_ID = {video_id}")
    print(f"==========================================")

    # 1. 重複チェック
    if video_id in existing_ids:
        print(f"⏩ 『{videos_file}』に登録済みのためスキップします。")
        continue

    # 2. メタ情報と字幕データの取得
    try:
        print("メタ情報を取得中...")
        title, published_at, auto_keywords = get_video_metadata(video_id)

        print("字幕データを取得中...")
        ytt_api = YouTubeTranscriptApi()
        try:
            fetched = ytt_api.fetch(video_id, languages=['th', 'en', 'ja'])
        except Exception:
            fetched = ytt_api.fetch(video_id)

        transcript_list = []
        for item in fetched:
            start_time = round(item.start, 2)
            end_time = round(start_time + item.duration, 2)
            transcript_list.append({
                "start": start_time,
                "end": end_time,
                "text": item.text
            })
    except Exception as e:
        print(f"❌ データ取得段階でエラーが発生しました: {e}")
        print("次の動画に進みます。")
        continue

    # 3. Gemini API プロンプト作成
    prompt = f"""あなたはタイのボーイズグループ「PERSES」のコンテンツを日本語に翻訳・解析する専門家です。

指定されたJSONフォーマットに従って完全なJSONデータのみを出力してください。

【グループおよび基本情報】
・グループ名: PERSES（読み方はパーセス） / ファンダム名: PIECES（読み方はピーセス）／所属会社: GNEST（読み方はジーネスト）
■ メンバー識別・呼称:
1. 🦥 ジャン（JUNG）
   ・愛称/呼称: ジャン、ピジャン
2. 🐒 ネー（NAY）
   ・愛称/呼称: ネー、ピネー
3. 🦈 クリッティン（KRITTIN）
   ・愛称/呼称: クリッティン、クリット
4. 🐶 パーム（PALM）
   ・愛称/呼称: パーム、ノンパーム、トンパーム
5. 🐱 プラッギー（PLUGGY）
   ・愛称/呼称: プラッギー、ギー、ノンギー、ギーギー

・今回の動画に登場する可能性のあるメンバー・関係者:
  - JUNG (ジャン)
  - NAY (ネー)
  - KRITTIN (クリッティン)
  - PALM (パーム)
  - PLUGGY (プラッギー)
  - STAFF (スタッフ/カメラマン等)
  - PIECES (ファン/観客等)
  - GUEST / OTHERS (その他出演者・ゲスト)

【話者判定（speaker フィールドの設定ルール）】
・発言者ごとに指定の「アルファベット名」（例: JUNG, NAY, STAFF 等）を speaker フィールドに格納してください。
・画面内に映っている人物だけでなく、画面外からの声も音声ややり取りから人物を識別してください。
・公式字幕や概要欄、文脈等から名前が取得できる人物（ゲスト等）は、その名前をアルファベット化した名称（例: P'AOF）を使用してください。
・全員が同時に発言している場合（挨拶や掛け声など）は、ALL や PERSES を使用してください。
・一人称は全メンバー共通で原則「僕」または「僕たち」に統一してください。
・ただし、自分の名前を一人称にしている愛嬌表現（例：「ギーはね〜」）は、ニュアンスを殺さずそのまま「〇〇は〜」と訳してください。

【話者判定の優先順位と注意マーク（❓）の付与】
1. 確定情報の優先: 公式字幕や概要欄に発言者の指定がある場合は、それを最優先してください。
2. 発言内容（タイ語）による文脈判定:
   映像や見た目の判定と、実際に話しているタイ語のニュアンス（敬語・語尾・口癖）が矛盾する場合は、発言内容の文脈を優先して発言者を再判定してください。
   ・例: メンバー同士の会話で明確な敬語（〜ครับ）を使っていれば、年下メンバー（PALMやPLUGGY）やSTAFF等の可能性が高い。
3. 発言者不確定時のルール（❓マークの付与）:
   画面が遠い・後ろ向き・複数人の声が混ざっている等で発言者を1人に特定する確信が持てない場合は、speaker のアルファベット名の末尾に「❓」を付与してください（例: speaker: "PALM❓"）。また、特定のキャラクター口調に偏らない標準的で自然な日本語で翻訳してください。

【日本語訳のトーン・表現ルール】
・個別の過度な役割語（キャラクター口調）は適用せず、タイ語原文のニュアンス（敬語・タメ口、感情の高ぶり）に忠実で、自然な日本語会話体に翻訳してください。
・文末に「クラップ/カー（ครับ/ค่ะ）」がついている発言や丁寧な単語が使われている場合は、日本語でも丁寧語・敬語（〜です/〜ます/〜ですね）を維持してください。
・日本語訳（translation）では、タイ語文字（เอ้ย, เฮ้ย, อุ้ย, อู้ว など）を出力することを【絶対厳禁】とします。
・リアクションや感嘆詞は【カタカナ音声】に変換してください。
・笑いの表現は「555」を使用してください。
・基本はタメ口で話している途中に急に文末詞をつけて「敬語」に戻った場合（照れ、皮肉、改まった雰囲気など）は、その落差（ギャップ）が日本語テロップでも伝わるように表現してください。

【タイ語のカタカナ表記推奨リスト（タイ沼・ファン向け）】
視聴者がタイカルチャーに親しみがあることを前提に、以下の定番単語・挨拶・リアクションは日本語に直訳（「こんにちは」「かわいい」等）せず、指定のカタカナ表記にカッコ書きでキャラクターに合わせた翻訳文を添えて出力してください。
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

【出力制約（厳格・文字エスケープルール）】
1. 必ず有効なJSONフォーマットのみを出力してください（Markdownの ```json ... ``` などの囲みも含めないでください）。
2. 文字数制限を意識し、途中で出力が切れないように記述してください。
3. すべての文字列は適切にダブルクォーテーションで閉じ、JSON構造（配列やオブジェクト）を途中で中断しないでください。
4. 【重要】タイ語原文（text）や日本語訳（translation）などの文章内に含まれるダブルクォーテーション（"）は、JSONの構文破綻を防ぐため、必ず `\"` にエスケープするか、「」や『』などの鍵かっこに置換して出力してください。改行文字が含まれる場合は必ず `\\n` としてエスケープしてください。

---

### 【データ作成ルール】

1. 動画タイトルの翻訳ルール:
 - 「title」フィールドには、動画の元のタイトルをそのまま直訳するのではなく、日本語話者にとって内容や魅力が伝わる「自然な日本語タイトル」に翻訳して設定してください。

2. members:
 - 動画に出演しているメンバー・登場人物のリスト（配列）。

3. transcript（字幕データ）:
 - id: 1から始まる連番。
 - start: 入力データの start の数値（float）をそのまま引き継いでください。
 - end: 入力データの end の数値（float）をそのまま引き継いでください。
 - speaker: 発話者名（JUNG, NAY, KRITTIN, PALM, PLUGGY, STAFF, PIECES 等。不確定時は末尾に❓を付与）。※translation側には名前を含めないでください。
 - text: タイ語原文。
 - pronunciation_kana: カタカナ発音。
 - pronunciation_roman: ローマ字発音。
 - translation: 自然でニュアンスの伝わる日本語訳（※話者名は含めない純粋な翻訳文）。

---

### 【出力フォーマット】
Markdownの枠（```json）も含めず、純粋なJSON文字列のみを出力してください。

{{
  "video_id": "{video_id}",
  "title": "【自然な日本語に翻訳した動画タイトル】",
  "published_at": "{published_at}",
  "thumbnail_url": "[https://img.youtube.com/vi/](https://img.youtube.com/vi/){video_id}/maxresdefault.jpg",
  "members": ["JUNG", "PALM"],
  "transcript": [
    {{
      "id": 1,
      "start": 0.0,
      "end": 3.5,
      "speaker": "JUNG",
      "text": "สวัสดีครับทุกคน",
      "pronunciation_kana": "サワッディー・クラップ・トゥックコン",
      "pronunciation_roman": "sawatdee krap thuk khon",
      "translation": "みなさんこんにちは。"
    }}
  ]
}}

---

### 【入力情報】
動画ID: {video_id}
元の動画タイトル: {title}

【字幕データ（JSON）】
{json.dumps(transcript_list, ensure_ascii=False)}
"""

    # 4. Gemini API 呼び出し（503リトライ処理付き）
    print("Gemini APIへ送信中... (解析には1〜2分かかります)")
    max_retries = 3
    success = False
    raw_text = ""

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model='gemini-3.5-flash',
                contents=prompt,
                config={'response_mime_type': 'application/json'}
            )
            raw_text = response.text.strip()
            success = True
            break
        except Exception as e:
            # 503エラー（またはServerError）の場合
            is_503 = "503" in str(e) or isinstance(e, ServerError)
            if is_503:
                print(f"⚠️ [503 UNAVAILABLE] エラーが発生しました。 ({attempt}/{max_retries}回目の試行)")
                if attempt < max_retries:
                    print("5秒後に再試行します...")
                    time.sleep(5)
                else:
                    print("❌ 3回再試行しましたがエラーが解消しませんでした。プログラムを異常終了します。")
                    sys.exit(1)
            else:
                print(f"❌ 予期せぬAPIエラーが発生しました: {e}")
                print("プログラムを異常終了します。")
                sys.exit(1)

    # 5. JSONパース・個別ファイル書き出し
    try:
        json_data = parse_and_fix_json(raw_text)
        output_file = f"video_{video_id}.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)
        print(f"✅ 『{output_file}』を出力しました。")
    except Exception as e:
        print(f"❌ JSONの出力に失敗しました: {e}")
        continue

    # 6. videos.json の登録・更新処理
    gemini_members = [f"#{m.replace('❓', '')}" for m in json_data.get("members", [])]
    raw_keywords = list(dict.fromkeys(auto_keywords + gemini_members))
    filtered_keywords = [tag for tag in raw_keywords if tag in ALLOWED_TAGS]

    video_entry = {
        "id": video_id,
        "title": json_data.get("title", title),
        "file": output_file,
        "keywords": filtered_keywords
    }

    if os.path.exists(videos_file):
        try:
            with open(videos_file, "r", encoding="utf-8") as f:
                videos_list = json.load(f)
        except json.JSONDecodeError:
            videos_list = []
    else:
        videos_list = []

    # 既存のリストを更新、無ければ追加
    is_updated = False
    for item in videos_list:
        if item.get("id") == video_id:
            item["title"] = video_entry["title"]
            item["file"] = output_file
            merged_raw = list(dict.fromkeys(item.get("keywords", []) + filtered_keywords))
            item["keywords"] = [tag for tag in merged_raw if tag in ALLOWED_TAGS]
            is_updated = True
            break

    if not is_updated:
        videos_list.append(video_entry)

    with open(videos_file, "w", encoding="utf-8") as f:
        json.dump(videos_list, f, ensure_ascii=False, indent=2)

    # 次の動画のスキップ判定用セットも更新
    existing_ids.add(video_id)
    print(f"✅ 『{videos_file}』を更新完了しました！（ID: {video_id}）")

print("\n🎉 すべてのURLの処理が完了しました！")