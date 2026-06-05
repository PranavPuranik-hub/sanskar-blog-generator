from dotenv import load_dotenv
load_dotenv()

import requests
import ast
import re
import os
import time
import json
import io

# ── API Keys ──────────────────────────────────────────────────────────────────
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
if not GROQ_API_KEY:
    raise RuntimeError("GROQ_API_KEY environment variable is not set.")

# ── Google Drive Service Account ─────────────────────────────────────────────
# Path to your downloaded service account JSON key file
SERVICE_ACCOUNT_FILE = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "service_account.json")
# ─────────────────────────────────────────────────────────────────────────────

# ── Model config ──────────────────────────────────────────────────────────────
BLOG_MODEL    = "openai/gpt-oss-120b"
# Lightweight model for short text utility tasks (keywords etc.)
UTILITY_MODEL = "llama-3.1-8b-instant"
# Vision model for photo relevance screening — Scout supports base64 image input on Groq
VISION_MODEL  = "meta-llama/llama-4-scout-17b-16e-instruct"
# Max images sent per vision API call (Groq Scout limit)
VISION_BATCH  = 2
# ─────────────────────────────────────────────────────────────────────────────

# Cache with TTL
_cached_images    = None
_cache_timestamp  = 0
CACHE_TTL_SECONDS = 1800  # 30 minutes
_used_photo_urls  = set()
_drive_folder_id  = None   # last used folder id


def call_groq(prompt, max_tokens=2500, temperature=0.8, model=None):
    if model is None:
        model = BLOG_MODEL
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature
        },
        timeout=60
    )
    if response.status_code == 200:
        return response.json()["choices"][0]["message"]["content"]
    raise Exception(f"Groq error {response.status_code}: {response.text[:300]}")





# ── Google Drive Service Account ─────────────────────────────────────────────

def _get_drive_service():
    """Build an authenticated Google Drive service using the service account."""
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        raise FileNotFoundError(
            f"Service account file not found: {SERVICE_ACCOUNT_FILE}\n"
            "Set GOOGLE_SERVICE_ACCOUNT_FILE env var to the correct path."
        )

    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/drive.readonly"]
    )
    return build("drive", "v3", credentials=creds)


def _folder_id_from_url(url: str) -> str:
    """Extract Google Drive folder ID from a sharing URL."""
    # Handles:
    #   https://drive.google.com/drive/folders/FOLDER_ID
    #   https://drive.google.com/drive/folders/FOLDER_ID?usp=sharing
    m = re.search(r"/folders/([a-zA-Z0-9_\-]{10,})", url)
    if m:
        return m.group(1)
    # Also handle plain IDs passed directly
    if re.match(r"^[a-zA-Z0-9_\-]{20,}$", url):
        return url
    raise ValueError(f"Cannot parse folder ID from URL: {url}")


def fetch_drive_photos(folder_url: str):
    """
    Fetch image files from a Google Drive folder using the service account.
    The folder must be shared with the service account email.
    Returns list of {real_url, real_thumb, name, alt} dicts (real_url = Drive file ID reference).
    """
    global _cached_images, _cache_timestamp, _drive_folder_id

    folder_id = _folder_id_from_url(folder_url)

    now = time.time()
    if (
        _cached_images is not None
        and (now - _cache_timestamp) < CACHE_TTL_SECONDS
        and _drive_folder_id == folder_id
    ):
        age = int(now - _cache_timestamp)
        print(f"=== Drive cache: {len(_cached_images)} photos (age {age}s) ===")
        return _cached_images

    print(f"=== Fetching Drive folder: {folder_id} ===")
    try:
        service = _get_drive_service()
        results = service.files().list(
            q=f"'{folder_id}' in parents and mimeType contains 'image/' and trashed=false",
            fields="files(id, name, mimeType)",
            pageSize=100
        ).execute()

        files = results.get("files", [])
        print(f"Drive files found: {len(files)}")

        images = []
        for f in files:
            file_id = f["id"]
            images.append({
                "real_url":   f"drive:{file_id}",   # internal reference, never sent to browser
                "real_thumb": f"drive:{file_id}",
                "name": f["name"],
                "alt":  f["name"].rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title()
            })

        _cached_images   = images
        _cache_timestamp = time.time()
        _drive_folder_id = folder_id
        return images

    except Exception as e:
        print(f"Drive fetch error: {e}")
        import traceback; traceback.print_exc()
        return []


