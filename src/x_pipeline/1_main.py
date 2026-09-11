import json
import os
import re
import sys
import time
import subprocess
from dotenv import load_dotenv
from google import genai

# ルート直下の .env を読み込み
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))

# パス設定
X_POSTS_DIR = os.path.join(BASE_DIR, "data", "x_posts")
ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")
STATUS_FILE_PATH = os.path.join(BASE_DIR, "data", "status_management.json")

os.makedirs(X_POSTS_DIR, exist_ok=True)

# ==========================================
# 埋め込み用テキスト（元 prompt.txt / translation_rules.txt）
# ==========================================

TRANSLATION_RULES = """【トーン指定】
・日本語訳の全体のトーンは「{target_tone}」で翻訳してください。

【グループおよびメンバー判別ガイドライン】
・グループ名: PERSES（読み方はパーセス） / ファンダム名: PIECES（読み方はピーセス）／所属会社: GNEST（読み方はジーネスト）
・画面内に映っている人物だけでなく、カメラマンや画面外から声のみ聞こえる発言者（カメラに映っていない人）も声の質ややり取りから人物を識別してください。
・発言者の識別ラベルには絵文字を使わず、指定されたアルファベット（JUNG, NAY, KRITTIN, PALM, PLUGGY, STAFF, GUEST, ALL 等）を使用してください。
{off_screen_instruction}

■ メンバー共通ルール:
・一人称は全メンバー共通で「僕」または「僕たち」に統一してください（「俺」「私」等は使用しない）。
・自分の名前を一人称にする呼び方（例：「プラッギーは〜」）：
 本人が愛嬌を出している・甘えているニュアンスを殺さず、そのまま「僕」ではなく「〇〇は〜」と訳すか、かわいらしい口調に落とし込んでください。

■ メンバー識別・呼称・キャラクター設定:
1. 🦥 ジャン（JUNG）
   ・見た目: 鼻が大きめ、たれ目、濃く太い眉。右頬に薄いホクロ。胸板ががっちりした筋肉質。笑うとえくぼが出ます。
   ・愛称/呼称: ピジャン
   ・キャラ/口調: 最年長・リーダー。明るく優しいお兄さん口調。基本はフランク。メンバー同士では敬語なしだが、周りに配慮したりリーダーとしてまとめ・解説をしたりする場面では丁寧語が混ざる。
   ・【固有ルール・口癖（ครับผม）】:
     「ครับผม（クラップポム）」を早口で言う口癖があるため、ジャンの発音に合わせて「カポン〜」「カッポンッ」「カーポン」などの愛嬌のあるカタカナ表記にし、カッコ訳を添えてください。（出力例：「カポン〜（了解〜）」「カッポンッ（はい！）」）

2. 🐒 ネー（NAY）
   ・見た目: 一重に見える奥二重、細長い鼻の穴、ややシャくれ気味の輪郭。上唇キワ・人中左・左頬・左首に複数のホクロ。高身長モデル体型で小顔・肩幅広・腕長。
   ・愛称/呼称: ピネー
   ・キャラ/口調: 落ち着き担当。クールで穏やか。語尾に「！」（感嘆符）は絶対に使用しない（例: 「〜〜だね」「〜〜かな」）。

3. 🦈 クリッティン（KRITTIN）
   ・見た目: 眉間が広く離れ目、厚い大きな唇（口を大きく開けて喋る）。右眉と左鼻にピアス。顔にホクロ多数。メンバー唯一タトゥーあり。笑うと目が細くなる。
   ・愛称/呼称: クリット
   ・キャラ/口調: ムードメーカー。基本の言葉遣いはフランクなタメ口と丁寧語（「〜〜ですよ」「〜〜ですね」）が半々程度。ギャルっぽい高テンションな口調を使う。
   ・語尾表現: テンションが高くフレンドリーな語尾（例：「〜〜じゃん！」「〜〜すぎ！」「〜〜なんだけど！」など）を使用してください。
   ・【「〜だし！」の使用制限】理由・主張・言い訳などを表す自然な文脈（OK例：「そんなの僕の勝手だし！」）では使用可能ですが、単体で文末を補う目的の脈絡のない使用（NG例：「カメラマンだし！」のように「〜です/〜だよ」の代用で使うこと）は厳禁とします。
   ・【絶対遵守】発言の7割程度に「！」をつけるテンション感（多用しすぎ・「！！」などの連打は禁止）。「〜っす」は絶対に使用しない。年下メンバー（パーム、プラッギー）に対しては敬語を使わずフランクなタメ口。

4. 🐶 パーム（PALM）
   ・見た目: 5人で最も肌トーンが暗め。山のある角度のついた眉。丸い鼻の穴、長めの人中、二重たれ目。顔にホクロ無し（左首に1つ）。がっちり水球体型。
   ・愛称/呼称: ノンパーム、トンパーム
   ・キャラ/口調: 年下組。プラッギー以外の年上（ジャン、ネー、クリティン）には基本的に敬語（〜です/〜ます）を使う（テンションが上がるとたまにタメ口が混ざる）。プラッギーに対しては完全タメ口。

5. 🐱 プラッギー（PLUGGY）
   ・見た目: 中性的で女性的な顔立ち（メイク多め）。明るい眉、大きめの鼻で鼻下が平行。右目下に泣きぼくろ、左頬にホクロ。5人で最も細身。
   ・愛称/呼称: ギー、ノンギー、ギーギー
   ・キャラ/口調: グループ最年少（末っ子）。誰に対しても一切遠慮がない。メンバー全員に対して完全タメ口（敬語一切なし）。おっとりした口調でたまに言葉を伸ばす（「チャ～イ」「だよね～」など）。ただし「〜」の多用は避け、自然な範囲（語尾の1〜2箇所程度）にとどめてください。メンバー以外には必ず敬語を使う。

■ メンバー以外の発言者（スタッフ・カメラマン等）の扱い:
・声の主がPERSESメンバー5人以外（スタッフや外部の人など）だと判断された場合は、`🎥: 発言内容` のように絵文字表記にし、日本語訳は丁寧な敬語（〜です / 〜ます）で出力してください。
・カメラマン・撮影スタッフ・天の声： 🎥: 発言内容
・進行役・MC・外部ゲストなど： 🎤: 発言内容
・その他不特定のスタッフ等： 🎬: 発言内容
※特に区別が不要な場合は、すべて 🎥: 発言内容 に統一して構いません。

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

【敬称・感情・発声表現ルール（最重要）】
・【日本語訳】側では、タイ語文字（เอ้ย, เฮ้ย, อุ้ย, อู้ว など）を出力することを【絶対厳禁】とします。
・リアクションや感嘆詞は【カタカナ音声】に変換し、シンプルに1回のみ出力してください（同じ感嘆詞の連続や重複は禁止。例: 「ヘーイ！ヘーイ！」→「ヘーイ！」に統一）。
また、感嘆符「！」の多用を避け、原則1発言につき1つまでにとどめてください（※個別に指定のあるネー［！使用禁止］、クリッティン［！多め］のルールを優先）。
・【タイ語原文】側にはカッコ書きの翻訳は付けず、純粋なタイ語・音のみを出力してください。
・笑いの表現は「555」を使用してください。
・文末に「クラップ/カー（ครับ/ค่ะ）」がついている発言や、丁寧な単語（ทาน, คุณなど）が使われている場合は、日本語でも「〜です」「〜ます」「〜ですね」「〜でしょうか？」といった丁寧語・敬語表現を維持してください。
・基本はタメ口で話している途中に急に文末詞をつけて「敬語」に戻った場合（照れ、皮肉、改まった雰囲気など）は、その落差（ギャップ）が日本語テロップでも伝わるように表現してください。

{extra_prompt}"""

