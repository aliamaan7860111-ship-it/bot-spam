"""
clean_media.py
==============
Strips metadata and defeats perceptual hashing on images and videos.
Makes imperceptible random modifications so each output generates a
unique visual/audio fingerprint while remaining visually identical.

Usage:
    python execution/clean_media.py --input ./ads --output ./cleaned
    python execution/clean_media.py --input ./ads --output ./cleaned --copies 3
    python execution/clean_media.py --input ./ads --output ./cleaned --images-only
    python execution/clean_media.py --input ./ads --output ./cleaned --videos-only

Requires:
    Pillow (images), FFmpeg on PATH (videos), tqdm (progress)
"""

import os
import sys
import json
import random
import time
import argparse
import logging
import subprocess
from pathlib import Path
from datetime import datetime

from PIL import Image, ImageEnhance, PngImagePlugin
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TMP_DIR = PROJECT_ROOT / ".tmp"
LOG_FILE = TMP_DIR / "clean_media.log"

SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov"}

# Image processing parameters
NOISE_INTENSITY = 2          # +/- pixel value range per channel
NOISE_COVERAGE = 0.05        # fraction of pixels to modify (5%)
CROP_MIN_PX = 2              # minimum pixels to crop per edge
CROP_MAX_PX = 4              # maximum pixels to crop per edge
BRIGHTNESS_SHIFT = 0.01      # +/- 1% max
SATURATION_SHIFT = 0.01      # +/- 1% max
JPEG_QUALITY_MIN = 92
JPEG_QUALITY_MAX = 96

# Video processing parameters
VIDEO_BRIGHTNESS_RANGE = 0.02
VIDEO_CONTRAST_RANGE = 0.02
VIDEO_SATURATION_MIN = 0.99
VIDEO_SATURATION_MAX = 1.01
VIDEO_CROP_MIN_PX = 2
VIDEO_CROP_MAX_PX = 4
AUDIO_TEMPO_OPTIONS = [0.999, 1.001]

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

TMP_DIR.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("clean_media")
log.setLevel(logging.DEBUG)

# Console handler
_ch = logging.StreamHandler()
_ch.setLevel(logging.INFO)
_ch.setFormatter(logging.Formatter("%(message)s"))
log.addHandler(_ch)

# File handler
_fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s"))
log.addHandler(_fh)

# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------

def discover_files(input_path, images_only=False, videos_only=False):
    """Scan input_path (file or directory) and return matching media files."""
    path = Path(input_path)
    if not path.exists():
        log.error(f"Input path does not exist: {input_path}")
        return []

    all_extensions = set()
    if not videos_only:
        all_extensions |= SUPPORTED_IMAGE_EXTENSIONS
    if not images_only:
        all_extensions |= SUPPORTED_VIDEO_EXTENSIONS

    if path.is_file():
        if path.suffix.lower() in all_extensions:
            return [path]
        log.warning(f"Unsupported file type: {path.suffix}")
        return []

    files = sorted(
        f for f in path.iterdir()
        if f.is_file() and f.suffix.lower() in all_extensions
    )
    return files


def randomize_timestamp(file_path):
    """Set random access/modification times within the last 30 days."""
    now = time.time()
    random_time = now - random.randint(0, 86400 * 30)
    os.utime(file_path, (random_time, random_time))


