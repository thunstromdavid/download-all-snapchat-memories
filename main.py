import os
import re
import json
import argparse
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import shutil

# Third-party libraries (installed via requirements.txt)
try:
    import piexif
    from tqdm import tqdm
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:
    print("Missing dependencies. Please run: pip install -r requirements.txt")
    exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

def auto_discover_paths():
    """
    Attempt to automatically find the Snapchat data folder.
    Priorities:
    1. Docker standard path (/data), or a subfolder of it
    2. Current directory (if user unzipped directly in repo)
    3. Any immediate subdirectory (if user dragged a folder into repo)
    """
    candidates = [(Path('/data'), '/output'), (Path('.'), 'output')]

    for base, output in candidates:
        if not base.is_dir():
            continue

        # Check 1: The export was extracted directly into the folder
        if (base / 'json' / 'memories_history.json').exists():
            return str(base / 'memories'), str(base / 'json' / 'memories_history.json'), output

        # Check 2: Immediate subdirectories
        # This handles the case where the user drags "my_snapchat_backup" into the folder
        try:
            for item in sorted(base.iterdir()):
                if item.is_dir():
                    # Check for the specific JSON file that indicates a valid export
                    potential_json = item / 'json' / 'memories_history.json'
                    if potential_json.exists():
                        logger.info(f"Auto-discovered Snapchat data in subfolder: {item}")
                        return str(item / 'memories'), str(potential_json), output
        except Exception as e:
            logger.warning(f"Error during auto-discovery: {e}")

    return None, None, None

def parse_arguments():
    """
    Handle command line arguments.
    Defaults are set to None so we can trigger auto-discovery if args are missing.
    """
    parser = argparse.ArgumentParser(description='Fix Snapchat Memories Metadata')
    
    # Input folder (where the raw images/videos are)
    parser.add_argument('--input', type=str, default=None,
                        help='Path to the folder containing your media files')
    
    # JSON file path
    parser.add_argument('--json', type=str, default=None,
                        help='Path to the memories_history.json file')
    
    # Output folder
    parser.add_argument('--output', type=str, default=None,
                        help='Path to save the fixed files')
    
    # Timezone used for the EXIF date (EXIF dates are local time by convention)
    parser.add_argument('--timezone', type=str, default=None,
                        help='Timezone for embedded dates, e.g. "Europe/Stockholm" (default: system timezone)')

    # Verbose mode
    parser.add_argument('--verbose', action='store_true', help='Enable detailed logging')
    
    return parser.parse_args()

def load_metadata(json_path):
    """
    Load and parse the Snapchat JSON file.
    Returns a list of media entries.
    """
    if not os.path.exists(json_path):
        logger.error(f"JSON file not found at: {json_path}")
        return None

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # Snapchat structure varies, but usually it's under 'Saved Media'
            return data.get('Saved Media', [])
    except Exception as e:
        logger.error(f"Failed to parse JSON: {e}")
        return None

# Filename format of newer exports: "2020-01-31_<uuid>-main.mp4" / "...-overlay.png"
UUID_FILENAME_RE = re.compile(
    r'^(\d{4}-\d{2}-\d{2})_([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})',
    re.IGNORECASE
)
LOCATION_RE = re.compile(r'(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)')

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.heic'}
VIDEO_EXTENSIONS = {'.mp4', '.mov'}