BASE_PROMPT = """添付された動画の【画面に映っているタイ語テロップ】と【話している音声（発言）】の両方を解析し、以下の指定JSONフォーマットに従って直接JSON形式で出力してください。

【タイトル・要約・キャプションの生成ルール】
・title: 動画全体の内容を表すキャッチーな視聴者目線の感想、または短い要約フレーズ（1文）。
・summary: 会話やテロップの重要なハイライトや全体の流れが短時間で把握できるように簡潔にまとめた文章。
・caption: 画面上のテロップ（画面表示テキスト）の日本語訳およびタイ語原文。テロップがない場合は空文字 "" としてください。

【話者（speaker）の記述ルール（絶対遵守）】
・絵文字は使用せず、必ず以下の【英大文字のアルファベット表記】で統一してください。
  - JUNG : ジャン
  - NAY : ネー
  - KRITTIN : クリッティン
  - PALM : パーム
  - PLUGGY : プラッギー
  - ALL : メンバー全員の掛け声など
  - STAFF : 撮影スタッフ・カメラマン・天の声など
  - GUEST : メンバー以外の外部ゲストや共演者など
  - UNKNOWN : 識別不能な人物

【テロップ・音声翻訳のルール】
{translation_rules}

【出力JSONフォーマット】
以下のキー構造を厳密に維持して出力してください。
{
  "title": {
    "ja": "キャッチーな日本語タイトル"
  },
  "summary": {
    "ja": "日本語要約文"
  },
  "caption": {
    "th": "テロップのタイ語原文（箇条書き改行など）",
    "ja": "テロップの日本語訳（箇条書き改行など）"
  },
  "transcript": [
    {
      "start": 0,
      "end": 2,
      "speaker": "アルファベット表記（例: PALM, GUEST, STAFF等）",
      "text": "話しているタイ語原文",
      "pronunciation_kana": "タイ語発音のカタカナ表記。区切りは中点・";
      "pronunciation_roman": "タイ語発音のローマ字表記。区切りは半角スペース",
      "translations": {
        "ja": "発言の日本語訳"
      }
    }
  ]
}
"""


