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
@app.route('/payment/initiate', methods=['POST'])
def payment_initiate():
    data     = request.get_json(force=True)
    uid      = data.get('uid', '').strip()
    email    = data.get('email', '').strip()
    amount   = data.get('amount')
    minutes  = data.get('minutes')
    currency = data.get('currency', 'KES')
    is_topup = bool(data.get('topup', False))

    if not uid or not email or amount is None or minutes is None:
        return jsonify({'error': 'Missing required fields'}), 400

    # Store pending state
    if uid not in sessions:
        sessions[uid] = {}

    if is_topup:
        sessions[uid]['topup_status']          = 'pending'
        sessions[uid]['topup_pending_minutes']  = int(minutes)
    else:
        sessions[uid]['status']          = 'pending'
        sessions[uid]['pending_minutes'] = int(minutes)

    # No IntaSend keys → dev mode, auto-confirm after 8s
    if not INTASEND_API_KEY or not INTASEND_SECRET:
        _dev_auto_confirm(uid, is_topup)
        return jsonify({
            'payment_url': f'{BASE_URL}/payment/dev-notice',
            'uid': uid,
            'dev_mode': True
        })

    payment_url = _create_intasend_checkout(uid, email, amount, currency, minutes, is_topup)

    if not payment_url:
        return jsonify({'error': 'Failed to create payment link — check IntaSend keys'}), 502

    return jsonify({'payment_url': payment_url, 'uid': uid})


def _create_intasend_checkout(uid, email, amount, currency, minutes, is_topup):
    """Call IntaSend REST API to create a hosted checkout session."""
    try:
        headers = {
            'Authorization': f'Bearer {INTASEND_SECRET}',
            'Content-Type': 'application/json',
        }
        payload = {
            'public_key':    INTASEND_API_KEY,
            'currency':      currency,           # 'KES' or 'USD'
            'amount':        str(amount),
            'email':         email,
            'comment':       f'Interview session {minutes}min | uid:{uid}',
            'redirect_url':  f'{BASE_URL}/payment/callback?uid={uid}&topup={1 if is_topup else 0}',
            'webhook_url':   f'{BASE_URL}/payment/webhook',
            'metadata': {
                'uid':     uid,
                'minutes': minutes,
                'topup':   is_topup,
            }
        }
        r = req.post(
            'https://payment.intasend.com/api/v1/checkout/',
            json=payload,
            headers=headers,
            timeout=10
        )
        resp = r.json()

        # IntaSend returns { url: "https://payment.intasend.com/pay/..." }
        return resp.get('url') or resp.get('checkout_url', '')

    except Exception as e:
        print(f'IntaSend error: {e}')
        return ''


def _dev_auto_confirm(uid, is_topup):
    """Dev only — auto-marks payment as paid after 8 seconds."""
    def confirm():
        time.sleep(8)
        _mark_paid(uid, is_topup)
    threading.Thread(target=confirm, daemon=True).start()


def _mark_paid(uid, is_topup):
    """Mark a session or topup as paid."""
    if uid not in sessions:
        sessions[uid] = {}
    if is_topup:
        sessions[uid]['topup_status'] = 'paid'
    else:
        sessions[uid]['status']  = 'paid'
        sessions[uid]['minutes'] = sessions[uid].get('pending_minutes', 0)


# ════════════════════════════════════════════════════
# PAYMENT — STATUS POLL
# Extension polls this every 5 seconds after initiating payment
# ════════════════════════════════════════════════════
@app.route('/payment/status')
def payment_status():
    uid      = request.args.get('uid', '')
    is_topup = request.args.get('topup', '0') == '1'
    session  = sessions.get(uid, {})

    if is_topup:
        status = session.get('topup_status', 'pending')
    else:
        status = session.get('status', 'pending')

    return jsonify({'status': status})


# ════════════════════════════════════════════════════
# PAYMENT — WEBHOOK
# IntaSend calls this when payment completes
# ════════════════════════════════════════════════════
@app.route('/payment/webhook', methods=['POST'])
def payment_webhook():
    data     = request.get_json(force=True)
    state    = data.get('state', '')          # 'COMPLETE' | 'FAILED' | 'PENDING'
    invoice  = data.get('invoice', {})
    metadata = invoice.get('metadata', {}) or data.get('metadata', {})
    uid      = str(metadata.get('uid', ''))
    is_topup = bool(metadata.get('topup', False))

    if not uid:
        return jsonify({'ok': False, 'error': 'No uid in metadata'}), 400

    if state == 'COMPLETE':
        _mark_paid(uid, is_topup)
    elif state == 'FAILED':
        if uid in sessions:
            if is_topup:
                sessions[uid]['topup_status'] = 'failed'
            else:
                sessions[uid]['status'] = 'failed'

    return jsonify({'ok': True})


# ════════════════════════════════════════════════════
# PAYMENT — CALLBACK (redirect after IntaSend page)
# IntaSend redirects user here after paying
# ════════════════════════════════════════════════════
@app.route('/payment/callback')
def payment_callback():
    uid      = request.args.get('uid', '')
    is_topup = request.args.get('topup', '0') == '1'
    # Webhook should have already fired, but mark paid here as backup
    if uid:
        _mark_paid(uid, is_topup)
    return '''<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"/><title>Payment Confirmed</title></head>
<body style="font-family:sans-serif;text-align:center;padding:60px 20px;background:#0d0f14;color:#fff">
  <div style="font-size:3rem;margin-bottom:16px">&#x2713;</div>
  <h2 style="color:#e8c55a;margin-bottom:8px">Payment Confirmed!</h2>
  <p style="color:#888;font-size:.9rem">You can close this tab and return to the extension.</p>
</body>
</html>'''


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
    print(f'  OpenRouter model : {OPENROUTER_MODEL}')
    print(f'  IntaSend keys    : {"configured" if INTASEND_API_KEY else "NOT SET — dev mode active"}')
    print(f'  Base URL         : {BASE_URL}')
    app.run(debug=True, host='0.0.0.0', port=5000)
