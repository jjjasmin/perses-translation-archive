import os
import sys
from datetime import datetime

class StreamLogger:
    """標準出力／標準エラー出力とログファイル（latest.log）の両方に書き出すクラス"""
    def __init__(self, original_stream, log_file):
        self.original_stream = original_stream
        self.log_file = log_file

    def write(self, message):
        self.original_stream.write(message)
        self.log_file.write(message)
        self.log_file.flush()

    def flush(self):
        self.original_stream.flush()
        self.log_file.flush()

def setup_logger():
    """プロジェクトルート直下の logs/latest.log への追記ログを開始する"""
    # src/logger.py の親ディレクトリ（=プロジェクトルート）を取得
    base_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(base_dir, ".."))
    
    log_dir = os.path.join(project_root, "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_path = os.path.join(log_dir, "latest.log")
    
    log_file = open(log_path, "a", encoding="utf-8")

    sys.stdout = StreamLogger(sys.stdout, log_file)
    sys.stderr = StreamLogger(sys.stderr, log_file)
    
    print(f"\n==========================================")
    print(f"🚀 実行開始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"==========================================")
    
    return log_path