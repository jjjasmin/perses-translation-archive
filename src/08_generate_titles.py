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
STATUS_FILE_PATH = '../pipeline_status.json'

MODEL_NAME = "gemini-3.5-flash-lite"
TARGET_LANGS = ["ja", "en", "ko", "zh-TW", "id", "pt"]

# 1回のAPIリクエストでまとめて翻訳する件数（類似タイトル同士を並べて処理）
BATCH_SIZE = 5

# ★ ひとまず全件やり直すため True に設定（完了後は False に変更してください）
FORCE_REGENERATE = True


# === 曲名・番組名の統一対応表 ===
# 形式:
# {
#     "keywords": [原題に含まれる検索キーワード（大文字小文字区別なし）],
#     "ja": "日本語タイトル用の統一表記（備考を除く全部）",
#     "other": "日本語以外（en, ko, zh-TW, id, pt）用の統一表記（英語＋タイ語アルファベット表記）"
# }
# ※新しい曲や番組を追加したい場合は、このリストに同じ形式で追記してください。
SONG_AND_SHOW_RULES = [
    {
        "keywords": ["MY TIME"],
        "ja": "MY TIME",
        "other": "MY TIME",
    },
    {
        "keywords": ["TOUCHDOWN", "ใกล้ดาว"],
        "ja": "TOUCHDOWN (ใกล้ดาว) | クライ・ダーウ | 星の近く",
        "other": "TOUCHDOWN (ใกล้ดาว) | klai dao",
    },
    {
        "keywords": ["Catch the Night"],
        "ja": "Catch the Night",
        "other": "Catch the Night",
    },
    {
        "keywords": ["Cuteless", "น่ารักน้อยลงหน่อย"],
        "ja": "Cuteless (น่ารักน้อยลงหน่อย) | ナーラック・ノーイ・ロン・ノイ | 可愛いすぎて困る",
        "other": "Cuteless (น่ารักน้อยลงหน่อย) | naarak noi long noi",
    },
    {
        "keywords": ["Beautiful"],
        "ja": "Beautiful",
        "other": "Beautiful",
    },
    {
        "keywords": ["Far Too Close", "คนใกล้ที่คิดไกล"],
        "ja": "Far Too Close (คนใกล้ที่คิดไกล) | コン・クライ・ティー・キット・クライ | 近づくほど遠くなる",
        "other": "Far Too Close (คนใกล้ที่คิดไกล) | khon klai thi khit klai",
    },
    {
        "keywords": ["Prompt Play", "เล่นใจป้ะ"],
        "ja": "Prompt Play (เล่นใจป้ะ?) | レン・チャイ・パ？ | 本気出して遊ばない？",
        "other": "Prompt Play (เล่นใจป้ะ?) | len chai pa?",
    },
    {
        "keywords": ["Spoil!"],
        "ja": "Spoil! | 甘やかして！",
        "other": "Spoil!",
    },
    {
        "keywords": ["BODYGUARD"],
        "ja": "BODYGUARD",
        "other": "BODYGUARD",
    },
    {
        "keywords": ["OVER", "อย่าฝืน"],
        "ja": "OVER (อย่าฝืน) | ヤー・フーン | 無理しないで",
        "other": "OVER (อย่าฝืน) | ya fuen",
    },
    {
        "keywords": ["ONE SHOT"],
        "ja": "ONE SHOT (feat. PiXXiE)",
        "other": "ONE SHOT (feat. PiXXiE)",
    },
    {
        "keywords": ["Comment", "เม้นหยอกๆ"],
        "ja": "Comment (เม้นหยอกๆ) | メン・ヨックヨック | 冗談めいたコメント",
        "other": "Comment (เม้นหยอกๆ) | men yok yok",
    },
    {
        "keywords": ["FREEZE", "ห้ามขยับจับนะ"],
        "ja": "FREEZE (ห้ามขยับจับนะ) | ハーム・カヤップ・チャップ・ナ | 動かないで、逮捕するよ",
        "other": "FREEZE (ห้ามขยับจับนะ) | ham khayap chap na",
    },
    {
        "keywords": ["KITTY SWAG"],
        "ja": "KITTY SWAG",
        "other": "KITTY SWAG",
    },
    {
        "keywords": ["OUR TIME", "อยู่คนเดียวยังไงไหว"],
        "ja": "OUR TIME (อยู่คนเดียวยังไงไหว) | ユー・コンディァウ・ヤンガイ・ワイ | 一人きりなんて耐えられないよ",
        "other": "OUR TIME (อยู่คนเดียวยังไงไหว) | yu khon diao yangngai wai",
    },
    {
        "keywords": ["TURN ME ON"],
        "ja": "TURN ME ON (feat. Tobii) | 夢中にさせて",
        "other": "TURN ME ON (feat. Tobii)",
    },
    {
        "keywords": ["Hotter than ur X", "ไม่แพ้ใคร"],
        "ja": "Hotter than ur X (ไม่แพ้ใคร) | マイ・ペー・クライ | 誰にも負けない（元カレにも！）",
        "other": "Hotter than ur X (ไม่แพ้ใคร) | mai phae khrai",
    },
    {
        "keywords": ["FLY", "JUNG x F.HERO"],
        "ja": "FLY - JUNG x F.HERO",
        "other": "FLY - JUNG x F.HERO",
    },
    {
        "keywords": ["YES ME, BABY", "คืนนี้อยากได้กี่ครั้ง"],
        "ja": "YES ME, BABY (คืนนี้อยากได้กี่ครั้ง) - PALM x Hannah 4EVE | クーン・ニー・ヤーク・ダイ・キー・クラング | 今夜は何回ほしい？",
        "other": "YES ME, BABY (คืนนี้อยากได้กี่ครั้ง) - PALM x Hannah 4EVE | khuen ni yak dai ki khrang",
    },
    {
        "keywords": ["ยกมือขึ้น"],
        "ja": "ยกมือขึ้น - NAY x TangBadVoice | ヨック・ムー・クン | 手を挙げて",
        "other": "ยกมือขึ้น - NAY x TangBadVoice | yok mue khuen",
    },
    {
        "keywords": ["NO PAIN NO GAIN"],
        "ja": "NO PAIN NO GAIN (Ost. Mr.Fanboy)",
        "other": "NO PAIN NO GAIN (Ost. Mr.Fanboy)",
    },
    {
        "keywords": ["Free WiFi"],
        "ja": "Free WiFi (feat. Hi Arpaporn)",
        "other": "Free WiFi (feat. Hi Arpaporn)",
    },
    {
        "keywords": ["Wanna Taste", "ใจละลาย"],
        "ja": "Wanna Taste (ใจละลาย) - PLUGGY x JAY OF DICE | ジャイ・ララーイ | 心がとろけちゃう",
        "other": "Wanna Taste (ใจละลาย) - PLUGGY x JAY OF DICE | chai lalai",
    },
    {
        "keywords": ["Mashup song CYCLE", "การเริ่มต้นใหม่ ความรัก และการจากลา"],
        "ja": "Mashup song CYCLE (การเริ่มต้นใหม่ ความรัก และการจากลา) - KRITTIN | カーン・レーム・トン・マイ、クワーム・ラック、レ・カーン・チャーク・ラー | 新しい始まり、愛、そして別れ",
        "other": "Mashup song CYCLE (การเริ่มต้นใหม่ ความรัก และการจากลา) - KRITTIN | kan roem ton mai khwam rak lae kan chak la",
    },
    {
        "keywords": ["บางกรอบ"],
        "ja": "บางกรอบ (POTATO feat. PERSES) | バンクローブ | 薄切りクリスプ",
        "other": "บางกรอบ (POTATO feat. PERSES) | bang krop",
    },

    # === 今回のリスト内に含まれていた未登録の楽曲・コラボ曲 ===
    {
        "keywords": ["Colorblind"],
        "ja": "Colorblind (JUNG PERSES Ft. GENIEPAK) (OST.iQIYI Original Don't Be Too Emotional)",
        "other": "Colorblind (JUNG PERSES Ft. GENIEPAK) ",
    },
    {
        "keywords": ["WAKE UP, DON’T WAIT", "WAKE UP, DON'T WAIT", "ไปกันต่อ"],
        "ja": "WAKE UP, DON'T WAIT (ไปกันต่อ) | パイ・カン・トー | さあ、先へ行こう",
        "other": "WAKE UP, DON'T WAIT (ไปกันต่อ) | pai kan to",
    },
    {
        "keywords": ["BOUNCE"],
        "ja": "BOUNCE (GMM GRAMMY 40周年記念企画『ซนซน (ソンソン／Playful)』)",
        "other": "BOUNCE (ซนซน 40 ปี GMM GRAMMY) | ZonZon (Playful) / Naughty 40 Years of GMM GRAMMY",
    },
    {
        "keywords": ["Safe Zone"],
        "ja": "Safe Zone (มีเธอมีฉัน) | ミー・トゥー・ミー・チャン | 君がいて僕がいる-GMMグラミー オールスターズ",
        "other": "Safe Zone (มีเธอมีฉัน) | mee thoe mee chan (Safe Zone) - ruam sinlapin GMM Grammy",
    },
    ## === 公式コンテンツ・オリジナル番組名 === --- 番組名などの追加例（必要に応じて追記してください） ---
    {
        "keywords": ["HEART at TRACK"],
        "ja": "HEART at TRACK",
        "other": "HEART at TRACK",
    },
    {
        "keywords": ["PERSES'S ARCHIVE", "PERSES ARCHIVE"],
        "ja": "PERSES'S ARCHIVE",
        "other": "PERSES'S ARCHIVE",
    },
    {
        "keywords": ["GNEST PLAYROOM", "G'NEST PLAYROOM", "PLAYROOM"],
        "ja": "G'NEST PLAYROOM",
        "other": "G'NEST PLAYROOM",
    },
    {
        "keywords": ["Research Day"],
        "ja": "Research Day",
        "other": "Research Day",
    },
    {
        "keywords": ["Artist of the Month"],
        "ja": "Artist of the Month",
        "other": "Artist of the Month",
    },
    {
        "keywords": ["ALTER", "ALTER-ROLE", "ALTER-CLASS"],
        "ja": "ALTER-ROLE",
        "other": "ALTER-ROLE",
    },
    {
        "keywords": ["A DAY WITH PERSES", "A Day With"],
        "ja": "A DAY WITH PERSES",
        "other": "A DAY WITH PERSES",
    },
    {
        "keywords": ["G'NEST on Tour"],
        "ja": "G'NEST on Tour",
        "other": "G'NEST on Tour",
    },
    {
        "keywords": ["PERSES Recess"],
        "ja": "PERSES Recess",
        "other": "PERSES Recess",
    },
    {
        "keywords": ["PERSES X%"],
        "ja": "PERSES X%",
        "other": "PERSES X%",
    },
    {
        "keywords": ["G'NEST OFFSTAGE"],
        "ja": "G'NEST OFFSTAGE",
        "other": "G'NEST OFFSTAGE",
    },
    {
        "keywords": ["PERSES MISSION"],
        "ja": "PERSES MISSION",
        "other": "PERSES MISSION",
    },
    {
        "keywords": ["CarOke"],
        "ja": "CarOke",
        "other": "CarOke",
    },
    {
        "keywords": ["GMM KARAOKE"],
        "ja": "GMM KARAOKE",
        "other": "GMM KARAOKE",
    },

    # === コンサート・ファンコン・大型イベント ===
    {
        "keywords": ["ALTERLAND"],
        "ja": "ALTERLAND",
        "other": "ALTERLAND",
    },
    {
        "keywords": ["UNCENSORED"],
        "ja": "UNCENSORED",
        "other": "UNCENSORED",
    },
    {
        "keywords": ["HELLO BOYSSS"],
        "ja": "HELLO BOYSSS",
        "other": "HELLO BOYSSS",
    },
    {
        "keywords": ["OUR PIECES"],
        "ja": "OUR PIECES",
        "other": "OUR PIECES",
    },
    {
        "keywords": ["TMEA 2024"],
        "ja": "TMEA 2024",
        "other": "TMEA 2024",
    },
    {
        "keywords": ["AIYA BKK"],
        "ja": "LIVE AT AIYA BKK",
        "other": "LIVE AT AIYA BKK",
    },

    # === 音楽番組・アワード・バラエティ・メディア番組（英語名あり） ===
    {
        "keywords": ["T-POP STAGE SHOW Presented by PEPSI", "T-POP STAGE Presented by PEPSI", "T-POP STAGE SHOW", "T-POP STAGE"],
        "ja": "T-POP STAGE",
        "other": "T-POP STAGE",
    },
    {
        "keywords": ["Thailand Music Countdown"],
        "ja": "Thailand Music Countdown",
        "other": "Thailand Music Countdown",
    },
    {
        "keywords": ["TOTY Music Awards"],
        "ja": "TOTY Music Awards",
        "other": "TOTY Music Awards",
    },
    {
        "keywords": ["Spotify T-Pop Now Hottest of the Month"],
        "ja": "Spotify T-Pop Now Hottest of the Month",
        "other": "Spotify T-Pop Now Hottest of the Month",
    },
    {
        "keywords": ["IDOL ENERGY"],
        "ja": "IDOL ENERGY",
        "other": "IDOL ENERGY",
    },
    {
        "keywords": ["One Playground"],
        "ja": "One Playground",
        "other": "One Playground",
    },
    {
        "keywords": ["LOVE HORIZON", "มิติรักสุดขอบฟ้า"],
        "ja": "LOVE HORIZON (มิติรักสุดขอบฟ้า) | ミティ・ラック・スット・コープ・ファー",
        "other": "LOVE HORIZON (มิติรักสุดขอบฟ้า) | miti rak sut khop fa",
    },
    {
        "keywords": ["5 จุดเปลี่ยน บทเรียนหัวใจ", "Turning Points"],
        "ja": "Turning Points (5 จุดเปลี่ยน บทเรียนหัวใจ)",
        "other": "Turning Points (5 จุดเปลี่ยน บทเรียนหัวใจ)",
    },
    {
        "keywords": ["EM DISTRICT"],
        "ja": "EM DISTRICT",
        "other": "EM DISTRICT",
    },
    {
        "keywords": ["CANDY CHANNEL"],
        "ja": "CANDY CHANNEL",
        "other": "CANDY CHANNEL",
    },
    {
        "keywords": ["OHANA บ้าพลัง", "OHANA"],
        "ja": "OHANA บ้าพลัง (OHANA バー・パラン)",
        "other": "OHANA บ้าพลัง (OHANA Ba Phalang)",
    },
    {
        "keywords": ["Bua Sri is Happy"],
        "ja": "Bua Sri is Happy",
        "other": "Bua Sri is Happy",
    },
    {
        "keywords": ["legend th"],
        "ja": "LEGEND TH",
        "other": "LEGEND TH",
    },
    {
        "keywords": ["Sound Check"],
        "ja": "Sound Check",
        "other": "Sound Check",
    },
    {
        "keywords": ["Horwang Sisters"],
        "ja": "Horwang Sisters",
        "other": "Horwang Sisters",
    },
    {
        "keywords": ["Katanyu Tonight"],
        "ja": "Katanyu Tonight",
        "other": "Katanyu Tonight",
    },
    {
        "keywords": ["Viking Sing Along"],
        "ja": "Viking Sing Along",
        "other": "Viking Sing Along",
    },
    {
        "keywords": ["Praew And You"],
        "ja": "Praew And You",
        "other": "Praew And You",
    },
    {
        "keywords": ["Mellow POP Mission"],
        "ja": "Mellow POP Mission",
        "other": "Mellow POP Mission",
    },
    {
        "keywords": ["Mellow Highlight"],
        "ja": "Mellow Highlight",
        "other": "Mellow Highlight",
    },
    {
        "keywords": ["Guest who?"],
        "ja": "Guest who?",
        "other": "Guest who?",
    },
    {
        "keywords": ["RoV T-POP Tournament"],
        "ja": "RoV T-POP Tournament",
        "other": "RoV T-POP Tournament",
    },
    {
        "keywords": ["Flex Talk"],
        "ja": "Flex Talk",
        "other": "Flex Talk",
    },
    {
        "keywords": ["TMC LIVE SPECIAL"],
        "ja": "TMC LIVE SPECIAL",
        "other": "TMC LIVE SPECIAL",
    },
    {
        "keywords": ["T-POP : FEED", "T-POP: FEED"],
        "ja": "T-POP : FEED",
        "other": "T-POP : FEED",
    },
    {
        "keywords": ["NYLON THAILAND"],
        "ja": "NYLON THAILAND",
        "other": "NYLON THAILAND",
    },
    {
        "keywords": ["KAZZ NEWS UPDATE"],
        "ja": "KAZZ NEWS UPDATE",
        "other": "KAZZ NEWS UPDATE",
    },
    {
        "keywords": ["Lesson B podcast"],
        "ja": "Lesson B podcast",
        "other": "Lesson B podcast",
    },
    {
        "keywords": ["EFMFandomlive", "EFM Fandom Live"],
        "ja": "EFM Fandom Live",
        "other": "EFM Fandom Live",
    },

    # === タイ語タイトルのテレビ・Web番組名 ===
    {
        "keywords": ["คุณพระช่วย"],
        "ja": "คุณพระช่วย (クン・プラ・チュアイ)",
        "other": "คุณพระช่วย (Khun Phra Chuay)",
    },
    {
        "keywords": ["20 ยังจอย"],
        "ja": "20 ยังจอย (20 ヤン・ジョイ)",
        "other": "20 ยังจอย (20 Yang Joy)",
    },
    {
        "keywords": ["รอบวัน"],
        "ja": "รอบวัน (ロープ・ワン)",
        "other": "รอบวัน (Rop Wan)",
    },
    {
        "keywords": ["เพื่อนฉันเป็นคนดีย์"],
        "ja": "เพื่อนฉันเป็นคนดีย์ (プアン・チャン・ペン・コン・ディー)",
        "other": "เพื่อนฉันเป็นคนดีย์ (Phuean Chan Pen Khon Di)",
    },
    {
        "keywords": ["หัวท้ายตายก่อน"],
        "ja": "หัวท้ายตายก่อน (フア・ターイ・ターイ・コーン)",
        "other": "หัวท้ายตายก่อน (Hua Thai Tai Kon)",
    },
    {
        "keywords": ["รับสายผมหน่อย"],
        "ja": "รับสายผมหน่อย (ラップ・サーイ・ポム・ノーイ)",
        "other": "รับสายผมหน่อย (Rap Sai Phom Noi)",
    },
    {
        "keywords": ["อะไรก็ไม่รู้"],
        "ja": "อะไรก็ไม่รู้ (アライ・コー・マイ・ルー)",
        "other": "อะไรก็ไม่รู้ (Arai Ko Mai Ru)",
    },
    {
        "keywords": ["โต๊ะหมู่ชาบู"],
        "ja": "โต๊ะหมู่ชาบู (ト・ムー・シャーブー)",
        "other": "โต๊ะหมู่ชาบู (To Mu Shabu)",
    },
    {
        "keywords": ["ถ้าโลกนี้ไม่มี GPS"],
        "ja": "ถ้าโลกนี้ไม่มี GPS (ター・ローク・ニー・マイ・ミー GPS)",
        "other": "ถ้าโลกนี้ไม่มี GPS (Tha Lok Ni Mai Mi GPS)",
    },
    {
        "keywords": ["ข่าววันบันเทิง"],
        "ja": "ข่าววันบันเทิง (カーオ・ワン・バントゥーン)",
        "other": "ข่าววันบันเทิง (Khao Wan Banthoeng)",
    },
    {
        "keywords": ["หนูตกถังข้าวสาร"],
        "ja": "หนูตกถังข้าวสาร (ヌー・トック・タン・カーオサーン)",
        "other": "หนูตกถังข้าวสาร (Nu Tok Thang Khaosan)",
    },
    {
        "keywords": ["ทศกัณฐ์รันวงการ"],
        "ja": "ทศกัณฐ์รันวงการ (トッサカン・ラン・ウォンカーン)",
        "other": "ทศกัณฐ์รันวงการ (Thotsakan Ran Wongkan)",
    },
    {
        "keywords": ["ตู้ซ่อนหมี"],
        "ja": "ตู้ซ่อนหมี (トゥー・ソーン・ミー)",
        "other": "ตู้ซ่อนหมี (Tu Son Mi)",
    },
    {
        "keywords": ["ไอดอลตึกเจมรุ่นแ"],
        "ja": "ไอดอลตึกเจมรุ่นแรก (アイドル・トゥック・ジェム・ルン・レーク)",
        "other": "ไอดอลตึกเจมรุ่นแรก (Idol Tuek Gem Run Raek)",
    },
    {
        "keywords": ["อาตุ่ยคุ้ยPerses", "อาตุ่ยคุ้ย"],
        "ja": "อาตุ่ยคุ้ย PERSES (アー・トゥイ・クイ PERSES)",
        "other": "อาตุ่ยคุ้ย PERSES (A Tui Khui PERSES)",
    },
    {
        "keywords": ["FIVE ME"],
        "ja": "FIVE ME",
        "other": "FIVE ME",
    },
]