# ==========================================
# APIキー管理クラス
# ==========================================
class KeyManager:
    def __init__(self):
        raw_keys = os.getenv("GEMINI_API_KEYS", "")
        if not raw_keys.strip():
            single_key = os.getenv("GEMINI_API_KEY", "")
            if single_key.strip():
                raw_keys = single_key

        if raw_keys:
            self.api_keys = [k.strip() for k in raw_keys.replace(",", "\n").splitlines() if k.strip()]
        else:
            self.api_keys = []

        if not self.api_keys:
            print("❌ エラー: .env に GEMINI_API_KEYS が設定されていません。")
            sys.exit(1)

        print(f"🔑 合計 {len(self.api_keys)} 個の API キーを読み込みました。")
        self.current_index = 0

    def get_client(self) -> genai.Client:
        key = self.api_keys[self.current_index]
        print(f"🔑 APIキーを使用中 (Index: {self.current_index})")
        return genai.Client(api_key=key)

    def switch_to_next_key(self) -> genai.Client:
        if len(self.api_keys) <= 1:
            print("⚠️ 利用可能な他のAPIキーがありません。60秒待機後に再試行します...")
            time.sleep(60)
            return self.get_client()

        self.current_index = (self.current_index + 1) % len(self.api_keys)
        print(f"🔄 APIキーを Index {self.current_index} に切り替えて再試行します...")
        time.sleep(3)
        return self.get_client()


# ファイルパス設定
URLS_FILE_PATH = os.path.join(os.path.dirname(__file__), "urls.txt")

# 基本設定（ほとんど変更しない項目）
CONFIG = {
    "target_tone": "標準・明るめ",
    "custom_instruction": "",
    "member_photos": {
        "JUNG": os.path.join(ASSETS_DIR, "jung.jpg"),
        "NAY": os.path.join(ASSETS_DIR, "nay.jpg"),
        "KRITTIN": os.path.join(ASSETS_DIR, "krittin.jpg"),
        "PALM": os.path.join(ASSETS_DIR, "palm.jpg"),
        "PLUGGY": os.path.join(ASSETS_DIR, "pluggy.jpg"),
    },
    "gemini_model": "gemini-3.5-flash",
}

def load_urls_from_file() -> list:
    """urls.txt から有効な URL リストを取得（空行・コメント行を除外）"""
    if not os.path.exists(URLS_FILE_PATH):
        print(f"⚠️ {URLS_FILE_PATH} が見つかりません。")
        return []
    
    urls = []
    with open(URLS_FILE_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            # 空行や `#` で始まる行（コメント）は無視
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def extract_status_id(url: str) -> str:
    match = re.search(r"status/(\d+)", url)
    return match.group(1) if match else str(int(time.time()))


def download_x_video(url: str, output_path: str) -> str | None:
    if os.path.exists(output_path):
        os.remove(output_path)

    cmd = f'yt-dlp --no-warnings --no-check-certificate -f "b[ext=mp4]/best" -o "{output_path}" "{url}"'
    os.system(cmd)

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return None
    return output_path


def get_x_post_info(url: str) -> dict:
    """yt-dlp を使用して X 投稿のメタデータ（投稿日時等）を取得する"""
    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--no-check-certificate",
        "--dump-json",
        url
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        info = json.loads(res.stdout)
        
        # timestamp (UNIX時間) から YYYY-MM-DD HH:MM:SS 形式の文字列を作成
        published_at = ""
        if "timestamp" in info and info["timestamp"]:
            published_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(info["timestamp"]))
        elif "upload_date" in info and info["upload_date"]:
            # YYYYMMDD 形式の場合
            d = info["upload_date"]
            published_at = f"{d[:4]}-{d[4:6]}-{d[6:8]}"

        return {"published_at": published_at}
    except Exception as e:
        print(f"⚠️ メタデータの取得に失敗しました: {e}")
        return {"published_at": ""}