def check_ffmpeg():
    """Return True if ffmpeg and ffprobe are available on PATH."""
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["ffprobe", "-version"],
            capture_output=True, timeout=10,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def get_video_info(file_path):
    """
    Use ffprobe to extract video metadata.
    Returns dict with width, height, duration, fps, codecs, bitrates, has_audio.
    """
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(file_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr}")

    data = json.loads(result.stdout)

    video_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "video"),
        None,
    )
    audio_stream = next(
        (s for s in data.get("streams", []) if s.get("codec_type") == "audio"),
        None,
    )

    if video_stream is None:
        raise RuntimeError(f"No video stream found in {file_path.name}")

    # Safe fps parsing (avoid eval)
    fps_str = video_stream.get("r_frame_rate", "30/1")
    if "/" in fps_str:
        num, den = fps_str.split("/")
        fps = int(num) / max(int(den), 1)
    else:
        fps = float(fps_str)

    return {
        "width": int(video_stream["width"]),
        "height": int(video_stream["height"]),
        "duration": float(data.get("format", {}).get("duration", 0)),
        "fps": fps,
        "video_codec": video_stream.get("codec_name", "h264"),
        "video_bitrate": int(video_stream["bit_rate"]) if video_stream.get("bit_rate") else None,
        "has_audio": audio_stream is not None,
        "audio_codec": audio_stream["codec_name"] if audio_stream else None,
        "audio_bitrate": int(audio_stream["bit_rate"]) if audio_stream and audio_stream.get("bit_rate") else 128000,
    }


def generate_output_filename(source_path, copy_index, total_copies):
    """Generate output filename. Multi-copy: insert _v{n} before extension."""
    if total_copies == 1:
        return source_path.name
    return f"{source_path.stem}_v{copy_index}{source_path.suffix}"


# ---------------------------------------------------------------------------
# Image Processing
# ---------------------------------------------------------------------------

def extract_icc_profile(img):
    """Extract ICC color profile from image if present."""
    return img.info.get("icc_profile", None)


def apply_pixel_noise(img, intensity=NOISE_INTENSITY, coverage=NOISE_COVERAGE):
    """
    Add imperceptible random noise to a fraction of pixels.
    Modifies RGB channels by +/- intensity, leaves alpha untouched.
    """
    pixels = img.load()
    w, h = img.size
    has_alpha = img.mode == "RGBA"
    num_pixels = int(w * h * coverage)

    for _ in range(num_pixels):
        x = random.randint(0, w - 1)
        y = random.randint(0, h - 1)
        pixel = pixels[x, y]

        if has_alpha:
            r, g, b, a = pixel
            pixels[x, y] = (
                max(0, min(255, r + random.randint(-intensity, intensity))),
                max(0, min(255, g + random.randint(-intensity, intensity))),
                max(0, min(255, b + random.randint(-intensity, intensity))),
                a,
            )
        else:
            r, g, b = pixel
            pixels[x, y] = (
                max(0, min(255, r + random.randint(-intensity, intensity))),
                max(0, min(255, g + random.randint(-intensity, intensity))),
                max(0, min(255, b + random.randint(-intensity, intensity))),
            )

    return img


def apply_random_crop_resize(img, min_px=CROP_MIN_PX, max_px=CROP_MAX_PX):
    """
    Crop random 2-4px from each edge, resize back to original dimensions.
    Shifts all pixel positions — most effective at breaking perceptual hash.
    """
    orig_w, orig_h = img.size
    left = random.randint(min_px, max_px)
    top = random.randint(min_px, max_px)
    right = random.randint(min_px, max_px)
    bottom = random.randint(min_px, max_px)

    cropped = img.crop((left, top, orig_w - right, orig_h - bottom))
    return cropped.resize((orig_w, orig_h), Image.Resampling.LANCZOS)


def apply_color_shifts(img):
    """Apply subtle random brightness and saturation shifts (+/- 1%)."""
    brightness_factor = 1.0 + random.uniform(-BRIGHTNESS_SHIFT, BRIGHTNESS_SHIFT)
    img = ImageEnhance.Brightness(img).enhance(brightness_factor)

    saturation_factor = 1.0 + random.uniform(-SATURATION_SHIFT, SATURATION_SHIFT)
    img = ImageEnhance.Color(img).enhance(saturation_factor)

    return img


