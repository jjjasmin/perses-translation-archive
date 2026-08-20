import json

# 元となるテキストデータ（ファイルから読み込む場合は open('input.txt') を使用）
with open('00_DIY_input.txt', 'r', encoding='utf-8') as f:
    lines = [line.strip() for line in f if line.strip()]

# transcript の配列を生成
transcript = []
for idx, text in enumerate(lines, start=1):
    transcript.append({
        "id": idx,
        "start": 0,
        "end": 0,
        "text": text
    })

# 全体のデータ構造
data = {
    "video_id": "差し替え",
    "original_title": "差し替え",
    "upload_date": "差し替え",
    "raw_tags": [],
    "transcript": transcript
}

# JSONファイルに出力
with open('00_DIY_output.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("完了しました！ 00_DIY_output.json を確認してください。")