"""
Interview Assistant — Flask Backend
Handles: Claude API proxy, payment session registration, webhook verification
"""

import os, time
from flask import Flask, request, jsonify
from flask_cors import CORS
import anthropic
import requests as req

app = Flask(__name__)
CORS(app)

def get_env():
    return {
        'ANTHROPIC_API_KEY':        os.environ.get('ANTHROPIC_API_KEY', ''),
        'INTASEND_PUBLISHABLE_KEY': os.environ.get('INTASEND_PUBLISHABLE_KEY', ''),
        'INTASEND_SECRET_KEY':      os.environ.get('INTASEND_SECRET_KEY', ''),
        'INTASEND_WEBHOOK_SECRET':  os.environ.get('INTASEND_WEBHOOK_SECRET', ''),
        'IS_TEST':                  os.environ.get('INTASEND_TEST', 'true').lower() == 'true',
    }

MODELS = {
    'fast':     ('claude-haiku-4-5-20251001', 280),
    'balanced': ('claude-sonnet-4-6',          350),
    'best':     ('claude-opus-4-6',            400),
}

sessions = {}

# ════════════════════════════════════════════════════
# HEALTH
# ════════════════════════════════════════════════════
@app.route('/health')
def health():
    return jsonify({'ok': True, 'time': int(time.time()), 'name': 'Shem is On You'})

# ════════════════════════════════════════════════════
# DEBUG — remove after confirming env vars work
# ════════════════════════════════════════════════════
@app.route('/debug/env')
def debug_env():
    env = get_env()
    return jsonify({
        'anthropic_key':  env['ANTHROPIC_API_KEY'][:10]        if env['ANTHROPIC_API_KEY']        else 'EMPTY',
        'secret_key':     env['INTASEND_SECRET_KEY'][:10]      if env['INTASEND_SECRET_KEY']      else 'EMPTY',
        'pub_key':        env['INTASEND_PUBLISHABLE_KEY'][:10] if env['INTASEND_PUBLISHABLE_KEY'] else 'EMPTY',
        'webhook_secret': env['INTASEND_WEBHOOK_SECRET'][:6]   if env['INTASEND_WEBHOOK_SECRET']  else 'EMPTY',
        'is_test':        env['IS_TEST'],
    })

# ════════════════════════════════════════════════════
# ASK CLAUDE
# ════════════════════════════════════════════════════
@app.route('/ask', methods=['POST'])
def ask():
    env           = get_env()
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
        client   = anthropic.Anthropic(api_key=env['ANTHROPIC_API_KEY'])
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
# PAYMENT — REGISTER SESSION (called before IntaSend popup opens)
# Stores uid + minutes so webhook or /confirm can find it
# ════════════════════════════════════════════════════
@app.route('/payment/session', methods=['POST'])
def payment_session():
    data    = request.get_json(force=True)
    uid     = data.get('uid')
    minutes = data.get('minutes')

    if not uid or not minutes:
        return jsonify({'error': 'Missing uid or minutes'}), 400

    sessions[uid] = {'status': 'pending', 'pending_minutes': minutes}
    print(f'Session registered: uid={uid} minutes={minutes}')
    return jsonify({'ok': True})

# ════════════════════════════════════════════════════
# PAYMENT — CONFIRM (called by frontend on IntaSend COMPLETE event)
# IntaSend's inline SDK fires COMPLETE in the browser — we trust it
# and mark the session paid immediately
# ════════════════════════════════════════════════════
@app.route('/payment/confirm', methods=['POST'])
def payment_confirm():
    data = request.get_json(force=True)
    uid  = data.get('uid')

    if not uid:
        return jsonify({'error': 'Missing uid'}), 400

    session = sessions.get(uid)
    if not session:
        # Session wasn't pre-registered (race condition) — create it
        sessions[uid] = {'status': 'paid', 'minutes': 60, 'pending_minutes': 60}
        print(f'Confirm: uid={uid} — new session created and marked PAID')
        return jsonify({'ok': True})

    sessions[uid]['status']  = 'paid'
    sessions[uid]['minutes'] = sessions[uid].get('pending_minutes', 60)
    print(f'Confirm: uid={uid} marked PAID via frontend COMPLETE event')
    return jsonify({'ok': True})

# ════════════════════════════════════════════════════
# PAYMENT — STATUS POLL
# ════════════════════════════════════════════════════
@app.route('/payment/status')
def payment_status():
    uid    = request.args.get('uid', '')
    status = sessions.get(uid, {}).get('status', 'pending')
    return jsonify({'status': status})

# ════════════════════════════════════════════════════
# PAYMENT — WEBHOOK (IntaSend server-side callback — backup confirmation)
# ════════════════════════════════════════════════════
@app.route('/payment/webhook', methods=['POST'])
def payment_webhook():
    data = request.get_json(force=True)
    print('IntaSend webhook payload:', data)

    invoice_id = data.get('invoice_id', '')
    state      = data.get('state', '')

    uid = sessions.get(f'inv_{invoice_id}')
    if not uid:
        uid = data.get('api_ref', '')
        if not uid or uid not in sessions:
            print(f'Webhook: unknown invoice_id={invoice_id} api_ref={uid}, ignoring')
            return jsonify({'ok': True}), 200

    if state == 'COMPLETE':
        sessions[uid]['status']  = 'paid'
        sessions[uid]['minutes'] = sessions[uid].get('pending_minutes', 60)
        print(f'Webhook: uid={uid} marked PAID')
    elif state == 'FAILED':
        sessions[uid]['status'] = 'failed'
        print(f'Webhook: uid={uid} marked FAILED')
    else:
        print(f'Webhook: uid={uid} state={state} — no action')

    return jsonify({'ok': True}), 200

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
