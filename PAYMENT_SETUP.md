# IntaSend Payment Setup

## Your keys go in server/.env:
  INTASEND_API_KEY=your-publishable-key
  INTASEND_SECRET=your-secret-key

## Where to find them:
  https://app.intasend.com → Settings → API Keys

## How the flow works:
  1. Extension calls POST /payment/initiate
  2. Server creates an IntaSend hosted checkout session
  3. Server returns the checkout URL
  4. Extension opens the URL in a new Chrome tab
  5. User pays via M-Pesa or card on IntaSend's page
  6. IntaSend calls POST /payment/webhook on your server
  7. Server marks session as paid
  8. Extension polls GET /payment/status every 5s → gets 'paid' → unlocks

## For M-Pesa STK Push (no redirect, phone prompt instead):
  IntaSend supports direct STK push — see their docs at:
  https://developers.intasend.com/docs/mpesa-stk-push
  Add a phone number field to paywall.html and call the STK endpoint instead.

## Testing without real payments:
  Leave INTASEND_API_KEY empty in .env
  Server auto-confirms after 8 seconds (dev mode)

## Going live:
  1. Deploy server (Railway, Render, or Fly.io — all have free tiers)
  2. Set BASE_URL in .env to your live domain e.g. https://myserver.railway.app
  3. Update SERVER = '...' in extension/paywall.js and extension/session.js
  4. Update manifest.json host_permissions and CSP with your live domain