def save_clean_image(img, output_path, original_format, icc_profile, has_alpha):
    """Save image with clean metadata in the appropriate format."""
    ext = output_path.suffix.lower()

    if ext in (".jpg", ".jpeg") or original_format == "JPEG":
        save_img = img.convert("RGB") if has_alpha else img
        quality = random.randint(JPEG_QUALITY_MIN, JPEG_QUALITY_MAX)
        save_kwargs = {"format": "JPEG", "quality": quality, "optimize": True}
        if icc_profile:
            save_kwargs["icc_profile"] = icc_profile
        save_img.save(output_path, **save_kwargs)

    elif ext == ".png" or original_format == "PNG":
        pnginfo = PngImagePlugin.PngInfo()  # empty = no text metadata
        compress_level = random.randint(6, 9)
        save_kwargs = {
            "format": "PNG",
            "pnginfo": pnginfo,
            "compress_level": compress_level,
        }
        if icc_profile:
            save_kwargs["icc_profile"] = icc_profile
        img.save(output_path, **save_kwargs)

    elif ext == ".webp" or original_format == "WEBP":
        quality = random.randint(90, 95)
        save_kwargs = {"format": "WEBP", "quality": quality, "method": 4}
        if icc_profile:
            save_kwargs["icc_profile"] = icc_profile
        img.save(output_path, **save_kwargs)

    else:
        # Fallback: save as JPEG
        save_img = img.convert("RGB") if has_alpha else img
        save_img.save(output_path, format="JPEG", quality=94, optimize=True)


def process_single_image(input_path, output_path):
    """
    Full image pipeline: strip metadata, noise, crop, color shift, save clean.
    Returns True on success, False on failure.
    """
    try:
        img = Image.open(input_path)
        original_format = img.format
        icc_profile = extract_icc_profile(img)

        # Normalize mode
        has_alpha = img.mode in ("RGBA", "LA", "PA")
        if img.mode == "P":
            img = img.convert("RGBA" if "transparency" in img.info else "RGB")
        elif img.mode == "L":
            img = img.convert("RGB")
        elif img.mode == "LA":
            img = img.convert("RGBA")
        elif img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
            has_alpha = False

        # Pipeline
        img = apply_pixel_noise(img)
        img = apply_random_crop_resize(img)
        img = apply_color_shifts(img)

        # Save
        save_clean_image(img, output_path, original_format, icc_profile, has_alpha)
        randomize_timestamp(output_path)

        log.debug(f"  [OK] {input_path.name} -> {output_path.name}")
        return True

    except Exception as e:
        log.error(f"  [FAIL] {input_path.name}: {e}")
        return False


# ---------------------------------------------------------------------------
# Video Processing
# ---------------------------------------------------------------------------

def estimate_crf_from_bitrate(bitrate, width, height):
    """
    Estimate CRF from video bitrate and resolution.
    Falls back to CRF 23 if bitrate unknown.
    """
    if bitrate is None:
        return 23

    # Normalize to 1080p equivalent bitrate
    pixels = width * height
    ref_pixels = 1920 * 1080
    normalized_bitrate = bitrate * (ref_pixels / max(pixels, 1))

    if normalized_bitrate > 8_000_000:
        return 18
    elif normalized_bitrate > 5_000_000:
        return 20
    elif normalized_bitrate > 3_000_000:
        return 22
    elif normalized_bitrate > 1_500_000:
        return 25
    else:
        return 28


