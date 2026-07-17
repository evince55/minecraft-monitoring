#!/usr/bin/env python3
import json
import time
import threading
import urllib.request
import urllib.error
import http.server
import datetime
import os
import sys

def load_config(config_path=None):
    if config_path is None:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
    with open(config_path) as f:
        return json.load(f)

config = None
poll_interval = 30
services = []
PORT = int(os.environ.get('STATUS_PORT', 8080))

results = []
results_lock = threading.Lock()

def init(config_path=None):
    global config, poll_interval, services
    config = load_config(config_path)
    poll_interval = config.get('poll_interval_seconds', 30)
    services = config['services']


def check_service(svc):
    name = svc['name']
    url = svc['url']
    check_type = svc.get('type', 'http_ok')
    timeout = svc.get('timeout', 10)
    field = svc.get('field')
    expected_value = svc.get('value')

    start = time.time()
    result = {
        'name': name,
        'status': 'unknown',
        'http_status': None,
        'latency_ms': None,
        'last_checked': None,
        'error': None,
    }

    try:
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result['latency_ms'] = int((time.time() - start) * 1000)
            result['http_status'] = resp.status
            result['last_checked'] = (
                datetime.datetime.now(datetime.timezone.utc)
                .isoformat()
            )

            if check_type == 'http_ok':
                result['status'] = 'up' if 200 <= resp.status < 400 else 'down'
            elif check_type == 'http_json':
                body = resp.read().decode('utf-8')
                data = json.loads(body)
                actual = data.get(field) if field else None
                result['status'] = (
                    'up' if actual == expected_value else 'down'
                )
    except Exception as e:
        result['latency_ms'] = int((time.time() - start) * 1000)
        result['last_checked'] = (
            datetime.datetime.now(datetime.timezone.utc)
            .isoformat()
        )
        result['status'] = 'down'
        result['error'] = f'{type(e).__name__}: {e}'

    return result


def poll_all():
    global results
    current = [check_service(svc) for svc in services]
    with results_lock:
        results = current


def poll_loop():
    while True:
        poll_all()
        time.sleep(poll_interval)


def escape_html(s):
    if s is None:
        return ''
    return (
        str(s)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )


def generate_html(statuses):
    total = len(statuses)
    up_count = sum(1 for s in statuses if s.get('status') == 'up')
    down_count = sum(1 for s in statuses if s.get('status') == 'down')
    unknown_count = total - up_count - down_count

    if down_count == 0 and unknown_count == 0:
        banner_cls = 'banner-up'
        banner_text = 'All Systems Operational'
    elif down_count > 0:
        banner_cls = 'banner-down'
        banner_text = f'{down_count} Service(s) Down'
    else:
        banner_cls = 'banner-degraded'
        banner_text = 'Degraded Performance'

    cards = ''
    for s in statuses:
        st = s.get('status', 'unknown')
        lat = s.get('latency_ms')
        lat_s = f'{lat}ms' if lat is not None else 'N/A'
        http_s = s.get('http_status') or 'N/A'
        last = s.get('last_checked') or 'N/A'
        err = s.get('error')
        err_html = (
            f'<div class="error">{escape_html(err)}</div>' if err else ''
        )

        cards += (
            '<div class="card status-' + st + '">'
            '<div class="card-header">'
            '<span class="indicator"></span>'
            '<span class="name">' + escape_html(s['name']) + '</span>'
            '</div>'
            '<div class="card-body">'
            '<div class="stat"><span class="label">HTTP</span> ' + str(http_s) + '</div>'
            '<div class="stat"><span class="label">Latency</span> ' + lat_s + '</div>'
            '<div class="stat"><span class="label">Checked</span> ' + escape_html(last) + '</div>'
            + err_html +
            '</div>'
            '</div>'
        )

    return f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Homelab Status</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0d1117;color:#c9d1d9;padding:40px 20px}}
