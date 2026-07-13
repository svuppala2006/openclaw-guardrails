"""
OpenAI-compatible proxy for NemoGuardrails.

Problem: NemoGuardrails returns {"messages": [...]} format, but the openclaw
gateway expects OpenAI-compatible {"choices": [...]} format. This proxy bridges
the gap.

Flow:
  1. Receives OpenAI-format chat completion requests from the gateway
  2. Extracts the latest user message
  3. Runs the NemoGuardrails self-check input rail against it
  4. If BLOCKED: returns an OpenAI-format response with the block message
  5. If PASSED: forwards the original request to LiteLLM and streams the
     response back to the gateway untouched

Environment variables:
  LITELLM_URL       - LiteLLM base URL (default: http://localhost:4000)
  LITELLM_API_KEY   - LiteLLM master key for auth
  NEMO_CONFIG_PATH  - Path to NemoGuardrails config dir (default: /etc/nemo-guardrails/default)
  PROXY_PORT        - Port to listen on (default: 8000)
  HF_HOME           - HuggingFace cache dir (set to /tmp/hf_cache for container permissions)
"""
import asyncio
import json
import time
import uuid
import os
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import URLError

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('guardrails-proxy')

LITELLM_URL = os.environ.get('LITELLM_URL', 'http://localhost:4000')
LITELLM_KEY = os.environ.get('LITELLM_API_KEY', '')
NEMO_CONFIG_PATH = os.environ.get('NEMO_CONFIG_PATH', '/etc/nemo-guardrails/default')

rails = None
_loop = None
_loop_thread = None


def init_rails():
    """Initialize NemoGuardrails from the config directory and start an async event loop."""
    global rails, _loop, _loop_thread
    from nemoguardrails import RailsConfig, LLMRails
    config = RailsConfig.from_path(NEMO_CONFIG_PATH)
    rails = LLMRails(config)
    log.info('NemoGuardrails initialized from %s', NEMO_CONFIG_PATH)
    _loop = asyncio.new_event_loop()
    _loop_thread = threading.Thread(target=_loop.run_forever, daemon=True)
    _loop_thread.start()


def run_async(coro):
    """Run an async coroutine from synchronous code using the background event loop."""
    future = asyncio.run_coroutine_threadsafe(coro, _loop)
    return future.result(timeout=120)


async def check_input(user_message):
    """
    Run NemoGuardrails self-check input against the user message.
    Returns (is_blocked: bool, response_message: str).
    """
    try:
        result = await rails.generate_async(
            messages=[{'role': 'user', 'content': user_message}]
        )
        bot_message = ''
        if isinstance(result, dict):
            bot_message = result.get('content', '')
        elif isinstance(result, list) and result:
            bot_message = result[0].get('content', '') if isinstance(result[0], dict) else str(result[0])
        if not bot_message:
            bot_message = str(result) if result else ''

        blocked_phrases = [
            "i can't respond to that",
            "cannot respond to that",
            "i cannot help with that",
            "internal error",
        ]
        is_blocked = any(phrase in bot_message.lower() for phrase in blocked_phrases)
        return is_blocked, bot_message
    except Exception as e:
        log.error('Guardrails check error: %s', e)
        return False, ''


def make_openai_response(content, model='claude-sonnet-4-6'):
    """Build a non-streaming OpenAI chat completion response."""
    return {
        'id': f'chatcmpl-{uuid.uuid4().hex[:12]}',
        'object': 'chat.completion',
        'created': int(time.time()),
        'model': model,
        'choices': [{
            'index': 0,
            'message': {'role': 'assistant', 'content': content},
            'finish_reason': 'stop'
        }],
        'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
    }


def make_sse_blocked(content, model):
    """Build SSE streaming chunks for a blocked response."""
    chunk_id = f'chatcmpl-{uuid.uuid4().hex[:12]}'
    created = int(time.time())
    lines = []
    lines.append(f'data: {json.dumps({"id": chunk_id, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})}\n\n')
    lines.append(f'data: {json.dumps({"id": chunk_id, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]})}\n\n')
    lines.append(f'data: {json.dumps({"id": chunk_id, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})}\n\n')
    lines.append('data: [DONE]\n\n')
    return ''.join(lines)


class ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'status': 'ok'}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if '/chat/completions' not in self.path:
            self.send_response(404)
            self.end_headers()
            return

        content_length = int(self.headers.get('Content-Length', 0))
        body = json.loads(self.rfile.read(content_length))
        model = body.get('model', 'claude-sonnet-4-6')
        stream = body.get('stream', False)
        messages = body.get('messages', [])

        # Extract the latest user message for guardrails check
        user_message = ''
        for msg in reversed(messages):
            if msg.get('role') == 'user':
                user_message = msg.get('content', '')
                break

        if user_message:
            blocked, block_msg = run_async(check_input(user_message))

            if blocked:
                log.info('BLOCKED by guardrails: %s', user_message[:100])
                if stream:
                    sse = make_sse_blocked(block_msg, model)
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Cache-Control', 'no-cache')
                    self.end_headers()
                    self.wfile.write(sse.encode())
                else:
                    resp = make_openai_response(block_msg, model)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(resp).encode())
                return

        # Safe message: forward to LiteLLM
        log.info('PASSED guardrails, forwarding to LiteLLM (stream=%s)', stream)
        try:
            headers = {'Content-Type': 'application/json'}
            if LITELLM_KEY:
                headers['Authorization'] = f'Bearer {LITELLM_KEY}'
            for h in ['Authorization', 'X-Api-Key']:
                if self.headers.get(h):
                    headers[h] = self.headers[h]

            req = Request(
                f'{LITELLM_URL}/v1/chat/completions',
                data=json.dumps(body).encode(),
                headers=headers,
                method='POST'
            )

            if stream:
                resp = urlopen(req, timeout=300)
                self.send_response(resp.status)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Cache-Control', 'no-cache')
                self.end_headers()
                while True:
                    chunk = resp.read(4096)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            else:
                resp = urlopen(req, timeout=300)
                resp_body = resp.read()
                self.send_response(resp.status)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(resp_body)
        except Exception as e:
            log.error('LiteLLM proxy error: %s', e)
            if stream:
                sse = make_sse_blocked(f'Error communicating with model: {e}', model)
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.end_headers()
                self.wfile.write(sse.encode())
            else:
                resp = make_openai_response(f'Error: {e}', model)
                self.send_response(502)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(resp).encode())

    def log_message(self, format, *args):
        log.info(format, *args)


class ThreadedHTTPServer(HTTPServer):
    """Handle each request in a new thread for concurrent streaming support."""
    def process_request(self, request, client_address):
        t = threading.Thread(target=self.process_request_thread, args=(request, client_address))
        t.daemon = True
        t.start()

    def process_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


if __name__ == '__main__':
    log.info('Initializing NemoGuardrails...')
    init_rails()
    port = int(os.environ.get('PROXY_PORT', '8000'))
    server = ThreadedHTTPServer(('0.0.0.0', port), ProxyHandler)
    log.info('Guardrails proxy listening on port %d', port)
    server.serve_forever()
