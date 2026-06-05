# 📝 Sanskar NGO Blog Generator

An AI-powered blog generator for **Sanskar Foundation** — paste raw event notes, get a fully formatted, photo-illustrated blog post in seconds. Powered by [Groq](https://groq.com) LLMs and Google Drive integration.

---

## ✨ Features

- **AI Blog Writing** — Generates emotional, story-driven NGO blogs using GPT-OSS 120B on Groq
- **Smart Photo Selection** — Uses a vision model (LLaMA 4 Scout) to pick only contextually relevant photos from your Google Drive folder
- **Secure Photo Proxy** — All Drive images are served through a server-side proxy; no credentials are ever exposed to the browser
- **Multiple Export Formats** — Download as `.txt`, `.html` (self-contained with embedded images), or `.docx` (Word document)
- **Rich Text Copy** — Copy the blog with formatting and images directly into Google Docs or Word
- **Rate Limiting** — Built-in IP-based rate limiter (10 requests/minute)

---

## 🗂️ Project Structure

```
BLOG GENRATOR 12/
├── bloggen/                # (package folder)
├── node_modules/           # Node.js dependencies (auto-generated, not committed)
├── app.py                  # FastAPI server — routes, proxy, DOCX export, frontend HTML
├── generate.py             # AI logic — blog generation, Drive fetching, photo selection
├── requirements.txt        # Python dependencies
├── package.json            # Node metadata (if using any JS tooling)
├── package-lock.json       # Node lock file (not committed — see .gitignore)
├── .env                    # ⚠️ Environment variables — DO NOT commit
├── .gitignore              # Git ignore rules
├── service_account.json    # ⚠️ Google Service Account key — DO NOT commit
└── README.md
```

> **Never commit** `.env` and `service_account.json` — they contain your API keys and Google credentials. Both are already covered in `.gitignore`.

---

## ⚙️ Setup

### 1. Clone the repo

```bash
https://github.com/PranavPuranik-hub/sanskar-blog-generator.git
cd sanskar-blog-generator
```

### 2. Create a virtual environment

```bash
python -m venv venv
source venv/bin/activate        # macOS/Linux
venv\Scripts\activate           # Windows
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Set up environment variables

Create a `.env` file in the project root:

```env
GROQ_API_KEY=your_groq_api_key_here
GOOGLE_SERVICE_ACCOUNT_FILE=Your Service account.
```

- Get your Groq API key at [console.groq.com](https://console.groq.com)
- See the **Google Drive Setup** section below for the service account

### 5. Run the server

```bash
uvicorn app:app --reload
```

Open [http://localhost:8000](http://localhost:8000) in your browser.

---

## 🔑 Google Drive Setup

To use photos from a Google Drive folder:

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (or use an existing one)
3. Enable the **Google Drive API**
4. Go to **IAM & Admin → Service Accounts** → Create a service account
5. Download the JSON key file and save it as `service_account.json` in the project root
6. **Share your Drive photo folder** with the service account email (found in the JSON as `client_email`) — give it **Viewer** access

Then paste the Drive folder's sharing URL into the app's "Drive Folder URL" field.

---

## 📡 API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serves the web UI |
| `POST` | `/generate` | Generate a blog from event notes |
| `GET` | `/photo-proxy?url=...` | Proxy server for Drive/Google Photos images |
| `POST` | `/export/docx` | Export blog + images as a Word document |

### `/generate` Request Body

```json
{
  "text": "Event notes describing what happened...",
  "folder_url": "https://drive.google.com/drive/folders/FOLDER_ID"
}
```

### `/generate` Response

```json
{
  "blog": "Full blog text with [PHOTO] markers...",
  "images": [
    { "url": "/photo-proxy?url=drive%3AFILE_ID", "thumb": "...", "name": "photo.jpg", "alt": "Photo Alt" }
  ],
  "keywords": ["education", "scholarship", "students"],
  "total_photos": 25,
  "photo_count": 3
}
```

---


## 🔒 Security Notes

- `service_account.json` and `.env` are **never committed** to Git (see `.gitignore`)
- The `/photo-proxy` endpoint validates file IDs strictly — no path traversal, only Drive file IDs
- Drive credentials live only on the server; the browser only ever sees `/photo-proxy` URLs

---

## 📦 Dependencies

| Package | Purpose |
|---|---|
| `fastapi` | Web framework |
| `uvicorn` | ASGI server |
| `requests` | HTTP calls to Groq API |
| `python-docx` | Word document generation |
| `google-api-python-client` | Google Drive API |
| `google-auth` | Service account authentication |
| `python-dotenv` | Load `.env` file |
| `pydantic` | Request/response validation |

---

## 🤝 Contributing

1. Fork the repo
2. Create a branch: `git checkout -b feature/your-feature`
3. Commit changes: `git commit -m "Add your feature"`
4. Push: `git push origin feature/your-feature`
5. Open a Pull Request

---

## 📄 License

MIT License — free to use and modify for Sanskar Foundation and similar NGO projects.