.container{{max-width:960px;margin:0 auto}}
h1{{font-size:1.5rem;margin-bottom:24px;color:#f0f6fc}}
.banner{{padding:16px 20px;border-radius:8px;font-size:1.1rem;font-weight:600;margin-bottom:24px}}
.banner-up{{background:#0f2d1f;color:#3fb950;border:1px solid #1a4f2e}}
.banner-down{{background:#2d0f0f;color:#f85149;border:1px solid #4f1a1a}}
.banner-degraded{{background:#2d1f0f;color:#d29922;border:1px solid #4f3a1a}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}}
.card{{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:16px}}
.status-up{{border-left:4px solid #3fb950}}
.status-down{{border-left:4px solid #f85149}}
.status-unknown{{border-left:4px solid #8b949e}}
.card-header{{display:flex;align-items:center;gap:10px;margin-bottom:12px}}
.indicator{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}
.status-up .indicator{{background:#3fb950;box-shadow:0 0 6px #3fb950}}
.status-down .indicator{{background:#f85149;box-shadow:0 0 6px #f85149}}
.status-unknown .indicator{{background:#8b949e}}
.name{{font-weight:600;font-size:1rem}}
.card-body{{font-size:0.85rem;color:#8b949e}}
.stat{{margin-bottom:4px}}
.label{{color:#6e7681;margin-right:6px}}
.error{{color:#f85149;margin-top:8px;font-size:0.8rem;word-break:break-all}}
.footer{{text-align:center;margin-top:32px;font-size:0.8rem;color:#484f58}}
</style>
</head>
<body>
<div class="container">
<h1>Homelab Status</h1>
<div id="banner" class="banner {banner_cls}">{banner_text}</div>
<div id="grid" class="grid">{cards}</div>
<div class="footer">Auto-refreshing every 10s</div>
</div>
<script>
function e(t){{return document.createElement(t)}}
function t(t){{return document.createTextNode(t)}}
async function refresh(){{
try{{
var r=await fetch('/api/status');if(!r.ok)return
var d=await r.json()
var up=0,down=0;d.forEach(function(s){{if(s.status==='up')up++;else if(s.status==='down')down++}})
var b=document.getElementById('banner')
if(down===0&&up===d.length){{b.className='banner banner-up';b.textContent='All Systems Operational'}}
else if(down>0){{b.className='banner banner-down';b.textContent=down+' Service(s) Down'}}
else{{b.className='banner banner-degraded';b.textContent='Degraded Performance'}}
var g=document.getElementById('grid')
while(g.firstChild)g.removeChild(g.firstChild)
d.forEach(function(s){{
var c=e('div');c.className='card status-'+(s.status||'unknown')
var h=e('div');h.className='card-header'
var ind=e('span');ind.className='indicator'
var nm=e('span');nm.className='name';nm.appendChild(t(s.name))
h.appendChild(ind);h.appendChild(nm);c.appendChild(h)
var bd=e('div');bd.className='card-body'
var h1=e('div');h1.className='stat'
var l1=e('span');l1.className='label';l1.appendChild(t('HTTP '))
h1.appendChild(l1);h1.appendChild(t(s.http_status!=null?s.http_status:'N/A'));bd.appendChild(h1)
var h2=e('div');h2.className='stat'
var l2=e('span');l2.className='label';l2.appendChild(t('Latency '))
h2.appendChild(l2);h2.appendChild(t(s.latency_ms!=null?s.latency_ms+'ms':'N/A'));bd.appendChild(h2)
var h3=e('div');h3.className='stat'
var l3=e('span');l3.className='label';l3.appendChild(t('Checked '))
h3.appendChild(l3);h3.appendChild(t(s.last_checked||'N/A'));bd.appendChild(h3)
if(s.error){{var er=e('div');er.className='error';er.appendChild(t(s.error));bd.appendChild(er)}}
c.appendChild(bd);g.appendChild(c)
}})
}}catch(e){{console.error('refresh',e)}}
setTimeout(refresh,10000)
}}
refresh()
</script>
</body>
</html>'''


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/api/status':
            self._serve_json()
        elif self.path == '/':
            self._serve_html()
        else:
            self.send_response(404)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Not found')

    def _serve_json(self):
        with results_lock:
            data = json.dumps(results)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(data.encode('utf-8'))

    def _serve_html(self):
        with results_lock:
            statuses = list(results)
        html = generate_html(statuses)
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def log_message(self, fmt, *args):
        pass


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else None
    init(config_path)
    poll_all()
    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()

    server = http.server.HTTPServer(('', PORT), Handler)
    print(f'Homelab Status running on http://0.0.0.0:{PORT}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nShutting down...', flush=True)
        server.shutdown()


if __name__ == '__main__':
    main()