# === APIキーの設定とローテーション管理（改行・リテラル\n・カンマ区切りに対応） ===
api_keys_str = os.getenv("GEMINI_API_KEYS")
if not api_keys_str:
    raise ValueError("エラー: .envファイルに GEMINI_API_KEYS が設定されていません。")

normalized_keys_str = api_keys_str.replace("\\n", "\n")
API_KEYS = [k.strip(' "\'') for k in re.split(r'[,\r\n]+', normalized_keys_str) if k.strip(' "\'')]

if not API_KEYS:
    raise ValueError("エラー: 有効なAPIキーが1つも読み込めませんでした。")

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
                temperature=0.15,
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


def is_title_valid(video_obj):
    """6言語すべてのタイトルが正常に埋まっているか検証（過去のゴミデータ混入も検知）"""
    title_obj = video_obj.get("title")
    if not isinstance(title_obj, dict):
        return False
    for lang in TARGET_LANGS:
        val = title_obj.get(lang)
        if not isinstance(val, str) or not val.strip():
            return False
        # 過去のパース崩れゴミ文字列（"translation:" 等）が混入していないかチェック
        if "translation:" in val or val.strip().startswith("{"):
            return False
    return True


def extract_series_signature(original_title):
    """類似タイトルをグルーピング・検索するための特徴キー（番組名・シリーズ名・主要トークン）を抽出"""
    if not original_title:
        return ""
    # パイプ '|' の後ろに番組名や企画名が入るケースが多い（例: "... | FIVE ME EP.40"）
    parts = [p.strip() for p in re.split(r'[|｜]', original_title) if p.strip()]
    if len(parts) >= 2:
        # EP番号や日付・数字を除いた番組名ベースをキーにする
        tail = re.sub(r'\b(EP\.?|#)\s*\d+.*$', '', parts[-1], flags=re.IGNORECASE).strip()
        if tail:
            return tail.lower()

    # パイプがない場合は先頭の英数字・タイ語フレーズ（括弧や数字より前）をキーにする
    head = re.split(r'[\[\(（|｜\-–—:]', original_title)[0].strip()
    head = re.sub(r'\b(EP\.?|#)\s*\d+.*$', '', head, flags=re.IGNORECASE).strip()
    return head.lower() if head else original_title[:20].lower()


