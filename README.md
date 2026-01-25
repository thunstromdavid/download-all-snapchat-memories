# Snapchat Memories Metadata Fixer

Fix the metadata (dates) on your exported Snapchat Memories so they sort correctly in Google Photos, Apple Photos, and other apps.

## The Problem

When you export your Snapchat data, the photos and videos lose their original timestamps. This tool restores the correct dates by:

1. Setting the file system modified date
2. Embedding EXIF `DateTimeOriginal` into JPG images

## Quick Start

### Option 1: Docker (Recommended)

1. [Request your Snapchat data](https://accounts.snapchat.com/accounts/downloadmydata) and download the export
2. Extract the zip and place the folder in `./data/`
3. Run:

```bash
docker compose up --build
```

Your fixed memories will be in `./data/fixed_memories/`

### Option 2: Python

1. Install dependencies:

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
| `--verbose` | Enable detailed logging |

## Folder Structure

Your Snapchat export should look like this:

```
your-snapchat-data/
├── json/
│   └── memories_history.json
└── memories/
    ├── 2022-10-04 18:33:11 UTC.jpg
    ├── 2022-10-05 12:00:00 UTC.mp4
    └── ...
```

## License

See [LICENSE](LICENSE) for details.