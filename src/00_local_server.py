import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import List

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# ==========================================
# 設定情報
# ==========================================
# サーバーファイル（00_local_server.py）から見たルートディレクトリを正確に取得
BASE_DIR = Path(__file__).resolve().parent.parent

TEMP_DIR = BASE_DIR / "data" / "temp"               # main/data/temp
PIPELINE_STATUS_FILE = BASE_DIR / "pipeline_status.json"  # main/pipeline_status.json

# お使いの環境の「ダウンロード」フォルダパス
DOWNLOADS_DIR = Path.home() / "Downloads"

# ディレクトリの事前作成
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# ==========================================
# 1. 動画IDの抽出ユーティリティ
# ==========================================
def extract_video_id(url: str) -> str | None:
    """YouTubeのURLから11桁の動画IDを取得"""
    patterns = [
        r"(?:v=|\/shorts\/|\/embed\/|\/youtu\.be\/)([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

# ==========================================
# 2. pipeline_status.json 管理クラス
# ==========================================
class PipelineStatusManager:
    def __init__(self, filepath: Path):
        self.filepath = filepath
        self._load()

    def _load(self):
        if self.filepath.exists():
            with open(self.filepath, "r", encoding="utf-8") as f:
                try:
                    self.data = json.load(f)
                except json.JSONDecodeError:
                    self.data = {}
        else:
            self.data = {}
            self._save()

    def _save(self):
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def mark_completed(self, url_or_id: str):
        video_id = extract_video_id(url_or_id) or url_or_id
        self._load()

        if video_id in self.data:
            if isinstance(self.data[video_id], dict):
                self.data[video_id]["pipeline_status"] = "collected"
            self._save()

status_mgr = PipelineStatusManager(PIPELINE_STATUS_FILE)

# ==========================================
# 3. ファイル自動移動処理 (Watchdog & 既存一括移動)
# ==========================================
def process_existing_downloads():
    """起動時にDownloadsフォルダに残っているtemp_source_*.jsonを一括移動"""
    print(f"🧹 [Watchdog] Downloads 内の既存ファイルをチェック中: {DOWNLOADS_DIR}")
    for file_path in DOWNLOADS_DIR.glob("temp_source_*.json"):
        move_file_to_temp(file_path)

def move_file_to_temp(src_path: Path):
    """ファイルを安全に main/data/temp へ移動"""
    if not src_path.exists():
        return
    
    # 書き込み完了待ち（ダウンロード直後のファイルロック対策）
    time.sleep(1)
    
    dest_path = TEMP_DIR / src_path.name
    try:
        shutil.move(str(src_path), str(dest_path))
        print(f"📦 [Watchdog] 移動完了: {src_path.name} -> {dest_path}")
    except Exception as e:
        print(f"❌ [Watchdog] 移動失敗 ({src_path.name}): {e}")

class JsonDownloadHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".json"):
            move_file_to_temp(Path(event.src_path))

    def on_moved(self, event):
        # ブラウザが .crdownload から .json にリネーム（完了）した瞬間を検知
        if not event.is_directory and event.dest_path.endswith(".json"):
            move_file_to_temp(Path(event.dest_path))

def start_folder_watchdog():
    # 1. 起動時にまず既存ファイルを処理
    process_existing_downloads()

    # 2. 監視を開始
    event_handler = JsonDownloadHandler()
    observer = Observer()
    observer.schedule(event_handler, str(DOWNLOADS_DIR), recursive=False)
    observer.start()
    print(f"👀 [Watchdog] 監視開始: {DOWNLOADS_DIR}")
    return observer

# ==========================================
# 4. FastAPI サーバー & リクエストモデル
# ==========================================
app = FastAPI(title="YouTube Processing Pipeline API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class CompleteRequest(BaseModel):
    url: str

@app.post("/collect-urls")
async def collect_urls(request: Request):
    try:
        data = await request.json()
        urls = data.get("urls", [])
    except Exception:
        return {"unprocessed_urls": []}

    existing_ids = set()
    if PIPELINE_STATUS_FILE.exists():
        with open(PIPELINE_STATUS_FILE, "r", encoding="utf-8") as f:
            try:
                status_data = json.load(f)
                existing_ids = set(status_data.keys())
            except json.JSONDecodeError:
                existing_ids = set()

    unprocessed_urls = []
    for url in urls:
        video_id = extract_video_id(url)
        if video_id and video_id not in existing_ids:
            unprocessed_urls.append(url)

    print(f"✅ [API] 重複チェック完了: 受信 {len(urls)} 件 -> 未処理 {len(unprocessed_urls)} 件")
    return {"unprocessed_urls": unprocessed_urls}

@app.post("/mark-completed")
def mark_completed(req: CompleteRequest):
    status_mgr.mark_completed(req.url)
    print(f"✅ [API] 収集完了: {req.url}")
    return {"status": "success"}

if __name__ == "__main__":
    observer = start_folder_watchdog()
    try:
        uvicorn.run(app, host="127.0.0.1", port=8080, log_level="info")
    finally:
        observer.stop()
        observer.join()