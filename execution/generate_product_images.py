"""
generate_product_images.py
==========================
Takes generic product images and generates e-commerce-ready variants
using Google Gemini image generation (text+image → image editing).

Usage:
    python execution/generate_product_images.py                          # process all in input/
    python execution/generate_product_images.py --input input/bag.jpg    # single image
    python execution/generate_product_images.py --variants 5             # generate 5 variants
    python execution/generate_product_images.py --dry-run                # preview only
    python execution/generate_product_images.py --force                  # regenerate existing

Requires:
    GEMINI_API_KEY in .env
"""

import os
import sys
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
from google import genai
from google.genai import types
from PIL import Image
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# Variant definitions: (name, prompt_template)
# {product} is replaced at runtime with a description from the AI or the filename
VARIANT_PROMPTS = [
    (
        "hero",
        (
            "Take this product and place it perfectly centered on a pure white "
            "background. Professional studio lighting, soft even shadows, "
            "e-commerce hero shot. The product should fill about 80% of the "
            "frame. Clean, crisp, high-quality product photography."
        ),
    ),
    (
        "angled",
        (
            "Take this product and show it from a 30-45 degree angle on a clean "
            "white or very light gradient background. Subtle drop shadow for "
            "depth. Professional product photography angle that shows the "
            "product's shape and dimension. Crisp, studio-quality lighting."
        ),
    ),
    (
        "lifestyle",
        (
            "Take this product and place it in an elegant, aspirational lifestyle "
            "setting. For example: on a marble countertop, styled wooden table, "
            "or in a tasteful room environment. The product should be the clear "
            "focal point but the setting should feel premium and editorial. "
            "Natural, warm lighting. High-end e-commerce lifestyle photography."
        ),
    ),
    (
        "detail",
        (
            "Take this product and create a close-up detail shot highlighting "
            "its key features, textures, materials, or craftsmanship. Focus on "
            "what makes this product premium — the stitching, finish, label, "
            "or unique design elements. Macro-style product photography on a "
            "clean background with professional studio lighting."
        ),
    ),
    (
        "group",
        (
            "Take this product and show it alongside complementary items that "
            "provide scale reference and context. Style it as a flat-lay or "
            "curated arrangement. The main product should be the hero of the "
            "composition. Clean, editorial product photography with soft, "
            "even lighting."
        ),
    ),
]

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

# Resolve paths relative to project root (one level up from execution/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = PROJECT_ROOT / "input"
OUTPUT_DIR = PROJECT_ROOT / "output"
TMP_DIR = PROJECT_ROOT / ".tmp"
ENV_PATH = PROJECT_ROOT / ".env"

# Load environment variables
load_dotenv(ENV_PATH)


def setup_logging() -> logging.Logger:
    """Configure logging to console + file."""
    TMP_DIR.mkdir(exist_ok=True)
    log_file = TMP_DIR / "generation_errors.log"

    logger = logging.getLogger("product_images")
    logger.setLevel(logging.DEBUG)

    # Console handler — INFO level
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S"))

    # File handler — DEBUG level (captures everything)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s"))

    logger.addHandler(ch)
    logger.addHandler(fh)
    return logger


log = setup_logging()

# ---------------------------------------------------------------------------
# Gemini Client
# ---------------------------------------------------------------------------


