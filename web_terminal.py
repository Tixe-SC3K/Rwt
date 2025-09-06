#!/usr/bin/env python3
"""
web_terminal.py

A minimal local web-based terminal emulator for Linux, macOS, and Windows (via fallback).

Features:
- Serves a single-page web UI (xterm.js from CDN).
- WebSocket bridge between browser and a local shell.
- Automatic OS detection: Linux/macOS uses PTY, Windows uses pywinpty.
- Simple token-based auth (TERMINAL_TOKEN env var).

Dependencies:
  pip install aiohttp
  pip install pywinpty (only for Windows)

"""

import os
import sys
import asyncio
import subprocess
import struct
import argparse
import secrets
import json
import platform
from aiohttp import web

# Windows PTY backend
IS_WINDOWS = platform.system() == 'Windows'
if IS_WINDOWS:
    try:
        import pywinpty
    except ImportError:
        print('pywinpty is required on Windows: pip install pywinpty')
        sys.exit(1)
else:
    import fcntl
    import termios

HTML_PAGE = r'''<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Remote Web Terminal</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/xterm/css/xterm.css" />
    <style>
      html, body { height: 100%; width: 100%; margin: 0; padding: 0; background: #000; overflow: hidden; }
      #terminal { height: 100%; width: 100%; }
      .xterm { font-family: monospace, "Courier New", Courier, monospace !important; font-size: 14px; }
    </style>
  </head>
  <body>
    <div id="terminal"></div>
    <script src="https://cdn.jsdelivr.net/npm/xterm/lib/xterm.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/xterm-addon-fit/lib/xterm-addon-fit.js"></script>
    <script>
      (function(){
        const term = new window.Terminal({cursorBlink:true});
        const fitAddon = new window.FitAddon.FitAddon();
        term.loadAddon(fitAddon);
        term.open(document.getElementById('terminal'));
        fitAddon.fit();

        async function start() {
          let token = new URLSearchParams(location.search).get('token');
          if(!token) { token = prompt('Enter connection token:'); if(!token) return; }
          const proto = location.protocol === 'https:' ? 'wss' : 'ws';
          const ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(token)}`);
          ws.binaryType = 'arraybuffer';

          ws.onopen = () => { term.write('\x1b[32mConnected.\x1b[0m\r\n'); sendSize(); }
          ws.onmessage = (ev) => {
            if(typeof ev.data === 'string') {
              try { const msg = JSON.parse(ev.data); if(msg.type==='disconnect'){term.write('\r\n\x1b[31mDisconnected.\x1b[0m\r\n');} return;} catch(e){}
            }
            term.write(new Uint8Array(ev.data));
          };

          ws.onclose = () => term.write('\r\n\x1b[31mWebSocket closed\x1b[0m');
          ws.onerror = () => term.write('\r\n\x1b[31mWebSocket error\x1b[0m');

          term.onData(data => ws.send(new TextEncoder().encode(data)));

          function sendSize() { fitAddon.fit(); const cols=term.cols, rows=term.rows; if(ws.readyState===WebSocket.OPEN){ws.send(JSON.stringify({type:'resize', cols, rows}));} }
          window.addEventListener('resize', sendSize);
        }
        start();
      })();
    </script>
  </body>
</html>
'''

async def index(request):
    return web.Response(text=HTML_PAGE, content_type='text/html')

async def websocket_handler(request):
    ws = web.WebSocketResponse(max_msg_size=0)
    await ws.prepare(request)

    expected = os.environ.get('TERMINAL_TOKEN')
    token = request.query.get('token') or ''
    if not expected or token != expected:
        await ws.send_str('Invalid token or TERMINAL_TOKEN not set.'); await ws.close(); return ws

    loop = asyncio.get_event_loop()

    if IS_WINDOWS:
        # Windows: pywinpty backend
        cols, rows = 80, 24
        pty = pywinpty.PtyProcess.spawn([request.app['shell']], dimensions=(rows, cols))

        async def read_pty():
            while pty.isalive():
                data = await loop.run_in_executor(None, pty.read, 1024)
                if not data: break
                await ws.send_bytes(data.encode() if isinstance(data,str) else data)
            await ws.send_str(json.dumps({'type':'disconnect'}))
            await ws.close()

        task = asyncio.ensure_future(read_pty())

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        j = json.loads(msg.data)
                        if isinstance(j, dict) and j.get('type')=='resize':
                            pty.setwinsize(j.get('rows',24), j.get('cols',80))
                            continue
                    except Exception:
                        pty.write(msg.data)
                elif msg.type == web.WSMsgType.BINARY:
                    pty.write(msg.data)
                elif msg.type == web.WSMsgType.CLOSE:
                    break
        finally:
            task.cancel()
            try: pty.terminate()
            except Exception: pass

    else:
        # Unix-like: PTY backend
        master_fd, slave_fd = os.openpty()
        env = os.environ.copy(); env['TERM']=env.get('TERM','xterm-256color')
        proc = subprocess.Popen([request.app['shell']], preexec_fn=os.setsid, stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True, env=env)
        os.close(slave_fd)

        async def read_pty():
            while True:
                data = await loop.run_in_executor(None, os.read, master_fd, 1024)
                if not data: break
                await ws.send_bytes(data)
            await ws.send_str(json.dumps({'type':'disconnect'}))
            await ws.close()

        task = asyncio.ensure_future(read_pty())

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    try:
                        j = json.loads(msg.data)
                        if isinstance(j, dict) and j.get('type')=='resize':
                            set_pty_size(master_fd,j.get('rows',24),j.get('cols',80)); continue
                    except Exception:
                        os.write(master_fd,msg.data.encode())
                elif msg.type == web.WSMsgType.BINARY:
                    os.write(master_fd,msg.data)
                elif msg.type == web.WSMsgType.CLOSE:
                    break
        finally:
            task.cancel(); proc.terminate(); os.close(master_fd)

    return ws


def set_pty_size(fd, rows, cols):
    winsize = struct.pack('HHHH', rows, cols, 0, 0)
    import fcntl, termios
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


def main():
    parser = argparse.ArgumentParser(description='Remote web terminal')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=8765, type=int)
    parser.add_argument('--shell', default=os.environ.get('SHELL', 'cmd' if IS_WINDOWS else '/bin/bash'))
    args = parser.parse_args()

    if not os.environ.get('TERMINAL_TOKEN'):
        token = secrets.token_urlsafe(16)
        print('Generated token:', token)
        os.environ['TERMINAL_TOKEN'] = token

    app = web.Application()
    app['shell'] = args.shell
    app.router.add_get('/', index)
    app.router.add_get('/ws', websocket_handler)

    print(f'Serving on http://{args.host}:{args.port} (OS: {platform.system()})')
    web.run_app(app, host=args.host, port=args.port)


if __name__ == '__main__':
    main()
