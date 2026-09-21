# tiktok.xのurl書き込みしていくためのサーバー
from http.server import BaseHTTPRequestHandler, HTTPServer
import json

FILE_PATH = "urls.txt" # 書き込み先のファイルパス

class RequestHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        data = json.loads(post_data.decode('utf-8'))
        
        url = data.get('url')
        if url:
            with open(FILE_PATH, 'a', encoding='utf-8') as f:
                f.write(url + '\n')
            
            self.send_response(200)
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
            self.end_headers()
            self.wfile.write(b'{"status":"success"}')

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

if __name__ == '__main__':
    server = HTTPServer(('127.0.0.1', 8010), RequestHandler)
    print("Server running on http://127.0.0.1:8010")
    server.serve_forever()