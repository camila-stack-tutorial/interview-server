"""
Interview Assistant — Flask Backend
Handles: Claude API proxy, payment session registration, webhook verification
"""

import os, time, json
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests as req

app = Flask(__name__)
CORS(app)

def get_env():
    return {
        'OPENROUTER_API_KEY':       os.environ.get('OPENROUTER_API_KEY', ''),
        'INTASEND_PUBLISHABLE_KEY': os.environ.get('INTASEND_PUBLISHABLE_KEY', ''),
        'INTASEND_SECRET_KEY':      os.environ.get('INTASEND_SECRET_KEY', ''),
        'INTASEND_WEBHOOK_SECRET':  os.environ.get('INTASEND_WEBHOOK_SECRET', ''),
        'IS_TEST':                  os.environ.get('INTASEND_TEST', 'true').lower() == 'true',
    }

# OpenRouter model mapping
# All free tier — swap to paid models when ready
MODELS = {
    'fast':     'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free',
    'balanced': 'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free',
    'best':     'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free',
}

OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions'

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
        'openrouter_key': env['OPENROUTER_API_KEY'][:10]        if env['OPENROUTER_API_KEY']        else 'EMPTY',
        'secret_key':     env['INTASEND_SECRET_KEY'][:10]       if env['INTASEND_SECRET_KEY']       else 'EMPTY',
        'pub_key':        env['INTASEND_PUBLISHABLE_KEY'][:10]  if env['INTASEND_PUBLISHABLE_KEY']  else 'EMPTY',
        'webhook_secret': env['INTASEND_WEBHOOK_SECRET'][:6]    if env['INTASEND_WEBHOOK_SECRET']   else 'EMPTY',
        'is_test':        env['IS_TEST'],
    })

# ════════════════════════════════════════════════════
# ASK — OpenRouter
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

    model = MODELS.get(speed, MODELS['fast'])

    # Build message list: system prompt first, then history, then current question
    messages = [{'role': 'system', 'content': system_prompt}]
    messages += history[-12:]
    messages.append({'role': 'user', 'content': question})

    try:
        response = req.post(
            url=OPENROUTER_URL,
            headers={
                'Authorization': f"Bearer {env['OPENROUTER_API_KEY']}",
                'Content-Type':  'application/json',
            },
            data=json.dumps({
                'model':     model,
                'messages':  messages,
                'reasoning': {'enabled': True}
            }),
            timeout=30
        )
        response.raise_for_status()
        result  = response.json()
        message = result['choices'][0]['message']
        reply   = message.get('content') or ''
        return jsonify({'reply': reply})

    except req.exceptions.Timeout:
        return jsonify({'error': 'Request timed out — please try again'}), 504
    except req.exceptions.RequestException as e:
        return jsonify({'error': f'OpenRouter error: {str(e)}'}), 500
    except (KeyError, IndexError) as e:
        return jsonify({'error': f'Unexpected response format: {str(e)}'}), 500

# ════════════════════════════════════════════════════
# PAYMENT — REGISTER SESSION
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
# PAYMENT — CONFIRM (frontend IntaSend COMPLETE event)
# ════════════════════════════════════════════════════
@app.route('/payment/confirm', methods=['POST'])
def payment_confirm():
    data = request.get_json(force=True)
    uid  = data.get('uid')

    if not uid:
        return jsonify({'error': 'Missing uid'}), 400

    session = sessions.get(uid)
    if not session:
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
# PAYMENT — WEBHOOK (IntaSend server-side backup confirmation)
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
