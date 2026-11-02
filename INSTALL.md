# ClipForge Installation Guide

### 1. Prerequisites (What to Install)

#### Windows (Command Line via winget):
Open PowerShell as Administrator and run:
```powershell
# Core prerequisites
winget install -e --id Python.Python.3.11
winget install -e --id Git.Git
winget install -e --id Gyan.FFmpeg

# Optional (for local AI language models)
winget install -e --id Ollama.Ollama
```
*(Alternatively, download installers manually: [Python 3.10+](https://www.python.org/downloads/) - check "Add to PATH", [Git](https://git-scm.com/), [FFmpeg](https://ffmpeg.org/download.html), and [Ollama](https://ollama.com/download)).*

---

#### Linux (Ubuntu / Debian):
```bash
sudo apt update
sudo apt install -y ffmpeg python3 python3-venv python3-pip git curl fonts-liberation fonts-dejavu-core

# Optional (for local AI language models)
curl -fsSL https://ollama.com/install.sh | sh
```

---

### 2. Clone the Repository

```bash
git clone https://github.com/yourusername/clipforge.git
cd clipforge
```

---

### 3. Create & Activate Virtual Environment

#### On Windows (PowerShell / Command Prompt):
```powershell
python -m venv .venv
.venv\Scripts\activate
```

#### On Linux / macOS:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

---

### 4. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

#### (Optional) NVIDIA GPU Acceleration (CUDA for faster-whisper):
If your machine or server has an NVIDIA GPU, install the CUDA runtime libraries to speed up transcription by 5–10x:
```bash
pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"
```

---

### 5. Run ClipForge

#### Standard Launch:
```bash
python main.py
```

#### On Linux (Automated Script):
On Linux servers, you can also use `start.sh`, which automatically checks dependencies, creates the `.venv`, installs packages, configures CUDA paths, and runs the server:
```bash
chmod +x start.sh
./start.sh
```

#### Running on a Remote Server / VPS:
Just run the app:
```bash
python main.py
```
*(or `./start.sh`)*

Then open `http://your-server-ip:8000` in your browser.

---

### 6. (Optional) Run with Docker

ClipForge includes a `Dockerfile` for containerized setups:
```bash
# Build the image
docker build -t clipforge .

# Run the container (persisting data in /data)
docker run -d -p 8000:8000 -v clipforge_data:/data --name clipforge clipforge
```

---

### 7. Open in Browser

Open your browser and navigate to:
**`http://localhost:8000`**