def parse_subtitles_to_lines(
    speech_th: str, speech_ja: str, speech_th_romanized: str = ""
) -> list:
    th_lines = [l.strip() for l in speech_th.split("\n") if l.strip()]
    ja_lines = [l.strip() for l in speech_ja.split("\n") if l.strip()]
    rom_lines = [
        l.strip() for l in speech_th_romanized.split("\n") if l.strip()
    ]

    transcript = []
    max_len = max(len(th_lines), len(ja_lines))

    for i in range(max_len):
        th_l = th_lines[i] if i < len(th_lines) else ""
        ja_l = ja_lines[i] if i < len(ja_lines) else ""
        rom_l = rom_lines[i] if i < len(rom_lines) else ""

        spk, th_txt = (
            th_l.split("：", 1)
            if "：" in th_l
            else (th_l.split(":", 1) if ":" in th_l else ("", th_l))
        )
        _, ja_txt = (
            ja_l.split("：", 1)
            if "：" in ja_l
            else (ja_l.split(":", 1) if ":" in ja_l else ("", ja_l))
        )

        rom_clean = re.sub(r'^[^\w\s]*\s*[\w\d_]+:\s*', '', rom_l.strip())
        rom_clean = re.sub(r'^[^\w\s]+:\s*', '', rom_clean)

        transcript.append(
            {
                "id": i + 1,
                "start": 0,
                "end": 0,
                "speaker": spk.strip(),
                "text": th_txt.strip(),
                "pronunciation_kana": "",
                "pronunciation_roman": rom_clean,
                "translations": {
                    "ja": ja_txt.strip()
                }
            }
        )
    return transcript


def translate_text_fields_with_gemini(
    key_mgr: KeyManager,
    data: dict,
    target_langs: list = ["en", "ko", "zh-TW", "id", "pt"]
) -> dict:
    """
    1回目の解析結果データを受け取り、多言語翻訳を行って構造化データを完成させる。
    ・title / summary: ja から翻訳
    ・caption: ja(日本語テロップ) または th(タイ語テロップ) から翻訳
    ・transcript.translations: text (タイ語原文) から各言語へ翻訳
    """
    client = key_mgr.get_client()

    prompt = f"""
以下のJSONデータに含まれるテキストを、指定の言語 {target_langs} に翻訳し、指定されたキー構造に従ってJSONで返却してください。

【翻訳指示】
1. "title": "ja" のテキストを翻訳して {target_langs} の各言語キー（例: "en", "ko", "zh-TW", "id", "pt"）を追加してください。
2. "summary": "ja" のテキストを翻訳して {target_langs} の各言語キーを追加してください。
3. "caption": "ja" または "th" の内容を翻訳して {target_langs} の各言語キーを追加してください（※空文字の場合は空文字のまま）。
4. "transcript": 各配列要素の "text"（タイ語原文）を元に、"translations" オブジェクト内に {target_langs} の各言語の翻訳文を追加してください。

【入力データ】
{json.dumps(data, ensure_ascii=False, indent=2)}

【出力JSONフォーマット】
入力データの構造を維持しつつ、translations や caption、title、summary 内に指定言語のキーを補完した完全なJSONを出力してください。
"""
    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents=[prompt],
            config={"response_mime_type": "application/json"}
        )
        translated_res = json.loads(response.text.strip())
        return translated_res
    except Exception as e:
        print(f"⚠️ 多言語翻訳ステップでエラーが発生しました (Error: {e})。元のデータをそのまま使用します。")
        return data


