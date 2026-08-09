import json
import os
from google import genai
import youtube_transcript_api as ytta
from yt_dlp import YoutubeDL

# ==========================================
# 1. 設定項目
# ==========================================
# ★ご自身のGemini APIキーをここに貼り付けてください
API_KEY = "AQ.Ab8RN6IckkMNj2e0Z97sG324h91XwBYWIfx8p-hYoN6wb1F9-w"

# テストしたいYouTube動画のID（例: https://www.youtube.com/watch?v=dQw4w9WgXcQ なら "dQw4w9WgXcQ"）
VIDEO_ID = "Koj9yImAjsI"

# ==========================================
# 2. 初期化と動画情報の取得 (yt-dlp)
# ==========================================
client = genai.Client(api_key=API_KEY)

def get_video_metadata(video_id: str):
    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {'quiet': True, 'skip_download': True}
    
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        title = info.get('title', '')
        upload_date = info.get('upload_date', '')
        
        # YYYYMMDD -> YYYY-MM-DD に変換
        if len(upload_date) == 8:
            formatted_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"
        else:
            formatted_date = ""
            
        return title, formatted_date

print("動画メタ情報を取得中...")
title, published_at = get_video_metadata(VIDEO_ID)

# ==========================================
# 3. 字幕データの取得
# ==========================================
print("字幕データを取得中...")

api = ytta.YouTubeTranscriptApi()
fetched = api.fetch(VIDEO_ID, languages=['th', 'en', 'ja'])

# 各要素を辞書型（dict）に変換して JSON 変換できるようにします
transcript_list = [
    {
        "text": item.text,
        "start": item.start,
        "duration": item.duration
    }
    for item in fetched
]

# ==========================================
# 4. Gemini API による翻訳と整形
# ==========================================
print("Geminiで翻訳・解析中...")

# プロンプトの組み立て
import json
import re

# ==========================================
# プロンプトの定義（JSON構造と文字エスケープを厳格化）
# ==========================================
prompt = f"""
以下の字幕データを解析し、指定されたJSON構造のみを出力してください。

【出力制約（厳格・文字エスケープルール）】
1. 必ず有効なJSONフォーマットのみを出力してください（Markdownの ```json ... ``` などの囲みも含めないでください）。
2. 文字数制限を意識し、途中で出力が切れないように記述してください。
3. すべての文字列は適切にダブルクォーテーションで閉じ、JSON構造（配列やオブジェクト）を途中で中断しないでください。
4. 【重要】タイ語原文（text）や日本語訳（translation）などの文章内に含まれるダブルクォーテーション（"）は、JSONの構文破綻を防ぐため、必ず `\"` にエスケープするか、「」や『』などの鍵かっこに置換して出力してください。改行文字が含まれる場合は必ず `\n` としてエスケープしてください。

【出力するJSONフォーマット】
{{
  "video_id": "{VIDEO_ID}",
  "title": "{title}",
  "published_at": "{published_at}",
  "thumbnail_url": "https://img.youtube.com/vi/{VIDEO_ID}/maxresdefault.jpg",
  "members": ["メンバー名リスト"],
  "transcript": [
    {{
      "id": 1,
      "start": 0.0,
      "speaker": "発話者名",
      "text": "タイ語原文",
      "pronunciation_kana": "カタカナ発音",
      "pronunciation_roman": "ローマ字発音",
      "translation": "日本語訳"
    }}
  ]
}}

【字幕データ】
{json.dumps(transcript_list, ensure_ascii=False)}
"""

print("Gemini APIへ送信中... (解析には1〜2分かかります)")

response = client.models.generate_content(
    model='gemini-3.5-flash',  # 応答速度が早く安定しているモデルを推奨
    contents=prompt,
    config={
        'response_mime_type': 'application/json',
        'max_output_tokens': 8192
    }
)

raw_text = response.text.strip()

# ==========================================
# JSONの破損防止・クレンジング処理
# ==========================================
def parse_and_fix_json(json_str):
    # Markdownのコードブロックが付いている場合は除去
    cleaned_str = re.sub(r'^```json\s*', '', json_str)
    cleaned_str = re.sub(r'\s*```$', '', cleaned_str)

    try:
        # まずは普通にパースしてみる
        return json.loads(cleaned_str)
    except json.JSONDecodeError:
        print("⚠️ 途中で切れた不完全なJSONを検知しました。補正処理を実行します...")
        
        # transcript 配列内で途切れている場合、最後の不完全なオブジェクトを除去して閉じる
        # 最後に完成している `}` を探す
        last_valid_object_index = cleaned_str.rfind('}')
        if last_valid_object_index != -1:
            # 最後に閉じているオブジェクトの位置まで切り取り
            truncated = cleaned_str[:last_valid_object_index + 1]
            
            # ブラケットやカッコが開いたままなら補完して閉じる
            if not truncated.rstrip().endswith(']}'):
                truncated += '\n  ]\n}'
            
            try:
                fixed_data = json.loads(truncated)
                print("✅ 破損していた末尾をカットし、正常なJSONとして復元しました！")
                return fixed_data
            except json.JSONDecodeError:
                pass

        raise ValueError("JSONの復元に失敗しました。字幕の文字数が多すぎる可能性があります。")

# 修正済みデータの取得
json_data = parse_and_fix_json(raw_text)

# JSONファイルに保存
output_file = f"video_{VIDEO_ID}.json"
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(json_data, f, ensure_ascii=False, indent=2)

print(f"『{output_file}』の出力が完了しました！")


import os

# ==========================================
# 5. videos.json の自動更新処理
# ==========================================
videos_file = "videos.json"
video_data = {
    "id": VIDEO_ID,
    "title": title,
    "file": f"video_{VIDEO_ID}.json"
}

# 既存の videos.json を読み込む（なければ空リスト）
if os.path.exists(videos_file):
    with open(videos_file, "r", encoding="utf-8") as f:
        try:
            videos_list = json.load(f)
        except json.JSONDecodeError:
            videos_list = []
else:
    videos_list = []

# すでに同じ動画IDが存在するかチェック
exists = False
for item in videos_list:
    if item.get("id") == VIDEO_ID:
        item["title"] = title  # タイトルを最新に更新
        item["file"] = f"video_{VIDEO_ID}.json"
        exists = True
        break

# 存在しなければリストに追加
if not exists:
    videos_list.append(video_data)

# videos.json に書き出し
with open(videos_file, "w", encoding="utf-8") as f:
    json.dump(videos_list, f, ensure_ascii=False, indent=2)

print(f"『{videos_file}』に動画情報を登録・更新しました！")