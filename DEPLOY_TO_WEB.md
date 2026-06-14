# Deploy World Cup 2026 Dashboard to the Web

Your app will be live at a URL like **https://wc2026-dashboard.onrender.com** that anyone can open.
Free tier. No credit card needed.

---

## Step 1 — Put your files on GitHub

1. Go to **https://github.com** and sign in (or create a free account).
2. Click **+** (top right) → **New repository**.
   - Name it: `wc2026-dashboard`
   - Set to **Public**
   - Click **Create repository**
3. On your computer, open **Command Prompt** in the `wc2026` folder and run:

```
git init
git add .
git commit -m "World Cup 2026 dashboard"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/wc2026-dashboard.git
git push -u origin main
```

> Replace `YOUR_USERNAME` with your actual GitHub username.

---

## Step 2 — Deploy on Render

1. Go to **https://render.com** and sign in with your GitHub account.
2. Click **New +** → **Web Service**.
3. Select your `wc2026-dashboard` repository → click **Connect**.
4. Render auto-detects the settings from `render.yaml`. Just confirm:
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python app.py`
5. Click **Create Web Service**.

Render will build and deploy your app (takes ~2 minutes the first time).

---

## Step 3 — Share the link!

Once deployed, Render gives you a URL like:
```
https://wc2026-dashboard.onrender.com
```

Send that link to your friends — it works on any device, any browser, worldwide.

---

## Notes

- **Free tier spin-down**: On Render's free plan, the app "sleeps" after 15 minutes of inactivity. The first visit after sleeping takes ~30 seconds to wake up. Subsequent visits are instant.
- **Keeping it awake**: If you want it always-on during the tournament, upgrade to Render's Starter plan ($7/mo) or use a free uptime monitor like https://uptimerobot.com to ping it every 10 minutes.
- **Your local app still works**: The batch file shortcut on your desktop is unchanged — it still runs the local version.
- **Updates**: To push new code, just run `git add . && git commit -m "update" && git push` — Render redeploys automatically.
