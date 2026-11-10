# ClipForge

Turn long videos into short, captioned clips, from your browser. Runs on your own server with local models, so there are no per-minute fees.

Paste YouTube links (or upload a file). ClipForge downloads the video, transcribes it, asks a language model to find the strongest moments, and lets you pick, trim and render them as vertical clips with burned-in captions. Finished clips live in a Library where you can download them or upload them to YouTube.

## What you need

- A Linux server (Ubuntu is easiest). An NVIDIA GPU with 16 GB or more of VRAM is recommended. A CPU-only server works but is slow.
- Python 3.10 or newer, and `ffmpeg`
- [Ollama](https://ollama.com) for the language model (or any OpenAI-compatible API instead)

```bash
sudo apt install ffmpeg python3-venv
curl -fsSL https://ollama.com/install.sh | sh
```

## Start it

```bash
unzip clipforge.zip && cd clipforge
./start.sh
```

The first run installs the Python packages. Then open **http://localhost:8120**.

**Using a remote server?**
Simply open **`http://your-server-ip:8120`** in your browser. (Optional: you can also set `CLIPFORGE_PASSWORD='your-password'` if you want to add password protection, or tunnel via `ssh -L 8120:localhost:8120 user@your-server`).

## First run, all from the browser

1. **Models page.** Download a Whisper model (`large-v3-turbo` is a good default on a GPU) and a language model. Pick one of the recommended models, or type any tag from ollama.com/library. Downloads run on the server and show live progress. You can close the tab.
2. Under **Which model does what**, choose the model for the first pass and the model for ranking. If you only have one, use it for both.
3. **New clips page.** Paste one or more YouTube links, one per line, and press Find clips.
4. When it finishes, open the video. Tick the clips you want, adjust start and end times if you like (Preview plays the exact range, "Use playhead" copies the current position), choose the shape and framing, and press Render.
5. **Library.** Play, download, delete, or upload to YouTube.

## Suggested models

| Your GPU | First pass | Ranking |
|---|---|---|
| 16 GB | `qwen3:8b` | `gpt-oss:20b` or `qwen3:14b` |
| 24 GB | `qwen3:8b` | `qwen3:30b`, `qwen3:32b` or `gemma3:27b` |

Model names and quality change quickly. Treat this as a starting point and compare two or three models on a video whose best moments you already know.

## Settings

Everything is on the Settings page and saved on the server in `data/settings.json`. Secrets (API keys, cookies, the Google client secret) are write-only: after you save them they are never sent back to the browser.

- **Language model.** Ollama address, or any OpenAI-compatible API (Groq, OpenRouter, Gemini's compatibility endpoint, LM Studio, vLLM). Temperature, context size.
- **Choosing clips.** Shortest and longest length, how many suggestions, section length.
- **Rendering.** Default shape (9:16, 1:1, 4:5, 16:9), framing, encoder (CPU or NVIDIA NVENC), quality.
- **Captions.** Font, size, colours, words per line, position, with a live preview.
- **Downloading.** Maximum resolution, and a cookies box (see Troubleshooting).
- **Storage.** The folder where rendered clips are saved.

## Uploading to YouTube

You need your own free Google OAuth keys:

1. In Google Cloud Console, create a project and enable **YouTube Data API v3**.
2. Configure the OAuth consent screen (External) and add your Google account as a test user.
3. Create an OAuth client ID of type **Web application**. Add the redirect URI shown on the Settings page, exactly: `http://localhost:8120/api/youtube/callback`.
4. Paste the client ID and secret into Settings, press **Connect YouTube**, and approve access.

Things to know:

- Google only allows a plain `http` redirect for `localhost`, so connect through an SSH tunnel if the server is remote.
- While your Google project is in Testing mode, the sign-in expires after 7 days and you reconnect.
- YouTube keeps uploads from unverified API projects **private** until the project passes Google's audit. You can still switch a video to public by hand in YouTube Studio.
- Each upload costs 1,600 of the default 10,000 daily quota units, so about 6 uploads a day.

## Uploading to TikTok (Sandbox & Multi-Account)

ClipForge supports uploading directly or via **Inbox Draft Mode** using TikTok's Content Posting API v2:

1. In the **TikTok Developer Portal** (`developers.tiktok.com`), create an app and open the **Sandbox** tab (next to Production). No app review or audit is required.
2. Under Products, add **Login Kit** and **Content Posting API**. Leave **Upload to TikTok** enabled (this powers Inbox Draft mode).
3. Under Login Kit, register your exact HTTPS callback URL (e.g. `https://your-tunnel-url/api/tiktok/callback`).
4. Under **Roles & Access (Target Users)**, add up to 10 TikTok accounts you own and accept the developer terms on each account.
5. In ClipForge Settings -> TikTok Accounts, click **+ Add TikTok Account / API**.
6. Use the **same Sandbox Client Key & Secret** across all your TikTok accounts. Name each account so you can tell them apart.
7. Click **Connect TikTok** for each row while logged into that account to save its authentication token.
8. **Why Inbox Mode?** Unaudited apps cannot post public videos directly. Inbox mode sends the clip directly into your TikTok app inbox as an editable draft. You receive a notification on your phone and tap Post with full public visibility!

## Where things are stored

```
data/settings.json    your settings
data/clipforge.db     videos and clips
data/tokens/          OAuth tokens for named YouTube and TikTok accounts
data/videos/<id>/     downloaded source, audio, transcript
data/models/whisper/  speech models
data/clips/           rendered clips (or the folder you chose in Settings)
```

Set `CLIPFORGE_DATA=/some/path` to keep all of this somewhere else.

## Run it as a service

```ini
# /etc/systemd/system/clipforge.service
[Unit]
Description=ClipForge
After=network.target ollama.service

[Service]
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/clipforge
ExecStart=/home/YOUR_USER/clipforge/start.sh
Environment=CLIPFORGE_PASSWORD=change-me
Environment=CLIPFORGE_HOST=0.0.0.0
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## Troubleshooting

- **"Sign in to confirm you're not a bot" when downloading.** YouTube often blocks server IP addresses. Export a `cookies.txt` from a browser where you are logged in (a "cookies.txt" browser extension does it) and paste it into Settings, Downloading. Keep yt-dlp current: `.venv/bin/pip install -U yt-dlp`.
- **Transcription is very slow or says it fell back to the CPU.** The CUDA 12 libraries are missing. `start.sh` tries to install them. Check `nvidia-smi` works on the server.
- **Out of GPU memory.** Pick a smaller model, lower the context size in Settings, or choose `int8_float16` precision for Whisper. ClipForge runs one job at a time and unloads the language model after each video by default.
- **Long videos get poor suggestions.** Raise the context size in Settings so each section fits in memory, or use a larger ranking model.
- **Captions use the wrong font.** The font must be installed on the server, or put a `.ttf` file in `data/fonts`.
- **A clip starts or ends awkwardly.** Edit the start and end on the review page and render again.

## Known limits

- Framing mode "Follow the speaker" tracks one face at a time (OpenCV's frontal face detector). In a two-person shot it stays with the first face it finds. Use "Fit with blurred background" or "Center crop" for those.
- The language model decides what is a good clip. Small models are hit and miss, so treat the list as a shortlist and use the Preview button.
- Only download and clip content you own or have permission to use.