def extract_tokens(text):
    """類似度計算用の単語・フレーズ集合を抽出"""
    # 英単語（2文字以上）およびタイ語連続文字列（3文字以上）を抽出
    tokens = set(re.findall(r'[A-Za-z0-9_]{2,}|[\u0E00-\u0E7F]{3,}', text.lower()))
    # 汎用的すぎる語は除外
    stopwords = {"perses", "the", "and", "for", "with", "official", "ep"}
    return tokens - stopwords


def find_similar_translated_examples(batch_items, all_videos, exclude_ids, max_examples=4):
    """バッチ内の動画と共通の番組名・フレーズを持つ翻訳済みタイトルを参考例として抽出"""
    batch_signatures = {extract_series_signature(item["original_title"]) for item in batch_items}
    batch_signatures.discard("")
    batch_tokens = set()
    for item in batch_items:
        batch_tokens |= extract_tokens(item["original_title"])

    scored_candidates = []
    for v in all_videos:
        vid = v.get("id")
        if vid in exclude_ids:
            continue
        if not is_title_valid(v):
            continue

        orig = v.get("original_title", "")
        if not orig:
            continue

        score = 0
        sig = extract_series_signature(orig)
        if sig and sig in batch_signatures:
            score += 10

        v_tokens = extract_tokens(orig)
        overlap = len(batch_tokens & v_tokens)
        score += overlap * 2

        if score > 0:
            scored_candidates.append((score, v))

    scored_candidates.sort(key=lambda x: x[0], reverse=True)
    return [v for _, v in scored_candidates[:max_examples]]


