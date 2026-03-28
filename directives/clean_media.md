# Clean Media — Metadata Strip + Anti-Fingerprint

## Goal
Take branded creative assets (images and videos) and produce clean variants that platforms treat as unique, original content. Strips all metadata and applies imperceptible visual/audio modifications to defeat perceptual hashing.

## When to Use
- Reposting winning creatives across different ad accounts
- Cross-posting branded content to different platform pages
- Any time the same visual asset needs to appear "fresh" to algorithm detection

## Inputs
- Folder of images (JPG, PNG, WebP) and/or videos (MP4, MOV)
- Typically 10-50 files per batch

## Script
`execution/clean_media.py`

## Usage
```bash
# Basic — clean all media in a folder
python execution/clean_media.py --input ./ads --output ./cleaned

# Generate 3 unique variants per file (for 3 different accounts)
python execution/clean_media.py --input ./ads --output ./cleaned --copies 3

# Images only
python execution/clean_media.py --input ./ads --output ./cleaned --images-only

# Videos only
python execution/clean_media.py --input ./ads --output ./cleaned --videos-only
```

## Output
- Cleaned files in the output folder
- Same filenames as source (or `_v1`, `_v2` etc. when using --copies)
- Visually identical to originals — imperceptible changes only
- Each variant is unique (different random transforms)

## What It Does

### Images
1. Strips all EXIF, XMP, IPTC metadata
2. Preserves ICC color profile (colors stay accurate)
3. Adds imperceptible pixel noise (5% of pixels, +/- 2 values)
4. Micro-crops 2-4px from edges, resizes back (breaks perceptual hash)
5. Slight brightness/saturation shift (+/- 1%)
6. Re-saves with randomized compression
7. Randomizes file timestamps

### Videos
1. Strips all container metadata (title, encoder, creation time, etc.)
2. Re-encodes H.264 at matched quality (CRF +/- 2)
3. Applies subtle color filter (brightness/contrast/saturation)
4. Micro-crops 2-4px from edges, scales back to original resolution
5. Re-encodes audio AAC with imperceptible tempo shift (0.1%)
6. Randomizes file timestamps

## Dependencies
- **Pillow** — installed (image processing)
- **tqdm** — installed (progress bar)
- **FFmpeg** — must be on PATH for video processing
  - Install: `winget install ffmpeg`
  - Script gracefully skips videos if FFmpeg is missing

## Edge Cases
- Corrupt files are logged and skipped
- RGBA images preserve alpha channel
- Videos without audio are handled (no audio re-encode)
- Very short videos (<1s) skip the crop filter
- MOV files are re-encoded to MP4 container

## Logs
`.tmp/clean_media.log`