def build_ffmpeg_command(input_path, output_path, info, skip_crop=False):
    """
    Build the full ffmpeg command with randomized filters.
    Each call generates fresh random values for uniqueness.
    """
    # Random visual parameters
    brightness = random.uniform(-VIDEO_BRIGHTNESS_RANGE, VIDEO_BRIGHTNESS_RANGE)
    contrast = random.uniform(1.0 - VIDEO_CONTRAST_RANGE, 1.0 + VIDEO_CONTRAST_RANGE)
    saturation = random.uniform(VIDEO_SATURATION_MIN, VIDEO_SATURATION_MAX)

    # CRF with offset
    base_crf = estimate_crf_from_bitrate(info["video_bitrate"], info["width"], info["height"])
    crf = base_crf + random.choice([-2, -1, 1, 2])
    crf = max(16, min(28, crf))

    # Video filter chain
    vf_parts = [
        f"eq=brightness={brightness:.4f}:contrast={contrast:.4f}:saturation={saturation:.4f}",
    ]

    if not skip_crop:
        crop_l = random.randint(VIDEO_CROP_MIN_PX, VIDEO_CROP_MAX_PX)
        crop_t = random.randint(VIDEO_CROP_MIN_PX, VIDEO_CROP_MAX_PX)
        crop_r = random.randint(VIDEO_CROP_MIN_PX, VIDEO_CROP_MAX_PX)
        crop_b = random.randint(VIDEO_CROP_MIN_PX, VIDEO_CROP_MAX_PX)
        crop_w = info["width"] - crop_l - crop_r
        crop_h = info["height"] - crop_t - crop_b
        vf_parts.append(f"crop={crop_w}:{crop_h}:{crop_l}:{crop_t}")
        vf_parts.append(f"scale={info['width']}:{info['height']}:flags=lanczos")

    vf = ",".join(vf_parts)

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(input_path),
        "-map_metadata", "-1",
        "-fflags", "+bitexact",
        "-vf", vf,
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]

    # Audio handling
    if info["has_audio"]:
        tempo = random.choice(AUDIO_TEMPO_OPTIONS)
        audio_bitrate_k = f"{info['audio_bitrate'] // 1000}k"
        cmd += [
            "-af", f"atempo={tempo}",
            "-c:a", "aac",
            "-b:a", audio_bitrate_k,
        ]
    else:
        cmd += ["-an"]

    cmd.append(str(output_path))
    return cmd


def process_single_video(input_path, output_path):
    """
    Full video pipeline: probe, strip metadata, re-encode with randomized filters.
    Returns True on success, False on failure.
    """
    try:
        info = get_video_info(input_path)
    except RuntimeError as e:
        log.error(f"  [FAIL] {input_path.name}: {e}")
        return False

    # Very short videos: skip crop to avoid filter issues
    skip_crop = info["duration"] < 1.0
    if skip_crop:
        log.warning(f"  {input_path.name}: very short ({info['duration']:.1f}s), skipping crop")

    cmd = build_ffmpeg_command(input_path, output_path, info, skip_crop=skip_crop)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=1800,  # 30 min timeout for large videos
        )
        if result.returncode != 0:
            # Show last 300 chars of stderr for debugging
            log.error(f"  [FAIL] {input_path.name}: FFmpeg error")
            log.debug(f"  FFmpeg stderr: {result.stderr[-300:]}")
            return False

        # Verify output
        if not output_path.exists() or output_path.stat().st_size == 0:
            log.error(f"  [FAIL] {input_path.name}: FFmpeg produced empty output")
            return False

        randomize_timestamp(output_path)
        log.debug(f"  [OK] {input_path.name} -> {output_path.name}")
        return True

    except subprocess.TimeoutExpired:
        log.error(f"  [FAIL] {input_path.name}: FFmpeg timed out (10 min)")
        return False
    except FileNotFoundError:
        log.error("FFmpeg not found on PATH. Install FFmpeg and try again.")
        return False


# ---------------------------------------------------------------------------
# Batch Orchestration
# ---------------------------------------------------------------------------