def get_client() -> genai.Client:
    """Initialize Gemini client with API key from .env."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        log.error("GEMINI_API_KEY not found in .env — please add it and retry.")
        sys.exit(1)
    # Use a longer timeout for image generation (default is too short)
    return genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=120_000),  # 120 seconds
    )


# ---------------------------------------------------------------------------
# Image Generation
# ---------------------------------------------------------------------------

MODEL = "gemini-2.5-flash-image"


def generate_variant(
    client: genai.Client,
    source_image: Image.Image,
    variant_name: str,
    prompt: str,
    output_path: Path,
    max_retries: int = 3,
) -> bool:
    """
    Generate a single image variant using Gemini.

    Returns True on success, False on failure.
    """
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=[prompt, source_image],
                config=types.GenerateContentConfig(
                    response_modalities=["Image"],
                ),
            )

            # Extract the image from the response
            if response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                for part in response.candidates[0].content.parts:
                    if part.inline_data is not None:
                        generated = part.as_image()
                        generated.save(str(output_path), quality=95)
                        log.info(f"  ✓ {variant_name} saved → {output_path.name}")
                        return True

            log.warning(f"  ✗ {variant_name} — no image in response (attempt {attempt}/{max_retries})")

        except Exception as e:
            error_str = str(e)

            # Fail fast on authentication errors
            if "API_KEY_INVALID" in error_str or "API key not valid" in error_str:
                log.error(f"  ✗ {variant_name} — INVALID API KEY. Please check GEMINI_API_KEY in .env")
                log.error("  Stopping — fix your API key and re-run.")
                sys.exit(1)

            # Fail fast on quota/billing errors (free tier has 0 quota for image gen)
            if "RESOURCE_EXHAUSTED" in error_str or "Quota exceeded" in error_str:
                log.error(f"  ✗ {variant_name} — QUOTA EXHAUSTED.")
                log.error("")
                log.error("  The Gemini free tier does NOT support image generation.")
                log.error("  You need to enable billing on your Google Cloud project:")
                log.error("    1. Go to https://aistudio.google.com/apikey")
                log.error("    2. Click on your API key")
                log.error("    3. Enable billing / upgrade to a paid plan")
                log.error("    4. Re-run this script")
                log.error("")
                log.error("  Alternatively, set GEMINI_API_KEY to a key from a billing-enabled project.")
                sys.exit(1)

            wait_time = 2 ** attempt  # Exponential backoff: 2s, 4s, 8s
            log.warning(f"  ✗ {variant_name} — error: {e} (attempt {attempt}/{max_retries}, retry in {wait_time}s)")
            if attempt < max_retries:
                time.sleep(wait_time)

    log.error(f"  ✗ {variant_name} — FAILED after {max_retries} attempts")
    return False


def process_product(
    client: genai.Client,
    image_path: Path,
    num_variants: int = 3,
    force: bool = False,
) -> dict:
    """
    Process a single product image and generate all variants.

    Returns a dict with results: {"success": [...], "failed": [...], "skipped": [...]}
    """
    product_name = image_path.stem
    product_output_dir = OUTPUT_DIR / product_name
    results = {"success": [], "failed": [], "skipped": []}

    # Check if already processed (resume support)
    if not force and product_output_dir.exists():
        existing = list(product_output_dir.glob("*.png"))
        if len(existing) >= num_variants:
            log.info(f"⏭  {product_name} — already has {len(existing)} images, skipping (use --force to regenerate)")
            results["skipped"] = [v[0] for v in VARIANT_PROMPTS[:num_variants]]
            return results

    # Create output directory
    product_output_dir.mkdir(parents=True, exist_ok=True)

    # Load source image
    try:
        source_image = Image.open(image_path)
        # Convert to RGB if needed (removes alpha channel issues)
        if source_image.mode in ("RGBA", "P"):
            source_image = source_image.convert("RGB")
    except Exception as e:
        log.error(f"✗  {product_name} — cannot open image: {e}")
        results["failed"] = [v[0] for v in VARIANT_PROMPTS[:num_variants]]
        return results

    log.info(f"🔄  Processing: {product_name} ({source_image.size[0]}×{source_image.size[1]})")

    # Generate each variant
    variants_to_generate = VARIANT_PROMPTS[:num_variants]

    for variant_name, prompt in variants_to_generate:
        output_path = product_output_dir / f"{variant_name}.png"

        # Skip if this variant already exists and not forcing
        if not force and output_path.exists():
            log.info(f"  ⏭ {variant_name} — already exists, skipping")
            results["skipped"].append(variant_name)
            continue

        success = generate_variant(client, source_image, variant_name, prompt, output_path)

        if success:
            results["success"].append(variant_name)
        else:
            results["failed"].append(variant_name)

        # Rate limit delay between calls (avoid hammering the API)
        time.sleep(2)

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def discover_images(input_path: str | None = None) -> list[Path]:
    """Find all product images to process."""
    if input_path:
        p = Path(input_path)
        if not p.exists():
            log.error(f"Input path does not exist: {p}")
            sys.exit(1)
        if p.is_file():
            if p.suffix.lower() in SUPPORTED_EXTENSIONS:
                return [p]
            else:
                log.error(f"Unsupported file type: {p.suffix}")
                sys.exit(1)
        elif p.is_dir():
            return sorted([f for f in p.iterdir() if f.suffix.lower() in SUPPORTED_EXTENSIONS])
    else:
        # Default: scan input/ directory
        if not INPUT_DIR.exists():
            INPUT_DIR.mkdir(parents=True, exist_ok=True)
            log.info(f"Created input directory: {INPUT_DIR}")
            log.info("Place your product images in this folder and run again.")
            return []
        return sorted([f for f in INPUT_DIR.iterdir() if f.suffix.lower() in SUPPORTED_EXTENSIONS])


def main():
    parser = argparse.ArgumentParser(
        description="Generate e-commerce-ready product images using AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python execution/generate_product_images.py                        # process all in input/
  python execution/generate_product_images.py --input input/bag.jpg  # single image
  python execution/generate_product_images.py --variants 5           # generate 5 variants
  python execution/generate_product_images.py --dry-run              # preview only
  python execution/generate_product_images.py --force                # regenerate existing
        """,
    )
    parser.add_argument("--input", type=str, default=None, help="Path to a specific image or folder (default: input/)")
    parser.add_argument("--variants", type=int, default=3, choices=[3, 4, 5], help="Number of variants per product (default: 3)")
    parser.add_argument("--force", action="store_true", help="Regenerate even if outputs already exist")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be processed without generating")
    args = parser.parse_args()

    # Header
    log.info("=" * 60)
    log.info("  Product Image Generator — E-Commerce Ready")
    log.info(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"  Variants per product: {args.variants}")
    log.info(f"  Force regenerate: {args.force}")
    log.info("=" * 60)

    # Discover images
    images = discover_images(args.input)

    if not images:
        log.info("No images found to process. Place product images in the input/ folder.")
        return

    log.info(f"Found {len(images)} product image(s) to process")
    log.info("")

    # Dry-run mode
    if args.dry_run:
        log.info("DRY RUN — no images will be generated:")
        for img in images:
            product_name = img.stem
            product_output_dir = OUTPUT_DIR / product_name
            existing = list(product_output_dir.glob("*.png")) if product_output_dir.exists() else []
            status = f"({len(existing)} existing)" if existing else "(new)"
            log.info(f"  • {img.name} → output/{product_name}/ {status}")
            for variant_name, _ in VARIANT_PROMPTS[: args.variants]:
                variant_file = product_output_dir / f"{variant_name}.png"
                exists = "✓ exists" if variant_file.exists() else "○ will generate"
                log.info(f"      {variant_name}: {exists}")
        log.info("")
        log.info(f"Total API calls needed: up to {len(images) * args.variants}")
        return

    # Initialize Gemini client
    client = get_client()

    # Process all images
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    total_success = 0
    total_failed = 0
    total_skipped = 0

    for image_path in tqdm(images, desc="Products", unit="product"):
        results = process_product(client, image_path, num_variants=args.variants, force=args.force)
        total_success += len(results["success"])
        total_failed += len(results["failed"])
        total_skipped += len(results["skipped"])
        log.info("")

    # Summary
    log.info("=" * 60)
    log.info("  COMPLETE")
    log.info(f"  Products processed: {len(images)}")
    log.info(f"  Images generated:   {total_success}")
    log.info(f"  Images skipped:     {total_skipped}")
    log.info(f"  Images failed:      {total_failed}")
    log.info(f"  Output directory:   {OUTPUT_DIR}")
    log.info("=" * 60)

    if total_failed > 0:
        log.warning(f"⚠  {total_failed} image(s) failed. Check .tmp/generation_errors.log for details.")


if __name__ == "__main__":
    main()