def fetch_drive_file_bytes(file_id: str) -> tuple[bytes, str]:
    """Stream a Drive file's bytes and content-type via service account."""
    from googleapiclient.http import MediaIoBaseDownload
    service = _get_drive_service()

    # Get metadata for MIME type
    meta = service.files().get(fileId=file_id, fields="mimeType,name").execute()
    mime = meta.get("mimeType", "image/jpeg")

    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue(), mime


# ── Photo proxy helpers ───────────────────────────────────────────────────────

def proxy_url(real_url: str) -> str:
    """Convert a real URL or drive:FILE_ID to a server-side proxy path."""
    import urllib.parse
    return f"/photo-proxy?url={urllib.parse.quote(real_url, safe='')}"


def _resize_to_thumbnail(data: bytes, mime: str, max_px: int = 400) -> tuple[bytes, str]:
    """
    Resize image bytes to a small thumbnail for vision API calls.
    Falls back to original bytes if Pillow is not installed.
    Always returns JPEG to keep payload size small.
    """
    try:
        from PIL import Image
        import io as _io
        img = Image.open(_io.BytesIO(data))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        ratio = max_px / max(img.width, img.height)
        if ratio < 1:
            img = img.resize(
                (int(img.width * ratio), int(img.height * ratio)),
                Image.LANCZOS
            )
        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=55)
        return buf.getvalue(), "image/jpeg"
    except ImportError:
        # Pillow not installed — sending original bytes (install Pillow to enable resize)
        return data, mime
    except Exception as e:
        print(f"Thumbnail resize failed ({e}), sending original")
        return data, mime


def _vision_screen_batch(batch: list[dict], blog_context: str) -> list[int]:
    """
    Send up to VISION_BATCH images to LLaMA 4 Scout and ask which ones
    are visually relevant to the blog topic.
    Returns a list of batch-local indices that are relevant.
    batch items: {"idx": original_index, "file_id": str, "name": str}
    """
    import base64

    content = [
        {
            "type": "text",
            "text": (
                f"You are selecting photos for an NGO blog post.\n"
                f"Blog topic: {blog_context}\n\n"
                f"I will show you {len(batch)} photos numbered 0 to {len(batch)-1}. "
                f"For each photo, decide if it is RELEVANT to the blog topic above "
                f"(shows people, activities, or scenes related to the event). "
                f"Ignore photos that show unrelated scenes (broken roads, random objects, etc.).\n"
                f"Return ONLY a Python list of the relevant photo numbers. "
                f"Example: [0, 2] or [] if none are relevant."
            )
        }
    ]

    loaded = []
    for pos, item in enumerate(batch):
        file_id = item["file_id"]
        try:
            data, mime = fetch_drive_file_bytes(file_id)
            thumb, tmime = _resize_to_thumbnail(data, mime)
            b64 = base64.b64encode(thumb).decode()
            content.append({"type": "text", "text": f"Photo {pos} (filename: {item['name']}):"})
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{tmime};base64,{b64}"}
            })
            loaded.append(pos)
        except Exception as e:
            print(f"Could not load photo {item['name']}: {e}")

    if not loaded:
        return []

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": VISION_MODEL,
                "messages": [{"role": "user", "content": content}],
                "max_tokens": 60,
                "temperature": 0.1
            },
            timeout=60
        )
        if response.status_code != 200:
            print(f"Vision API error {response.status_code}: {response.text[:200]}")
            return loaded  # treat all as relevant on error

        raw = response.json()["choices"][0]["message"]["content"]
        print(f"Vision response: {raw.strip()}")
        local_indices = ast.literal_eval(raw[raw.index("["):raw.index("]")+1])
        return [i for i in local_indices if isinstance(i, int) and 0 <= i < len(batch)]

    except Exception as e:
        print(f"Vision screening failed ({e}), treating all as relevant")
        return loaded


