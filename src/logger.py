import os
import sys
from datetime import datetime

class Logger:
    """標準出力とログファイル（latest.log）の両方に追記出力するクラス"""
    def __init__(self, log_filepath):
        self.terminal = sys.stdout
        # "a" モード（追記）でオープン
        self.log_file = open(log_filepath, "a", encoding="utf-8")

    def write(self, message):
        self.terminal.write(message)
        self.log_file.write(message)
        self.log_file.flush()

    def flush(self):
        self.terminal.flush()
        self.log_file.flush()

def setup_logger():
    """logs/latest.log への追記ログを開始するセットアップ関数"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    # ログファイルの保存先（常に固定名）
    log_path = os.path.join(log_dir, "latest.log")
    
    sys.stdout = Logger(log_path)
    
    # 追記ログの区切り用ヘッダー
    print(f"\n==========================================")
    print(f"🚀 実行開始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"==========================================")
    
    return log_path