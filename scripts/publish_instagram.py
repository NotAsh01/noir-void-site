import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_VERSION = "v27.0"
API_ROOT = f"https://graph.facebook.com/{API_VERSION}"
QUEUE_PATH = Path("queue.json")


def request(method, url, data=None):
    encoded = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=encoded, method=method)
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Meta API returned HTTP {exc.code}: {detail}") from exc


def publish(media_url, caption, media_type, user_id, token):
    media_type = (media_type or "IMAGE").upper()
    if not media_url.startswith("https://"):
        raise ValueError("Media URL must be a publicly accessible HTTPS URL.")
    if media_type not in {"IMAGE", "REELS"}:
        raise ValueError("Only IMAGE and REELS are supported by this starter workflow.")

    params = {"caption": caption, "access_token": token}
    if media_type == "IMAGE":
        params["image_url"] = media_url
    else:
        params.update({"media_type": "REELS", "video_url": media_url, "share_to_feed": "true"})

    container = request("POST", f"{API_ROOT}/{user_id}/media", params)
    container_id = container.get("id")
    if not container_id:
        raise RuntimeError(f"Meta did not return a media container ID: {container}")

    # Wait for Meta to finish processing the media before publishing.
    for _ in range(20):
        status = request(
            "GET",
            f"{API_ROOT}/{container_id}?fields=status_code&access_token={urllib.parse.quote(token)}"
        )
        code = status.get("status_code")
        if code == "FINISHED":
            break
        if code == "ERROR":
            raise RuntimeError(f"Meta could not process the media: {status}")
        time.sleep(6)
    else:
        raise RuntimeError("Media processing did not finish within 2 minutes.")

    result = request(
        "POST",
        f"{API_ROOT}/{user_id}/media_publish",
        {"creation_id": container_id, "access_token": token},
    )
    published_id = result.get("id")
    if not published_id:
        raise RuntimeError(f"Meta did not confirm publication: {result}")
    print(f"Instagram post published. Media ID: {published_id}")


def main():
    token = os.environ.get("META_ACCESS_TOKEN", "").strip()
    user_id = os.environ.get("INSTAGRAM_USER_ID", "").strip()
    if not token or not user_id:
        raise RuntimeError("Missing META_ACCESS_TOKEN or INSTAGRAM_USER_ID GitHub secret.")

    event = os.environ.get("EVENT_NAME", "")
    if event == "workflow_dispatch":
        media_url = os.environ.get("MEDIA_URL", "").strip()
        caption = os.environ.get("CAPTION", "")
        media_type = os.environ.get("MEDIA_TYPE", "IMAGE").strip()
        if not media_url or not caption:
            raise ValueError("Manual publishing requires both media_url and caption.")
        publish(media_url, caption, media_type, user_id, token)
        return

    queue = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
    posts = queue.get("posts", [])
    if not posts:
        print("Queue is empty. Nothing to publish today.")
        return

    post = posts[0]
    publish(
        post["media_url"],
        post.get("caption", ""),
        post.get("media_type", "IMAGE"),
        user_id,
        token,
    )
    # Remove the item only after Meta confirms it was published.
    queue["posts"] = posts[1:]
    QUEUE_PATH.write_text(json.dumps(queue, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Publishing failed: {error}", file=sys.stderr)
        sys.exit(1)