def pick_best_photos(all_images, blog_text, keywords):
    """
    Select photos by VISUALLY screening them with LLaMA 4 Scout.
    Processes photos in batches of VISION_BATCH, discards irrelevant ones,
    and stops as soon as max_pick relevant photos are found.
    Falls back to spread selection if vision screening fails entirely.
    """
    global _used_photo_urls

    fresh = [img for img in all_images if img["real_url"] not in _used_photo_urls]
    print(f"Fresh photos: {len(fresh)} of {len(all_images)}")

    if len(fresh) < 2:
        print("Resetting photo tracker")
        _used_photo_urls = set()
        fresh = list(all_images)

    if not fresh:
        return []

    photo_markers = blog_text.count('[PHOTO]')
    max_pick = min(max(photo_markers, 1), 4, len(fresh))

    if len(fresh) <= max_pick:
        selected = fresh
    else:
        blog_context = f"Keywords: {keywords}. Blog excerpt: {blog_text[:250]}"

        # Build a spread sample — scan up to 5 batches (25 photos) before giving up
        max_scan = min(len(fresh), VISION_BATCH * 5)
        step = max(1, len(fresh) // max_scan)
        candidates = [fresh[i * step] for i in range(min(max_scan, len(fresh)))]

        selected = []
        i = 0
        while i < len(candidates) and len(selected) < max_pick:
            batch_imgs = candidates[i: i + VISION_BATCH]
            batch = [
                {
                    "idx": i + pos,
                    "file_id": img["real_url"][6:],   # strip "drive:"
                    "name": img["name"]
                }
                for pos, img in enumerate(batch_imgs)
            ]
            print(f"Vision screening batch {i//VISION_BATCH + 1}: {[b['name'] for b in batch]}")
            relevant_local = _vision_screen_batch(batch, blog_context)
            for local_idx in relevant_local:
                if len(selected) < max_pick:
                    selected.append(batch_imgs[local_idx])
            i += VISION_BATCH

        if not selected:
            print("Vision found nothing relevant — falling back to spread selection")
            step2    = max(1, len(fresh) // max_pick)
            selected = [fresh[i2 * step2] for i2 in range(min(max_pick, len(fresh)))]

    for img in selected:
        _used_photo_urls.add(img["real_url"])

    print(f"Selected {len(selected)} photos")
    return [
        {
            "url":   proxy_url(img["real_url"]),
            "thumb": proxy_url(img["real_thumb"]),
            "name":  img["name"],
            "alt":   img["alt"]
        }
        for img in selected
    ]


# ── Blog generation ───────────────────────────────────────────────────────────

def generate_blog(user_input: str, folder_url: str = ""):
    prompt = f"""You are the storytelling blog writer for Sanskar Foundation, an Indian NGO.

Write like a human who lived through this event and cannot stop thinking about it.
Make the reader feel something real — not read a report.

Study these 3 real Sanskar blogs carefully — absorb their EXACT tone, depth and style:

--- EXAMPLE 1 (Scholarship — deep, story-driven, emotional) ---
Title: From Hope to Scholarship Access
Subtitle: From Hope to Higher Education: How One Scholarship Became a Promise for Many

*There are some journeys that begin not with grand plans, but with a simple question — What if someone's dream ends only because they cannot afford it?*

That question stayed with us.

At Sanskar MKFoundation, we have always believed that education is not just a privilege; it is a right. Yet every day, there are brilliant students forced to pause their studies because of financial struggles. We kept thinking about those silent battles — students hiding their worries behind smiles, parents sacrificing their needs so their children could study, and dreams waiting for just one chance to survive.

That is how our Scholarship Program began.

**The Beginning of a Purpose**

Sometimes, change starts quietly. No stage, no spotlight — just a conversation among people who cared. Our objective was clear: to provide financial support to deserving students, to encourage them to continue, to promote equal access to opportunities. Simple goals — but powerful enough to transform lives.

**The Student Behind the Scholarship**

Every initiative becomes meaningful when it reaches the right hands. For us, that moment came with Kritika Joshi — a student from Garden Valley Public School, Uttarakhand. A total of ₹30,000 was distributed to support her education.

> Behind this number was a mother's relief.
> A student's confidence.
> A family's hope.

--- EXAMPLE 2 (Diwali — warm, festive, community) ---
LIGHTING LIVES ON DIWALI | SANSKAR FOUNDATIONS
GIVING IS CELEBRATING | 19th October 2024

Most of you reading this don't realize how fortunate you are — to have steady food, a shelter, loving parents. Many children and elderly don't have access to these basics. On the 19th of October, Sanskar Foundation celebrated Diwali with the children of Brzee Foundation.

MOTIVE AND PURPOSE
We believe giving IS celebrating. All our volunteers spent a joyful day engaging and celebrating with the children.

OVERVIEW OF THE EVENT
Refreshment Distribution — Volunteers distributed snacks and stationery.
Drawing Competition — Encouraged creativity and self-expression.
Dinner Provision — A nutritious meal closed the day beautifully.

SUCCESS HIGHLIGHTS
Engagement & Participation — Children enthusiastically joined every activity.
Heartfelt Gratitude — Caregivers expressed their deep appreciation.
Memorable Experience — The event reinforced the power of togetherness.

--- EXAMPLE 3 (Labour Day — respectful, human) ---
From Sweat to Success: Recognizing the Invisible Workforce

"Sometimes we need to recognize the relentless efforts of those who aid our journey, because kindness is happiness."

Labour Day is a moment to honor workers. On May 1st 2024, Sanskar Foundation took initiative to appreciate the non-teaching staff at Acharya Institutes.

The team distributed fruits and juices. Volunteers spent time listening to their stories — not just handing things out, but actually connecting.

Impact: Around 25-30 staff members — their smiles said everything.

---

NOW write a blog for the INPUT below.

Write the blog as you wish by the given input but it should be enaging,emotional and story-driven, and should be like human.
Use the examples above as a style guide, but do NOT copy their structure or headings.
Create your own unique flow and section titles that fit the new event.

LENGTH: Natural to the story. Rich event = longer. Simple = concise. Around 500-700 words.

PHOTOS: Insert [PHOTO] at natural visual moments only — where a photo would genuinely add emotion.
No forced photo placement. 1 photo minimum, up to 4 maximum, only where they belong.

FORMAT:
[Title — compelling, not generic]
[Subtitle — one line that makes them want to read]

*[Opening italic line — the hook]*

[2-3 short paragraphs building into the story]

[PHOTO]

[Section heading — INVENTED for this event, not copied from examples]
[Story continues...]

[PHOTO if needed]

[Section heading — INVENTED for this event]
[Story continues...]

[PHOTO if needed]

[Warm closing — 2-3 sentences]

Join Us in Making a Difference!!
Volunteer with Us: [1-2 genuine sentences]
Make a Donation: [1-2 genuine sentences]
Stay Connected: [1 sentence]

Contact Us: Instagram, YouTube, LinkedIn, Sanskar Registration Form
Contact Number: 7983923205, 8306708175

INPUT:
{user_input}
"""

    print(f"=== Generating blog with {BLOG_MODEL} ===")

    # Single-model generation — LLaMA 4 Maverick is strong enough on its own
    blog = call_groq(prompt, max_tokens=2500, temperature=0.85)
    if not blog:
        raise Exception("Model returned empty response — check Groq API key and quota.")

    print(f"Final blog: {len(blog.split())} words | [PHOTO]: {blog.count('[PHOTO]')}")

    # Keywords via lightweight model
    try:
        kw_raw = call_groq(
            f'Extract 3-4 keywords from this NGO blog. Return ONLY a Python list.\nBLOG: {blog[:300]}',
            max_tokens=60, temperature=0.2, model=UTILITY_MODEL
        )
        keywords = ast.literal_eval(kw_raw[kw_raw.index("["):kw_raw.index("]")+1])
    except Exception:
        keywords = ["event", "volunteers", "NGO"]
    print(f"Keywords: {keywords}")

    # Photos from Google Drive if folder URL provided
    all_images = []
    if folder_url and folder_url.strip():
        all_images = fetch_drive_photos(folder_url.strip())

    selected = pick_best_photos(all_images, blog, keywords) if all_images else []

    return {
        "blog":         blog,
        "images":       selected,
        "keywords":     keywords,
        "total_photos": len(all_images),
        "photo_count":  len(selected)
    }