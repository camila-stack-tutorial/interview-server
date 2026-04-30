"""
Interview Assistant — Flask Backend v7
- AI: OpenRouter (google/gemma-3-4b-it:free)
- Payments: IntaSend (M-Pesa + Card, Kenya)
"""

import os, time, threading, requests as req
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()  # loads .env file automatically when running locally

app = Flask(__name__)
CORS(app)

# ── Config — load from environment variables (.env file)
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY', '')
INTASEND_API_KEY   = os.environ.get('INTASEND_API_KEY', '')    # publishable key
INTASEND_SECRET    = os.environ.get('INTASEND_SECRET', '')     # secret key
BASE_URL           = os.environ.get('BASE_URL', 'http://localhost:5000')

OPENROUTER_URL  = 'https://openrouter.ai/api/v1/chat/completions'
OPENROUTER_MODEL = 'google/gemma-3-4b-it:free'

# ── Model speed mapping
# OpenRouter free tier uses the same model regardless of speed,
# but we adjust max_tokens to keep fast responses lean
SPEED_TOKENS = {
    'fast':     250,
    'balanced': 350,
    'best':     500,
}

# ── In-memory session store
# Structure: { uid: { status, minutes, topup_status, topup_pending_minutes } }
# Replace with Redis or SQLite for production multi-worker deployments
sessions = {}


# ════════════════════════════════════════════════════
# HEALTH CHECK
# ════════════════════════════════════════════════════
@app.route('/health')
def health():
    return jsonify({'ok': True, 'time': int(time.time())})


# ════════════════════════════════════════════════════
# ASK  — proxies question to OpenRouter
# ════════════════════════════════════════════════════
@app.route('/ask', methods=['POST'])
def ask():
    data          = request.get_json(force=True)
    uid           = data.get('uid', '')
    question      = data.get('question', '').strip()
    system_prompt = data.get('system_prompt', 'You are a helpful interview coach.')
    history       = data.get('history', [])   # list of {role, content}
    speed         = data.get('speed', 'fast')

    if not question:
        return jsonify({'error': 'No question provided'}), 400

    if not OPENROUTER_API_KEY:
        return jsonify({'error': 'OpenRouter API key not configured on server'}), 500

    max_tokens = SPEED_TOKENS.get(speed, 250)

    # Build message list: system + last 12 history turns + new question
    messages = [{'role': 'system', 'content': system_prompt}]
    messages += history[-12:]
    messages.append({'role': 'user', 'content': question})

    try:
        response = req.post(
            OPENROUTER_URL,
            headers={
                'Authorization': f'Bearer {OPENROUTER_API_KEY}',
                'Content-Type': 'application/json',
                # OpenRouter recommends these headers for tracking/ranking
                'HTTP-Referer': BASE_URL,
                'X-Title': 'Interview Assistant',
            },
            json={
                'model': OPENROUTER_MODEL,
                'messages': messages,
                'max_tokens': max_tokens,
                'temperature': 0.7,
            },
            timeout=30   # generous timeout for free-tier model
        )

        if response.status_code != 200:
            err = response.json()
            msg = err.get('error', {}).get('message', f'OpenRouter error {response.status_code}')
            return jsonify({'error': msg}), 502

        result = response.json()
        reply  = result['choices'][0]['message']['content'].strip()
        return jsonify({'reply': reply})

    except req.exceptions.Timeout:
        return jsonify({'error': 'AI response timed out — please try again'}), 504
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ════════════════════════════════════════════════════
# PAYMENT — INITIATE
# Calls IntaSend hosted checkout API
# Docs: https://developers.intasend.com/docs/checkout
# ════════════════════════════════════════════════════

# ════════════════════════════════════════════════════
# PAYMENT — STATUS POLL (extension polls this every 5s)
# ════════════════════════════════════════════════════
@app.route('/payment/status')
def payment_status():
    uid     = request.args.get('uid', '')
    session = sessions.get(uid, {})
    status  = session.get('status', 'pending')
    return jsonify({'status': status})

# ════════════════════════════════════════════════════
# PAYMENT — WEBHOOK (IntaSend posts here on completion)
# ════════════════════════════════════════════════════
@app.route('/payment/webhook', methods=['POST'])
def payment_webhook():
    data      = request.get_json(force=True)
    challenge = data.get('challenge', '')

    # Validate challenge
    if challenge != INTASEND_WEBHOOK_SECRET:
        return jsonify({'error': 'Unauthorised'}), 401

    print('IntaSend webhook payload:', data)

    state = data.get('state', '')

    uid = (
        request.args.get('session_id')
        or data.get('session_id')
        or data.get('metadata', {}).get('session_id', '')
    )

    if state == 'COMPLETE' and uid:
        if uid not in sessions:
            sessions[uid] = {}
        sessions[uid]['status'] = 'paid'

    return jsonify({'ok': True})

# ════════════════════════════════════════════════════
# DEV NOTICE PAGE (shown in dev mode instead of real payment)
# ════════════════════════════════════════════════════
@app.route('/payment/dev-notice')
def dev_notice():
    return '''<!DOCTYPE html>
            <html>
            <head><meta charset="UTF-8"/><title>Dev Mode</title></head>
            <body style="font-family:sans-serif;text-align:center;padding:60px 20px;background:#0d0f14;color:#fff">
              <div style="font-size:2.5rem;margin-bottom:12px">&#x1F6E0;</div>
              <h2 style="color:#5ab8e8;margin-bottom:8px">Dev Mode — No Real Payment</h2>
              <p style="color:#888;font-size:.9rem">Payment will auto-confirm in ~8 seconds.<br/>Close this tab and watch the extension.</p>
            </body>
            </html>'''

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)

