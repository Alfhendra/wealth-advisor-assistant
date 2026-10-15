"""Local, deliberately synthetic CRM API; bind only to loopback."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            payload, status = {'status': 'ok'}, 200
        elif self.path.startswith('/clients/'):
            client = unquote(self.path[len('/clients/'):])
            if client == 'unavailable':
                payload, status = {'error': 'simulated outage'}, 503
            elif client == 'missing':
                payload, status = {'error': 'not found'}, 404
            else:
                payload, status = {'client_id': client, 'risk_tolerance': 'balanced'}, 200
        else:
            payload, status = {'error': 'not found'}, 404
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # Request URLs can contain client IDs; omit them from server logs.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'Synthetic CRM listening at http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
