from http.server import SimpleHTTPRequestHandler, HTTPServer
import os

os.chdir("/home/Chakradhar/cpbot_images")

server = HTTPServer(("0.0.0.0", 8080), SimpleHTTPRequestHandler)
print("Serving images on port 8080")
server.serve_forever()
