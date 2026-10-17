# Snapchat Memories Metadata Fixer

Fix the metadata (dates) on your exported Snapchat Memories so they sort correctly in Google Photos, Apple Photos, and other apps.

## The Problem

When you export your Snapchat data, the photos and videos lose their original timestamps. This tool restores the correct dates by:

1. Matching each file to its entry in `memories_history.json` to recover the exact capture time and GPS location
2. Embedding EXIF `DateTimeOriginal` (with timezone offset) and GPS into JPG images
3. Embedding `creation_time` and location into MP4/MOV videos (requires [ffmpeg](https://ffmpeg.org/), included in the Docker image; no re-encoding)
4. Setting the file system modified date

## Quick Start

### Option 1: Docker (Recommended)

1. [Request your Snapchat data](https://accounts.snapchat.com/accounts/downloadmydata) and download the export
2. Extract the zip and place the folder in `./data/`
3. Set `TZ` in `docker-compose.yml` to your timezone (e.g. `Europe/Stockholm`)
4. Run:

```bash
docker compose up --build
```

Your fixed memories will be in `./output/`

### Option 2: Python

1. Install dependencies (and optionally [ffmpeg](https://ffmpeg.org/download.html) to fix video metadata):

```bash
pip install -r requirements.txt
```

2. Run the script:

```bash
# Auto-discovery (place your Snapchat folder in the repo directory)
python main.py

# Or specify paths manually
python main.py --input /path/to/memories --json /path/to/memories_history.json --output /path/to/output
```

## Command Line Options

| Option | Description |
|--------|-------------|
| `--input` | Path to the folder containing your media files |
| `--json` | Path to the `memories_history.json` file |
| `--output` | Path to save the fixed files |
| `--timezone` | Timezone for embedded dates, e.g. `Europe/Stockholm` (default: system timezone) |
| `--verbose` | Enable detailed logging |

## Folder Structure

Your Snapchat export should look like this:

```
your-snapchat-data/
├── json/
│   └── memories_history.json
└── memories/
    ├── 2022-10-04_<uuid>-main.jpg
    ├── 2022-10-04_<uuid>-overlay.png
    ├── 2022-10-05_<uuid>-main.mp4
    └── ...
```

Files named like `2022-10-04 18:33:11 UTC.jpg` (older exports) are also supported.

At the end of a run the script prints how many files got an exact time from the JSON. Files that couldn't be matched still get the correct date (but not time).

## License

See [LICENSE](LICENSE) for details.