def get_relevant_song_rules(batch_items):
    """バッチ内の原題にマッチする曲名・番組名ルールを優先抽出（全体ルールも併記）"""
    matched = []
    for rule in SONG_AND_SHOW_RULES:
        for item in batch_items:
            orig_lower = item["original_title"].lower()
            if any(kw.lower() in orig_lower for kw in rule["keywords"]):
                matched.append(rule)
                break
    return matched


def build_translation_prompt(batch_items, reference_videos):
    """6言語一括タイトル翻訳用のプロンプトを構築"""
    matched_rules = get_relevant_song_rules(batch_items)

    # 該当する曲名ルールを強調表示
    matched_rules_text = ""
    if matched_rules:
        lines = []
        for r in matched_rules:
            lines.append(
                f"  - 原題キーワード {r['keywords']} を含む場合:\n"
                f"      ja（日本語）      -> 「{r['ja']}」\n"
                f"      en/ko/zh-TW/id/pt -> 「{r['other']}」"
            )
        matched_rules_text = "【★今回の対象タイトルに該当する必須の曲名・番組名指定】\n" + "\n".join(lines) + "\n\n"

    # 全曲名ルール一覧（念のためすべて渡す）
    all_rules_lines = []
    for r in SONG_AND_SHOW_RULES:
        all_rules_lines.append(f"  - {', '.join(r['keywords'])} => ja: 「{r['ja']}」 / 他言語: 「{r['other']}」")
    all_rules_text = "\n".join(all_rules_lines)

    # 参考用の既存翻訳例（Few-shot）
    ref_text = ""
    if reference_videos:
        ref_lines = []
        for rv in reference_videos:
            t = rv["title"]
            ref_lines.append(
                f"  - 原題: {rv['original_title']}\n"
                f"    ja: {t['ja']} | en: {t['en']} | ko: {t['ko']} | zh-TW: {t['zh-TW']} | id: {t['id']} | pt: {t['pt']}"
            )
        ref_text = (
            "【参考：過去の翻訳済み類似タイトル（同じシリーズ・番組名・記号フォーマットがある場合は訳語や語順をこれに完全に揃えること）】\n"
            + "\n".join(ref_lines)
            + "\n\n"
        )

    # 今回の翻訳対象リスト
    target_lines = []
    for item in batch_items:
        target_lines.append(f'  - "{item["id"]}": "{item["original_title"]}"')
    targets_text = "\n".join(target_lines)

    prompt = f"""あなたはタイの5人組ボーイズグループ「PERSES」の多言語ファンアーカイブの翻訳者です。
以下の動画の原題（original_title）リストを、元の雰囲気・テンション・記号（|、EP.xx、[MV]など）を保ったまま、指定された6言語（"ja", "en", "ko", "zh-TW", "id", "pt"）に翻訳してください。

【絶対厳守ルール】
1. グループ名・ファンダム名:
   - すべての言語（ja含む）で必ず英語表記に統一すること（PERSES, PIECES）。
2. メンバー名の表記:
   - 日本語（"ja"）のみ、メンバー名を必ずカタカナ表記に統一すること:
     JUNG -> ジャン / NAY -> ネー / KRITTIN -> クリッティン / PALM -> パーム / PLUGGY -> プラッギー
   - 日本語以外の5言語（"en", "ko", "zh-TW", "id", "pt"）は、すべて英語大文字表記に統一すること:
     JUNG, NAY, KRITTIN, PALM, PLUGGY
3. 曲名・番組名の統一:
   - 原題に下記の対応表の曲名・番組名が含まれる場合は、翻訳や省略をせず、指定された文字列をそのまま使用すること。
   - 日本語（"ja"）には「ja」用の指定表記を、日本語以外の5言語には「他言語」用の指定表記を必ず使うこと。
4. シリーズ・カテゴリの統一感:
   - 今回のリスト内や参考例に同じ番組名・コーナー名・定型フレーズが含まれる場合、すべての動画で訳語・語順・記号スタイルを統一すること。
5. 原題の雰囲気維持:
   - 煽り文句やバラエティのノリ、絵文字や記号のニュアンスを消さず、各言語で自然な動画タイトルにすること。
6. 出力フォーマット:
   - 動画IDをキーとし、値に6言語（"ja", "en", "ko", "zh-TW", "id", "pt"）の辞書を持つ純粋なJSONオブジェクトのみを出力すること。

{matched_rules_text}【曲名・番組名 統一対応表（全リスト）】
{all_rules_text}

{ref_text}【今回翻訳する動画リスト（ID: original_title）】
{targets_text}

【出力JSONフォーマット例】
{{
  "動画ID_1": {{
    "ja": "...",
    "en": "...",
    "ko": "...",
    "zh-TW": "...",
    "id": "...",
    "pt": "..."
  }}
}}
"""
    return prompt


