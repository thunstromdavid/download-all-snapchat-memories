import os
import json
import argparse
import logging
from datetime import datetime
from pathlib import Path
import shutil

# Third-party libraries (installed via requirements.txt)
try:
    import piexif
    from tqdm import tqdm
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
    1. Docker standard path (/data)
    2. Current directory (if user unzipped directly in repo)
    3. Any immediate subdirectory (if user dragged a folder into repo)
    """
    # Check 1: Docker Container Standard Path
    if os.path.exists('/data/json/memories_history.json'):
        return '/data/memories', '/data/json/memories_history.json', '/output'

    # Check 2: Current Directory
    if os.path.exists('json/memories_history.json'):
         return 'memories', 'json/memories_history.json', 'output'

    # Check 3: Immediate Subdirectories
    # This handles the case where the user drags "my_snapchat_backup" into the repo folder
    cwd = Path('.')
    try:
        for item in cwd.iterdir():
            if item.is_dir():
                # Check for the specific JSON file that indicates a valid export
                potential_json = item / 'json' / 'memories_history.json'
                if potential_json.exists():
                    logger.info(f"Auto-discovered Snapchat data in subfolder: {item.name}")
                    return str(item / 'memories'), str(potential_json), 'output'
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

def set_exif_date(filepath, date_obj):
    """
    Embed EXIF 'DateTimeOriginal' into JPG images.
    This allows photo apps (Google Photos, Apple Photos) to sort them correctly.
    """
    try:
        # Format date for EXIF: "YYYY:MM:DD HH:MM:SS"
        exif_date_str = date_obj.strftime("%Y:%m:%d %H:%M:%S")
        
        # Load existing EXIF or create new
        exif_dict = piexif.load(filepath)
        
        # Set DateTimeOriginal (36867), DateTimeDigitized (36868), and DateTime (306)
        exif_dict['0th'][piexif.ImageIFD.DateTime] = exif_date_str
        exif_dict['Exif'][piexif.ExifIFD.DateTimeOriginal] = exif_date_str
        exif_dict['Exif'][piexif.ExifIFD.DateTimeDigitized] = exif_date_str
        
        exif_bytes = piexif.dump(exif_dict)
        piexif.insert(exif_bytes, filepath)
    except Exception as e:
        # Many snapchat images are actually PNGs or videos masked as JPGs, 
        # or have corrupted headers. We log but continue.
        pass

def process_memories(args):
    """
    Main processing logic.
    """
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
    
    logger.info(f"Input Directory: {input_dir}")
    logger.info(f"JSON File:      {json_path}")
    logger.info(f"Output Directory: {output_dir}")

    # Create output directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)
    
    logger.info("Loading metadata...")
    media_list = load_metadata(json_path)
    
    if not media_list:
        logger.error("No media entries found in JSON. Exiting.")
        return

    logger.info(f"Found {len(media_list)} entries in JSON. matching with files...")
    
    files_processed = 0
    files_failed = 0
    
    # Get list of all files in input directory
    if not input_dir.exists():
         logger.error(f"Input directory does not exist: {input_dir}")
         return

    all_files = list(input_dir.glob('*'))
    
    for file_path in tqdm(all_files, desc="Processing Memories"):
        if file_path.name.startswith('.'): continue # Skip hidden files
        
        try:
            # 1. Determine the timestamp
            # Strategy: Try to parse the filename first (most reliable for Snapchat exports)
            # Filename format example: "2022-10-04 18:33:11 UTC.jpg" OR "2020-01-31_UUID-main.mp4"
            date_str = file_path.stem # remove extension
            
            date_obj = None
            
            # Try parsing standard Snapchat export format: "2022-10-04 18:33:11 UTC"
            try:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S UTC")
            except ValueError:
                pass
            
            # Try parsing date prefix format: "2020-01-31_UUID-main"
            if date_obj is None:
                try:
                    # Extract just the date part (first 10 characters: YYYY-MM-DD)
                    date_prefix = date_str[:10]
                    date_obj = datetime.strptime(date_prefix, "%Y-%m-%d")
                except ValueError:
                    pass
            
            if date_obj is None:
                if args.verbose:
                    logger.warning(f"Skipping {file_path.name}: Cannot parse date from filename.")
                files_failed += 1
                continue

            unix_timestamp = date_obj.timestamp()
            
            # 2. Copy file to output directory (to keep original safe)
            dest_path = output_dir / file_path.name
            shutil.copy2(file_path, dest_path)
            
            # 3. Apply Metadata
            
            # Set File System Timestamp (Modified Date) - Works for ALL files
            set_file_times(str(dest_path), unix_timestamp)
            
            # Set EXIF Timestamp - Works for JPG/JPEG only
            if dest_path.suffix.lower() in ['.jpg', '.jpeg']:
                set_exif_date(str(dest_path), date_obj)
            
            files_processed += 1
            
        except Exception as e:
            if args.verbose:
                logger.error(f"Error processing {file_path.name}: {e}")
            files_failed += 1

    logger.info("------------------------------------------------")
    logger.info(f"Processing Complete.")
    logger.info(f"Successfully Processed: {files_processed}")
    logger.info(f"Skipped/Failed: {files_failed}")
    logger.info(f"Memories are located in: {output_dir}")
    logger.info("------------------------------------------------")

if __name__ == "__main__":
    args = parse_arguments()
    process_memories(args)