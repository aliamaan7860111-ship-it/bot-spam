# Directive: Product Image Generation for E-Commerce

## Goal
Take generic product images and generate 3–5 e-commerce-ready image variants per product using AI image generation (Google Gemini).

## Inputs
- A folder of source product images (`input/`) in `.jpg`, `.png`, or `.webp` format
- Each filename is treated as the product name (e.g., `leather-bag-001.jpg` → product name `leather-bag-001`)

## Image Variants to Generate

| # | Name       | Prompt Intent                                                                 |
|---|------------|-------------------------------------------------------------------------------|
| 1 | `hero`     | Product centered on pure white background, well-lit, studio-quality           |
| 2 | `angled`   | Product at 30–45° angle, soft drop shadow, white/gradient background          |
| 3 | `lifestyle`| Product in contextual setting (e.g., perfume on counter, bag on styled table) |
| 4 | `detail`   | Close-up of key feature/texture (stitching, label, nozzle, clasp)            |
| 5 | `group`    | Product with complementary items for scale/context                           |

**Minimum 3** variants (hero, angled, lifestyle). Images 4 and 5 are optional and controlled by the `--variants` flag.

## Execution Script
`execution/generate_product_images.py`

### Usage
```bash
# Process all images in input/
python execution/generate_product_images.py

# Process specific image
python execution/generate_product_images.py --input "input/leather-bag-001.jpg"

# Generate 5 variants instead of default 3
python execution/generate_product_images.py --variants 5

# Dry run (show what would be processed)
python execution/generate_product_images.py --dry-run
```

## Output Structure
```
output/
  leather-bag-001/
    hero.png
    angled.png
    lifestyle.png
    detail.png       (if --variants >= 4)
    group.png        (if --variants >= 5)
  perfume-xyz/
    hero.png
    angled.png
    lifestyle.png
```

## Edge Cases & Notes
- **Resume support**: Script skips products whose output folder already has the required number of images. Use `--force` to regenerate.
- **Rate limits**: Gemini API has rate limits. Script uses delays between requests and retries with exponential backoff.
- **Transparent/glass products**: The AI handles these but results may vary. Review generated outputs for quality.
- **API cost**: Each image generation call costs tokens. Estimate ~5 calls × 700 products = 3,500 API calls. Monitor usage.
- **Error handling**: Failed generations are logged to `.tmp/generation_errors.log`. The script continues processing remaining products.

## Environment Variables (`.env`)
```
GEMINI_API_KEY=your_gemini_api_key_here
```
