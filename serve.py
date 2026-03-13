#!/usr/bin/env python3
import os, http.server, socketserver
os.chdir(os.path.dirname(os.path.abspath(__file__)))
with socketserver.TCPServer(("", 8080), http.server.SimpleHTTPRequestHandler) as httpd:
    print(f"Serving on http://localhost:8080")
    httpd.serve_forever()