def main():
    print(f"🚀 多言語タイトル生成を開始します (使用モデル: {MODEL_NAME} / APIキー数: {len(API_KEYS)}個 / FORCE_REGENERATE={FORCE_REGENERATE})")
    videos = load_json(VIDEOS_JSON_PATH, [])
    if not videos:
        print(f"❌ {VIDEOS_JSON_PATH} が見つからないか空です。")
        return

    status_data = load_json(STATUS_FILE_PATH, {})
    videos_status = status_data.get("videos", {})

    def get_priority(v):
        vid = v.get("id", "")
        return videos_status.get(vid, {}).get("priority", 999999)

    # 対象動画の抽出
    target_indices = []
    skipped_count = 0
    for idx, v in enumerate(videos):
        orig = v.get("original_title", "").strip()
        if not orig:
            skipped_count += 1
            continue
        if not FORCE_REGENERATE and is_title_valid(v):
            skipped_count += 1
            continue
        target_indices.append(idx)

    if not target_indices:
        print(f"🎉 翻訳対象の動画はありませんでした（スキップ: {skipped_count} 件）。")
        return

    # 類似タイトル（同じシリーズ・カテゴリ）ごとにグループ化し、グループ内の最高priority順でソート
    series_groups = {}
    for idx in target_indices:
        sig = extract_series_signature(videos[idx]["original_title"])
        series_groups.setdefault(sig, []).append(idx)

    # 各グループ内は original_title 昇順（EP順など）に並べ、グループ同士は最小priority順に並べる
    sorted_groups = sorted(
        series_groups.values(),
        key=lambda grp: min(get_priority(videos[i]) for i in grp)
    )
    ordered_indices = []
    for grp in sorted_groups:
        grp_sorted = sorted(grp, key=lambda i: videos[i]["original_title"])
        ordered_indices.extend(grp_sorted)

    print(f"📋 翻訳対象: {len(ordered_indices)} 件 / スキップ: {skipped_count} 件（{BATCH_SIZE}件ずつバッチ処理します）")

    # すでに処理済みのIDを追跡（FORCE_REGENERATE=True の場合でも、直前に処理したバッチの結果を後続バッチが参照できるようにする）
    newly_translated_ids = set()
    updated_count = 0

    for start in range(0, len(ordered_indices), BATCH_SIZE):
        batch_idx_slice = ordered_indices[start:start + BATCH_SIZE]
        batch_items = [
            {"idx": i, "id": videos[i]["id"], "original_title": videos[i]["original_title"]}
            for i in batch_idx_slice
        ]

        print(f"\n📦 バッチ処理中 ({start + 1}〜{start + len(batch_items)} / {len(ordered_indices)} 件):")
        for item in batch_items:
            print(f"   - [{item['id']}] {item['original_title']}")

        # FORCE_REGENERATE=True のときは、まだ再翻訳していない古いタイトルを参考にしないよう除外する
        if FORCE_REGENERATE:
            exclude_ids = {videos[i]["id"] for i in ordered_indices if videos[i]["id"] not in newly_translated_ids}
        else:
            exclude_ids = {item["id"] for item in batch_items}

        ref_videos = find_similar_translated_examples(batch_items, videos, exclude_ids, max_examples=4)
        if ref_videos:
            print(f"   🔗 統一感のため過去翻訳 {len(ref_videos)} 件を参考例として参照します")

        prompt = build_translation_prompt(batch_items, ref_videos)

        try:
            raw_res = call_gemini_with_retry(prompt, response_mime_type="application/json")
            parsed = parse_and_fix_json(raw_res)

            if not isinstance(parsed, dict):
                print("⚠️ JSONのパースに失敗したため、このバッチをスキップします。")
                continue

            batch_updated = 0
            for item in batch_items:
                vid = item["id"]
                idx = item["idx"]
                res_obj = parsed.get(vid)

                if not isinstance(res_obj, dict):
                    print(f"   ⚠️ [{vid}] の結果がレスポンスに含まれていませんでした。")
                    continue

                cleaned_title = {}
                valid = True
                for lang in TARGET_LANGS:
                    val = res_obj.get(lang)
                    if not isinstance(val, str) or not val.strip():
                        valid = False
                        break
                    cleaned_title[lang] = val.strip()

                if valid:
                    videos[idx]["title"] = cleaned_title
                    newly_translated_ids.add(vid)
                    updated_count += 1
                    batch_updated += 1
                    print(f"   ✅ [{vid}] JA: {cleaned_title['ja']}")
                    print(f"              EN: {cleaned_title['en']}")
                else:
                    print(f"   ⚠️ [{vid}] 一部の言語が空だったため保存をスキップしました。")

            if batch_updated > 0:
                save_json(VIDEOS_JSON_PATH, videos)

            time.sleep(1.5)

        except RuntimeError as re_err:
            print(f"\n{re_err}")
            break
        except Exception as e:
            print(f"❌ バッチ処理中に予期せぬエラー: {e}")

    print(f"\n🎉 処理終了: 更新 {updated_count} 件 / スキップ {skipped_count} 件")


if __name__ == "__main__":
    main()