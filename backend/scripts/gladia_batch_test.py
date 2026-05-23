"""
Gladia Batch STT Test — Send a pre-recorded audio file for transcription.

Usage:
  python scripts/gladia_batch_test.py path/to/audio.webm
  python scripts/gladia_batch_test.py path/to/audio.wav
  python scripts/gladia_batch_test.py path/to/audio.mp3
"""
import sys
import time
import json
import requests

GLADIA_API_KEY = "6d99892c-2e82-4376-9757-06cbb38915a6"

def transcribe_file(file_path: str) -> dict:
    """Send audio file to Gladia batch transcription API."""

    print(f"\nFile: {file_path}")
    print(f"Size: {round(len(open(file_path, 'rb').read()) / 1024, 1)} KB")

    # Step 1: Upload file
    print("\n[1] Uploading file...")
    t0 = time.time()

    with open(file_path, "rb") as f:
        upload_resp = requests.post(
            "https://api.gladia.io/v2/upload",
            headers={"x-gladia-key": GLADIA_API_KEY},
            files={"audio": (file_path.split("/")[-1], f)},
        )

    if upload_resp.status_code != 200:
        print(f"Upload failed: {upload_resp.status_code} {upload_resp.text}")
        return {}

    upload_data = upload_resp.json()
    audio_url = upload_data.get("audio_url", "")
    upload_ms = int((time.time() - t0) * 1000)
    print(f"   Uploaded in {upload_ms}ms")
    print(f"   URL: {audio_url[:80]}...")

    # Step 2: Request transcription
    print("\n[2] Requesting transcription...")
    t1 = time.time()

    transcribe_resp = requests.post(
        "https://api.gladia.io/v2/transcription",
        headers={
            "x-gladia-key": GLADIA_API_KEY,
            "Content-Type": "application/json",
        },
        json={
            "audio_url": audio_url,
            "language": "en",
        },
    )

    if transcribe_resp.status_code not in (200, 201):
        print(f"Transcription request failed: {transcribe_resp.status_code} {transcribe_resp.text}")
        return {}

    result_data = transcribe_resp.json()
    result_url = result_data.get("result_url", "")
    request_ms = int((time.time() - t1) * 1000)
    print(f"   Requested in {request_ms}ms")
    print(f"   Result URL: {result_url[:80]}...")

    # Step 3: Poll for result
    print("\n[3] Waiting for result...")
    t2 = time.time()

    for attempt in range(60):
        time.sleep(1)
        poll_resp = requests.get(
            result_url,
            headers={"x-gladia-key": GLADIA_API_KEY},
        )

        if poll_resp.status_code != 200:
            print(f"   Poll failed: {poll_resp.status_code}")
            continue

        poll_data = poll_resp.json()
        status = poll_data.get("status", "")

        if status == "done":
            total_ms = int((time.time() - t0) * 1000)
            process_ms = int((time.time() - t2) * 1000)

            # Extract transcript
            utterances = poll_data.get("result", {}).get("transcription", {}).get("utterances", [])
            full_text = poll_data.get("result", {}).get("transcription", {}).get("full_transcript", "")

            if not full_text and utterances:
                full_text = " ".join(u.get("text", "") for u in utterances)

            print(f"   Done in {process_ms}ms (total: {total_ms}ms)")
            print(f"\n{'='*50}")
            print(f"TRANSCRIPT:")
            print(f"{'='*50}")
            print(f"\n{full_text}\n")

            if utterances:
                print(f"{'='*50}")
                print(f"UTTERANCES ({len(utterances)}):")
                print(f"{'='*50}")
                for u in utterances:
                    start = u.get("start", 0)
                    end = u.get("end", 0)
                    text = u.get("text", "")
                    confidence = u.get("confidence", 0)
                    print(f"  [{start:.1f}s - {end:.1f}s] ({confidence:.2f}) {text}")

            print(f"\n{'='*50}")
            print(f"LATENCY BREAKDOWN:")
            print(f"{'='*50}")
            print(f"  Upload:     {upload_ms}ms")
            print(f"  Request:    {request_ms}ms")
            print(f"  Processing: {process_ms}ms")
            print(f"  Total:      {total_ms}ms")

            return poll_data

        elif status == "error":
            print(f"   Transcription error: {poll_data}")
            return {}

        else:
            if attempt % 5 == 0:
                print(f"   Status: {status} (attempt {attempt+1})...")

    print("   Timeout — transcription took too long")
    return {}


def transcribe_bytes(audio_bytes: bytes, filename: str = "audio.webm") -> str:
    """Transcribe raw audio bytes. Returns transcript text."""
    import tempfile
    import os

    tmp_path = None
    try:
        ext = filename.rsplit(".", 1)[-1] if "." in filename else "webm"
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name

        result = transcribe_file(tmp_path)
        return result.get("result", {}).get("transcription", {}).get("full_transcript", "")
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/gladia_batch_test.py <audio_file>")
        print("Supports: .wav, .webm, .mp3, .ogg, .flac")
        sys.exit(1)

    transcribe_file(sys.argv[1])