def analyze_video_with_gemini(
    key_mgr: KeyManager,
    video_path: str,
    base_prompt: str,
    translation_rules: str,
    member_photo_paths: dict,
    config: dict,
    off_screen_speaker: str = "",
) -> dict:
    client = key_mgr.get_client()

    print("📸 メンバー参照画像をアップロード中...")
    uploaded_photos = []
    photo_instruction = "\n\n【参考：メンバーの顔画像データ】\n"

    for name, photo_path in member_photo_paths.items():
        if os.path.exists(photo_path):
            try:
                img_file = client.files.upload(file=photo_path)
                uploaded_photos.append(img_file)
                photo_instruction += f"・添付した画像 {img_file.name} は {name} の顔写真です。\n"
                print(f"  └ ✅ {name} 読み込み成功")
            except Exception as e:
                print(f"  └ ⚠️ {name} 読み込み失敗: {e}")

    print("📹 動画ファイルを Gemini へアップロード中...")
    video_file = client.files.upload(file=video_path)

    while video_file.state.name == "PROCESSING":
        print(".", end="", flush=True)
        time.sleep(2)
        video_file = client.files.get(name=video_file.name)

    if video_file.state.name == "FAILED":
        raise RuntimeError("Gemini での動画処理に失敗しました。")

    extra_prompt = ""
    if config["custom_instruction"].strip():
        extra_prompt = f"【追加指示】\n・{config['custom_instruction'].strip()}\n"

    off_screen_instruction = ""
    if off_screen_speaker.strip():
        off_screen_instruction = (
            f"今回の動画でカメラ外で喋ってる人は、{off_screen_speaker.strip()}です。"
        )

    prompt_text = base_prompt.replace("{translation_rules}", translation_rules)
    prompt_text = (
        prompt_text.replace("{target_tone}", config["target_tone"])
        .replace("{extra_prompt}", extra_prompt)
        .replace("{off_screen_instruction}", off_screen_instruction)
    )

    final_prompt = prompt_text + photo_instruction
    contents_input = [video_file] + uploaded_photos + [final_prompt]

    raw_output = ""
    max_retries = 5

    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=config["gemini_model"],
                contents=contents_input,
                config={"response_mime_type": "application/json"}
            )
            raw_output = response.text.strip()
            break
        except Exception as e:
            err_msg = str(e)
            is_429 = "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg
            is_503 = "503" in err_msg or "UNAVAILABLE" in err_msg

            if is_429:
                print(f"\n⚠️ 制限検知 (429 / RESOURCE_EXHAUSTED)。次のキーへ切替えます...")
                client = key_mgr.switch_to_next_key()
            elif is_503:
                print(f"\n⚠️ サーバー混雑中 (503)。7秒待機後に再試行 ({attempt}/{max_retries})...")
                time.sleep(7)
            else:
                print(f"\n❌ APIエラーが発生しました: {e}")
                time.sleep(5)

    try:
        client.files.delete(name=video_file.name)
        for img_file in uploaded_photos:
            client.files.delete(name=img_file.name)
    except Exception:
        pass

    # JSONレスポンスを直接ロード
    result_data = json.loads(raw_output)

    # transcript 内に id を自動採番して追加（Gemini側の出力揺れ防止）
    if "transcript" in result_data:
        for idx, item in enumerate(result_data["transcript"]):
            item["id"] = idx + 1
            item.setdefault("start", 0)
            item.setdefault("end", 0)
            item.setdefault("pronunciation_kana", "")
            item.setdefault("pronunciation_roman", "")
    return result_data