def parse_utc_date(date_str):
    """Parse a Snapchat date string like '2022-10-04 18:33:11 UTC' into an aware UTC datetime."""
    try:
        return datetime.strptime(date_str.strip(), "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None

def parse_location(location_str):
    """
    Parse 'Latitude, Longitude: 59.3293, 18.0686' into (lat, lon).
    Returns None if missing or 0,0 (Snapchat uses 0,0 for 'unknown').
    """
    if not isinstance(location_str, str):
        return None
    match = LOCATION_RE.search(location_str)
    if not match:
        return None
    lat, lon = float(match.group(1)), float(match.group(2))
    if (lat == 0 and lon == 0) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon

def extract_media_id(entry):
    """Find the 'mid' query parameter in any of the entry's download URLs."""
    for value in entry.values():
        if isinstance(value, str) and 'mid=' in value:
            mid = parse_qs(urlparse(value).query).get('mid')
            if mid:
                return mid[0].lower()
    return None

def build_metadata_index(media_list):
    """
    Index JSON entries by media id and by UTC date so files can be matched to them.
    Each indexed item is a dict with 'date' (aware UTC datetime), 'location' and 'type'.
    """
    by_id = {}
    by_day = {}
    for entry in media_list:
        date_obj = parse_utc_date(entry.get('Date', ''))
        if date_obj is None:
            continue
        info = {
            'date': date_obj,
            'location': parse_location(entry.get('Location')),
            'type': str(entry.get('Media Type', '')).lower(),
        }
        mid = extract_media_id(entry)
        if mid:
            by_id[mid] = info
        by_day.setdefault(date_obj.strftime('%Y-%m-%d'), []).append(info)
    return by_id, by_day

def resolve_file_metadata(file_path, by_id, by_day):
    """
    Work out the capture date (and location, if known) for a file.
    Returns (date_obj, location, source) or (None, None, None).
    """
    stem = file_path.stem

    # 1. Standard Snapchat export format: "2022-10-04 18:33:11 UTC"
    date_obj = parse_utc_date(stem)
    if date_obj:
        return date_obj, None, 'filename'

    # 2. Newer format: "2020-01-31_<uuid>-main" -> match the uuid against the JSON
    match = UUID_FILENAME_RE.match(stem)
    if match:
        day, mid = match.group(1), match.group(2).lower()
        if mid in by_id:
            info = by_id[mid]
            return info['date'], info['location'], 'json'

        # Fall back to a unique entry of the same type on the same day
        wanted = 'video' if file_path.suffix.lower() in VIDEO_EXTENSIONS else 'image'
        same_day = [i for i in by_day.get(day, []) if i['type'] == wanted]
        if len(same_day) == 1:
            info = same_day[0]
            return info['date'], info['location'], 'json'

    # 3. Date prefix only: "2020-01-31..." (time unknown, use noon UTC so the
    #    calendar day survives conversion to most timezones)
    try:
        day = datetime.strptime(stem[:10], "%Y-%m-%d")
        return day.replace(hour=12, tzinfo=timezone.utc), None, 'date-only'
    except ValueError:
        return None, None, None

def set_file_times(filepath, timestamp):
    """
    Update the file system 'Modified' and 'Accessed' times.
    This works for both Images and Videos.
    """
    try:
        # os.utime takes (atime, mtime)
        os.utime(filepath, (timestamp, timestamp))
    except Exception as e:
        logger.warning(f"Could not set system time for {filepath}: {e}")

def to_exif_rational(value):
    """Convert a decimal degree value to EXIF degrees/minutes/seconds rationals."""
    value = abs(value)
    degrees = int(value)
    minutes_float = (value - degrees) * 60
    minutes = int(minutes_float)
    seconds = round((minutes_float - minutes) * 60 * 10000)
    return ((degrees, 1), (minutes, 1), (seconds, 10000))

def set_exif_date(filepath, local_date, location=None):
    """
    Embed EXIF 'DateTimeOriginal' (and GPS, if known) into JPG images.
    This allows photo apps (Google Photos, Apple Photos) to sort them correctly.
    Returns True on success.
    """
    # Format date for EXIF: "YYYY:MM:DD HH:MM:SS" (local time) plus its UTC offset
    exif_date_str = local_date.strftime("%Y:%m:%d %H:%M:%S")
    offset = local_date.strftime("%z")
    offset_str = f"{offset[:3]}:{offset[3:]}"

    def apply_tags(exif_dict):
        exif_dict['0th'][piexif.ImageIFD.DateTime] = exif_date_str
        exif_dict['Exif'][piexif.ExifIFD.DateTimeOriginal] = exif_date_str
        exif_dict['Exif'][piexif.ExifIFD.DateTimeDigitized] = exif_date_str
        exif_dict['Exif'][piexif.ExifIFD.OffsetTime] = offset_str
        exif_dict['Exif'][piexif.ExifIFD.OffsetTimeOriginal] = offset_str
        exif_dict['Exif'][piexif.ExifIFD.OffsetTimeDigitized] = offset_str
        if location:
            lat, lon = location
            exif_dict['GPS'].update({
                piexif.GPSIFD.GPSVersionID: (2, 3, 0, 0),
                piexif.GPSIFD.GPSLatitudeRef: 'N' if lat >= 0 else 'S',
                piexif.GPSIFD.GPSLatitude: to_exif_rational(lat),
                piexif.GPSIFD.GPSLongitudeRef: 'E' if lon >= 0 else 'W',
                piexif.GPSIFD.GPSLongitude: to_exif_rational(lon),
            })
        return exif_dict

    try:
        try:
            # Load existing EXIF and add our tags
            exif_bytes = piexif.dump(apply_tags(piexif.load(filepath)))
        except Exception:
            # Existing EXIF is malformed; start from a clean set of tags
            exif_bytes = piexif.dump(apply_tags({'0th': {}, 'Exif': {}, 'GPS': {}, '1st': {}, 'thumbnail': None}))
        piexif.insert(exif_bytes, filepath)
        return True
    except Exception as e:
        # Some snapchat "JPGs" are actually PNGs or have corrupted headers. We log but continue.
        logger.debug(f"Could not write EXIF to {filepath}: {e}")
        return False

def iso6709(location):
    lat, lon = location
    return f"{lat:+08.4f}{lon:+09.4f}/"

def set_video_metadata(filepath, utc_date, location=None):
    """
    Embed 'creation_time' (and location, if known) into MP4/MOV files using ffmpeg.
    Streams are copied, so there is no quality loss. Returns True on success.
    """
    src = Path(filepath)
    tmp = src.with_name(f".tmp_{src.name}")
    cmd = [
        'ffmpeg', '-y', '-loglevel', 'error', '-i', str(src),
        '-map', '0', '-c', 'copy', '-map_metadata', '0',
        '-metadata', f"creation_time={utc_date.strftime('%Y-%m-%dT%H:%M:%S.000000Z')}",
    ]
    if location:
        cmd += ['-metadata', f"location={iso6709(location)}"]
    cmd.append(str(tmp))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.debug(f"ffmpeg failed for {filepath}: {result.stderr.strip()}")
            return False
        os.replace(tmp, src)
        return True
    except Exception as e:
        logger.debug(f"Could not write video metadata to {filepath}: {e}")
        return False
    finally:
        if tmp.exists():
            tmp.unlink()

def resolve_timezone(name):
    """Return the tzinfo used for local EXIF dates (--timezone > TZ env var > system)."""
    name = name or os.environ.get('TZ', '').lstrip(':')
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            logger.error(f"Unknown timezone '{name}', falling back to system timezone.")
    return datetime.now().astimezone().tzinfo

def process_memories(args):
    """
    Main processing logic.
    """
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # 1. Resolve Paths (CLI args > Auto-discovery > Fallback)
    input_path = args.input
    json_path = args.json
    output_path = args.output

    # If critical paths are missing, try auto-discovery
    if not input_path or not json_path:
        disc_input, disc_json, disc_output = auto_discover_paths()
        
        # Only overwrite if the user didn't specify via CLI
        if disc_input:
            input_path = input_path or disc_input
            json_path = json_path or disc_json
            output_path = output_path or disc_output
        else:
            # If discovery fails, check if we are in a state to error out
            if not input_path:
                logger.error("Could not find 'memories' folder. Please specify --input or drag your data folder here.")
                return
            if not json_path:
                logger.error("Could not find 'memories_history.json'. Please specify --json or drag your data folder here.")
                return

    # Default output if still not set
    if not output_path:
        output_path = str(Path(input_path).parent / 'fixed_memories')

    input_dir = Path(input_path)
    output_dir = Path(output_path)
    local_tz = resolve_timezone(args.timezone)
    has_ffmpeg = shutil.which('ffmpeg') is not None
    
    logger.info(f"Input Directory: {input_dir}")
    logger.info(f"JSON File:      {json_path}")
    logger.info(f"Output Directory: {output_dir}")
    logger.info(f"Timezone:       {local_tz}")
    if not has_ffmpeg:
        logger.warning("ffmpeg not found: videos will only get their file date fixed, not embedded metadata.")

    logger.info("Loading metadata...")
    media_list = load_metadata(json_path)
    
    if not media_list:
        logger.error("No media entries found in JSON. Exiting.")
        return

    by_id, by_day = build_metadata_index(media_list)
    logger.info(f"Found {len(media_list)} entries in JSON. matching with files...")
    
    # Get list of all files in input directory
    if not input_dir.exists():
         logger.error(f"Input directory does not exist: {input_dir}")
         return

    # Create output directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)

    files_processed = 0
    files_failed = 0
    stats = {'json': 0, 'filename': 0, 'date-only': 0, 'location': 0, 'embed_failed': 0}

    all_files = sorted(p for p in input_dir.glob('*') if p.is_file())
    
    for file_path in tqdm(all_files, desc="Processing Memories"):
        if file_path.name.startswith('.'): continue # Skip hidden files
        
        try:
            # 1. Determine the timestamp (and location) from the filename and JSON
            date_obj, location, source = resolve_file_metadata(file_path, by_id, by_day)
            
            if date_obj is None:
                logger.debug(f"Skipping {file_path.name}: Cannot parse date from filename.")
                files_failed += 1
                continue

            stats[source] += 1
            if location:
                stats['location'] += 1
            if source == 'date-only':
                logger.debug(f"{file_path.name}: no JSON match, only the date is known.")

            # 2. Copy file to output directory (to keep original safe)
            dest_path = output_dir / file_path.name
            shutil.copy2(file_path, dest_path)
            
            # 3. Apply embedded metadata
            suffix = dest_path.suffix.lower()
            embedded = True
            if suffix in ('.jpg', '.jpeg'):
                embedded = set_exif_date(str(dest_path), date_obj.astimezone(local_tz), location)
            elif suffix in VIDEO_EXTENSIONS and has_ffmpeg:
                embedded = set_video_metadata(str(dest_path), date_obj, location)
            if not embedded:
                stats['embed_failed'] += 1

            # 4. Set File System Timestamp (Modified Date) - Works for ALL files.
            #    Must happen last, since writing EXIF/video metadata rewrites the file.
            set_file_times(str(dest_path), date_obj.timestamp())
            
            files_processed += 1
            
        except Exception as e:
            logger.debug(f"Error processing {file_path.name}: {e}")
            files_failed += 1

    logger.info("------------------------------------------------")
    logger.info(f"Processing Complete.")
    logger.info(f"Successfully Processed: {files_processed}")
    logger.info(f"  Exact time from JSON:     {stats['json']}")
    logger.info(f"  Exact time from filename: {stats['filename']}")
    logger.info(f"  Date only (no time):      {stats['date-only']}")
    logger.info(f"  With GPS location:        {stats['location']}")
    if stats['embed_failed']:
        logger.info(f"  Embedded metadata failed: {stats['embed_failed']} (file date still set; use --verbose for details)")
    logger.info(f"Skipped/Failed: {files_failed}")
    logger.info(f"Memories are located in: {output_dir}")
    logger.info("------------------------------------------------")

if __name__ == "__main__":
    args = parse_arguments()
    process_memories(args)
