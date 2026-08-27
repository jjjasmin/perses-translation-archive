import json
import os


# skipped_english_onlyのブロックを全消去するプロンプトです。
# pipeline_status.json から該当の VIDEO_ID の記述を消します。
# pipeline_status.json から、対象の VIDEO_ID のブロック（"skipped_english_only" と書かれている部分）を削除して保存してから通常実行すると、
# 未処理の新規データとして認識されます。


# パス設定（01_generate_raw_chunks.py と同じディレクトリ構成に対応）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.abspath(os.path.join(BASE_DIR, "..", "pipeline_status.json"))

def clean_skipped_english_entries():
    """
    pipeline_status.json から "generate": "skipped_english_only" のエントリーを全削除する
    """
    if not os.path.exists(STATUS_FILE):
        print(f"❌ エラー: ステータスファイルが見つかりません: {STATUS_FILE}")
        return

    # JSONの読み込み
    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            status_data = json.load(f)
    except Exception as e:
        print(f"❌ JSON読み込みエラー: {e}")
        return

    # "generate": "skipped_english_only" のIDを抽出
    target_ids = [
        vid for vid, info in status_data.items()
        if isinstance(info, dict) and info.get("generate") == "skipped_english_only"
    ]

    if not target_ids:
        print("ℹ️ 『skipped_english_only』 のデータは見つかりませんでした。処理を終了します。")
        return

    print(f"🔍 『skipped_english_only』 のデータを {len(target_ids)} 件検出しました。削除を開始します...\n")

    # 対象のキーを削除
    for vid in target_ids:
        title = status_data[vid].get("title", "タイトルなし")
        del status_data[vid]
        print(f"🗑️ 削除: [{vid}] - {title}")

    # 上書き保存
    try:
        with open(STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(status_data, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 計 {len(target_ids)} 件のスキップデータを削除し、pipeline_status.json を更新しました！")
        print("💡 01_generate_raw_chunks.py を通常実行すると、新しいタイ語ソースで再処理されます。")
    except Exception as e:
        print(f"❌ ファイル保存エラー: {e}")

if __name__ == "__main__":
    clean_skipped_english_entries()