def process_batch(files, output_dir, copies=1):
    """Process all files with progress bar. Returns results dict."""
    output_dir.mkdir(parents=True, exist_ok=True)

    results = {
        "images_ok": 0,
        "images_fail": 0,
        "videos_ok": 0,
        "videos_fail": 0,
        "output_paths": [],
        "errors": [],
    }

    total_tasks = len(files) * copies
    ffmpeg_available = check_ffmpeg()

    with tqdm(total=total_tasks, desc="Processing", unit="file") as pbar:
        for source_file in files:
            ext = source_file.suffix.lower()
            is_image = ext in SUPPORTED_IMAGE_EXTENSIONS
            is_video = ext in SUPPORTED_VIDEO_EXTENSIONS

            # Skip videos if FFmpeg not installed
            if is_video and not ffmpeg_available:
                log.warning(f"  Skipping {source_file.name} (FFmpeg not installed)")
                pbar.update(copies)
                results["videos_fail"] += copies
                continue

            for copy_idx in range(1, copies + 1):
                out_name = generate_output_filename(source_file, copy_idx, copies)
                output_path = output_dir / out_name

                if is_image:
                    ok = process_single_image(source_file, output_path)
                    if ok:
                        results["images_ok"] += 1
                        results["output_paths"].append(str(output_path))
                    else:
                        results["images_fail"] += 1
                        results["errors"].append(str(source_file))

                elif is_video:
                    ok = process_single_video(source_file, output_path)
                    if ok:
                        results["videos_ok"] += 1
                        results["output_paths"].append(str(output_path))
                    else:
                        results["videos_fail"] += 1
                        results["errors"].append(str(source_file))

                pbar.update(1)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Strip metadata and defeat perceptual hashing on images/videos",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python execution/clean_media.py --input ./ads --output ./cleaned
  python execution/clean_media.py --input ./ads --output ./cleaned --copies 3
  python execution/clean_media.py --input ./ads --output ./cleaned --images-only
  python execution/clean_media.py --input ./ads --output ./cleaned --videos-only
        """,
    )
    parser.add_argument("--input", required=True, help="Input file or folder")
    parser.add_argument("--output", required=True, help="Output folder")
    parser.add_argument("--copies", type=int, default=1,
                        help="Unique variants per source file (default: 1)")
    parser.add_argument("--images-only", action="store_true",
                        help="Process only images")
    parser.add_argument("--videos-only", action="store_true",
                        help="Process only videos")
    args = parser.parse_args()

    # Validation
    if args.images_only and args.videos_only:
        parser.error("Cannot use --images-only and --videos-only together")
    if args.copies < 1:
        parser.error("--copies must be >= 1")

    # Check FFmpeg
    has_ffmpeg = check_ffmpeg()
    if not args.images_only and not has_ffmpeg:
        log.warning("FFmpeg not found on PATH. Video files will be skipped.")
        log.warning("Install: winget install ffmpeg  OR  https://ffmpeg.org/download.html")

    # Discover files
    files = discover_files(args.input, args.images_only, args.videos_only)
    if not files:
        log.info("No supported media files found.")
        return

    image_count = sum(1 for f in files if f.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS)
    video_count = sum(1 for f in files if f.suffix.lower() in SUPPORTED_VIDEO_EXTENSIONS)

    # Header
    log.info("=" * 60)
    log.info("  Media Cleaner - Metadata Strip + Hash Defeat")
    log.info(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"  Input:  {args.input}")
    log.info(f"  Output: {args.output}")
    log.info(f"  Files:  {image_count} images, {video_count} videos")
    log.info(f"  Copies: {args.copies} per source ({len(files) * args.copies} total)")
    log.info("=" * 60)

    # Process
    results = process_batch(files, Path(args.output), args.copies)

    # Summary
    log.info("")
    log.info("=" * 60)
    log.info("  COMPLETE")
    log.info(f"  Images:  {results['images_ok']} ok / {results['images_fail']} failed")
    log.info(f"  Videos:  {results['videos_ok']} ok / {results['videos_fail']} failed")
    log.info(f"  Output:  {args.output}")
    log.info(f"  Total:   {len(results['output_paths'])} files")
    log.info("=" * 60)

    if results["errors"]:
        log.warning(f"  {len(results['errors'])} file(s) failed. See {LOG_FILE}")


if __name__ == "__main__":
    main()
