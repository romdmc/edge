
import sys
sys.path.insert(0, '.')
from http.server import HTTPServer, BaseHTTPRequestHandler
from api import EdgeAPIHandler
import threading, time, urllib.request, json

server = HTTPServer(('127.0.0.1', 8888), EdgeAPIHandler)
t = threading.Thread(target=server.serve_forever)
t.daemon = True
t.start()
time.sleep(999)
