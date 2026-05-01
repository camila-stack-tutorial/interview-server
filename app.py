"""
Interview Assistant — Flask Backend
Handles: Claude API proxy, payment initiation via IntaSend, webhook verification
"""

import os, time
from flask import Flask, request, jsonify
from flask_cors import CORS
import anthropic
import requests as req

app = Flask(__name__)
CORS(app)

ANTHROPIC_API_KEY        = os.environ.get('ANTHROPIC_API_KEY', '')
INTASEND_PUBLISHABLE_KEY = os.environ.get('INTASEND_PUBLISHABLE_KEY', '')
INTASEND_SECRET_KEY      = os.environ.get('INTASEND_SECRET_KEY', '')
INTASEND_WEBHOOK_SECRET  = os.environ.get('INTASEND_WEBHOOK_SECRET', '')
IS_TEST                  = os.environ.get('INTASEND_TEST', 'true').lower() == 'true'

MODELS = {
    'fast':     ('claude-haiku-4-5-20251001', 280),
    'balanced': ('claude-sonnet-4-6',          350),
    'best':     ('claude-opus-4-6',            400),
}

sessions = {}
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ════════════════════════════════════════════════════
# HEALTH
# ════════════════════════════════════════════════════
@app.route('/health')
def health():
    return jsonify({'ok': True, 'time': int(time.time()), 'name': 'Shem is On You'})

# ════════════════════════════════════════════════════
# ASK CLAUDE
# ════════════════════════════════════════════════════
@app.route('/ask', methods=['POST'])
def ask():
    data          = request.get_json(force=True)
    uid           = data.get('uid', '')
    question      = data.get('question', '').strip()
    system_prompt = data.get('system_prompt', 'You are a helpful interview coach.')
    history       = data.get('history', [])
    speed         = data.get('speed', 'fast')

    if not question:
        return jsonify({'error': 'No question provided'}), 400

    session = sessions.get(uid)
    if not session or session.get('status') != 'paid':
        return jsonify({'error': 'Session not authorised'}), 403

    model, max_tokens = MODELS.get(speed, MODELS['fast'])
    messages = history[-12:] + [{'role': 'user', 'content': question}]

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages
        )
        return jsonify({'reply': response.content[0].text})
    except anthropic.APIError as e:
        return jsonify({'error': str(e)}), 500

# ════════════════════════════════════════════════════
# PAYMENT — INITIATE
# ════════════════════════════════════════════════════
@app.route('/payment/initiate', methods=['POST'])
def payment_initiate():
    data     = request.get_json(force=True)
    uid      = data.get('uid')
    amount   = data.get('amount')
    minutes  = data.get('minutes')
    currency = data.get('currency', 'KES')
    method   = data.get('method', 'M-PESA')   # 'M-PESA' or 'CARD-PAYMENT'
    phone    = data.get('phone', '')

    if not uid or not amount or not minutes:
        return jsonify({'error': 'Missing required fields'}), 400

    sessions[uid] = {'status': 'pending', 'pending_minutes': minutes}

    base = 'https://sandbox.intasend.com' if IS_TEST else 'https://payment.intasend.com'
    headers = {
        'Authorization': f'Bearer {INTASEND_SECRET_KEY}',
        'Content-Type': 'application/json'
    }
    payload = {
        'public_key': INTASEND_PUBLISHABLE_KEY,
        'currency':   currency,
        'amount':     amount,
        'api_ref':    uid,
        'comment':    f'Interview session {minutes}min',
        'method':     method,
        'redirect_url': 'https://uqmuqzybwnfy.eu-central-1.clawcloudrun.com/health'
    }

    # Only include phone for M-Pesa
    if method == 'M-PESA' and phone:
        payload['phone_number'] = phone

    try:
        r    = req.post(f'{base}/api/v1/checkout/', json=payload, headers=headers, timeout=8)
        resp = r.json()
        url  = resp.get('url', '')
        if not url:
            print('IntaSend initiate error:', resp)
            return jsonify({'error': 'Failed to create checkout'}), 500
        return jsonify({'payment_url': url, 'uid': uid})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ════════════════════════════════════════════════════
# PAYMENT — STATUS POLL
# ════════════════════════════════════════════════════
@app.route('/payment/status')
def payment_status():
    uid    = request.args.get('uid', '')
    status = sessions.get(uid, {}).get('status', 'pending')
    return jsonify({'status': status})

# ════════════════════════════════════════════════════
# PAYMENT — WEBHOOK
# ════════════════════════════════════════════════════
@app.route('/payment/webhook', methods=['POST'])
def payment_webhook():
    data = request.get_json(force=True)
    print('IntaSend webhook payload:', data)

    challenge = data.get('challenge', '')
    if challenge != INTASEND_WEBHOOK_SECRET:
        return jsonify({'error': 'Unauthorised'}), 401

    state = data.get('state', '')
    uid   = data.get('api_ref', '')

    if state == 'COMPLETE' and uid:
        if uid not in sessions:
            sessions[uid] = {}
        sessions[uid]['status']  = 'paid'
        sessions[uid]['minutes'] = sessions[uid].get('pending_minutes', 0)
    elif state == 'FAILED' and uid:
        if uid not in sessions:
            sessions[uid] = {}
        sessions[uid]['status'] = 'failed'

    return jsonify({'ok': True})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