def update_status_management_file(item_data: dict, json_filename: str):
    """
    既存のステータス管理JSONを読み込み、新規または更新データを反映して保存する。
    """
    status_list = []
    if os.path.exists(STATUS_FILE_PATH):
        try:
            with open(STATUS_FILE_PATH, "r", encoding="utf-8") as f:
                status_list = json.load(f)
        except Exception:
            status_list = []

    status_entry = {
        "id": item_data.get("id", ""),
        "summary": item_data.get("summary", {}),
        "published_at": item_data.get("published_at", ""),
        "file": json_filename,
        "keywords": []
    }

    # 同一IDが既存にあれば更新、なければ末尾に追加
    updated = False
    for i, entry in enumerate(status_list):
        if entry.get("id") == status_entry["id"]:
            status_list[i] = status_entry
            updated = True
            break

    if not updated:
        status_list.append(status_entry)

    with open(STATUS_FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(status_list, f, ensure_ascii=False, indent=2)

    print(f"📊 ステータス管理ファイルを更新しました: `data/status_management.json`")


def main():
    urls = load_urls_from_file()
    if not urls:
        print("❌ エラー: urls.txt に処理対象の URL が記載されていません。")
        return

    base_prompt = BASE_PROMPT
    translation_rules = TRANSLATION_RULES
    key_mgr = KeyManager()
    cfg = CONFIG

    for idx, video_url in enumerate(urls):
        status_id = extract_status_id(video_url)

        # 個別JSONファイルのパス
        json_filename = f"x_{status_id}.json"
        json_output_path = os.path.join(X_POSTS_DIR, json_filename)

        existing_data = None
        needs_video_analysis = True
        needs_multilingual = True

        # 既存ファイルがあるか確認
        if os.path.exists(json_output_path):
            try:
                with open(json_output_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
                
                # 動画解析はすでに完了している
                needs_video_analysis = False
                
                # 多言語（例: en）が存在するかチェック
                title_langs = existing_data.get("title", {})
                if "en" in title_langs:
                    needs_multilingual = False

            except Exception:
                # ファイルが壊れている等の場合は最初からやり直し
                needs_video_analysis = True

        # 完全完了（動画解析も多言語翻訳も終わっている）ならスキップ
        if not needs_video_analysis and not needs_multilingual:
            print(f"⏭️ スキップ [{idx + 1}/{len(urls)}]: status_id({status_id}) は多言語翻訳まで完了済みです。")
            continue

        video_path = os.path.join(
            os.path.dirname(__file__), f"temp_video_{status_id}.mp4"
        )

        try:
            # 1. 動画解析ステップ（未完了の場合のみ実行）
            if needs_video_analysis:
                print(f"\n🎬 処理中 [{idx + 1}/{len(urls)}]: {video_url}")
                
                post_info = get_x_post_info(video_url)
                item_published_at = post_info.get("published_at", "")

                downloaded_file = download_x_video(video_url, video_path)
                if not downloaded_file:
                    print("❌ 動画のダウンロードに失敗しました。")
                    continue

                print("🤖 動画から日本語・タイ語データを解析中...")
                parsed_data = analyze_video_with_gemini(
                    key_mgr=key_mgr,
                    video_path=downloaded_file,
                    base_prompt=base_prompt,
                    translation_rules=translation_rules,
                    member_photo_paths=cfg["member_photos"],
                    config=cfg,
                    off_screen_speaker="",
                )
                
                # 中間データ（動画解析結果）の作成
                current_output = {
                    "id": status_id,
                    "x_url": video_url,
                    "published_at": item_published_at,
                    "title": parsed_data.get("title", {}),
                    "summary": parsed_data.get("summary", {}),
                    "caption": parsed_data.get("caption", {}),
                    "transcript": parsed_data.get("transcript", [])
                }
                
                # ひとまず日本語/タイ語のみで中間保存しておく
                with open(json_output_path, "w", encoding="utf-8") as f:
                    json.dump(current_output, f, ensure_ascii=False, indent=2)
            else:
                print(f"\n🔄 多言語翻訳の再実行 [{idx + 1}/{len(urls)}]: status_id({status_id}) の既存データを読み込みました。")
                current_output = existing_data

            # 2. 多言語翻訳ステップ（未完了の場合のみ実行）
            if needs_multilingual:
                print("🌐 多言語 (en, ko, zh-TW, id, pt) への翻訳を実行中...")
                full_data = translate_text_fields_with_gemini(
                    key_mgr=key_mgr,
                    data=current_output,
                    target_langs=["en", "ko", "zh-TW", "id", "pt"]
                )

                # 最終データ（多言語込み）に更新して上書き保存
                final_output = {
                    "id": status_id,
                    "x_url": current_output.get("x_url", video_url),
                    "published_at": current_output.get("published_at", ""),
                    "title": full_data.get("title", {}),
                    "summary": full_data.get("summary", {}),
                    "caption": full_data.get("caption", {}),
                    "transcript": full_data.get("transcript", [])
                }

                with open(json_output_path, "w", encoding="utf-8") as f:
                    json.dump(final_output, f, ensure_ascii=False, indent=2)

                update_status_management_file(final_output, json_filename)
                print(f"✅ 多言語解析完了！データ保存先: `data/x_posts/x_{status_id}.json`")

        finally:
            if os.path.exists(video_path):
                os.remove(video_path)


if __name__ == "__main__":
    main()