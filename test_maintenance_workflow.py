#!/usr/bin/env python3
"""
Automated Maintenance Page Workflow Test
========================================
Run on your laptop to validate repair_index → backfill_gemini Pass 2 workflow.

Usage:
    python test_maintenance_workflow.py [--with-files] [--execute]

Options:
    --with-files   Create test MP3 files in NeedsReview/Unknown/ (requires ffmpeg)
    --execute      Run actual commands (default: dry-run only)
"""

import sys
import os
import subprocess
from pathlib import Path
from datetime import datetime

# Configuration
BACKEND_DIR = Path(__file__).parent / "backend"
BASE_DOWNLOAD_DIR = None  # Will read from .env

def log_section(title):
    """Print a section header."""
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}\n")

def log_step(step_num, desc):
    """Print a step marker."""
    print(f"\n[STEP {step_num}] {desc}")
    print("-" * 70)

def log_ok(msg):
    """Print success message."""
    print(f"  ✓ {msg}")

def log_warn(msg):
    """Print warning message."""
    print(f"  ⚠ {msg}")

def log_error(msg):
    """Print error message."""
    print(f"  ✗ {msg}")

def read_base_dir():
    """Extract BASE_DOWNLOAD_DIR from .env"""
    env_file = BACKEND_DIR / ".env"
    if not env_file.exists():
        log_error(f".env not found at {env_file}")
        return None

    with open(env_file) as f:
        for line in f:
            if line.startswith("BASE_DOWNLOAD_DIR="):
                path = line.split("=", 1)[1].strip()
                # Handle Windows paths
                if path.startswith('"') and path.endswith('"'):
                    path = path[1:-1]
                return Path(path)

    log_error("BASE_DOWNLOAD_DIR not found in .env")
    return None

def check_env():
    """Verify test environment."""
    log_step(1, "Environment Check")

    checks = []

    # Check Python
    print(f"  Python: {sys.version.split()[0]}")
    checks.append(True)

    # Check backend directory
    if BACKEND_DIR.exists():
        log_ok(f"Backend directory: {BACKEND_DIR}")
        checks.append(True)
    else:
        log_error(f"Backend directory not found: {BACKEND_DIR}")
        checks.append(False)

    # Check .env
    env_file = BACKEND_DIR / ".env"
    if env_file.exists():
        log_ok(".env file exists")
        checks.append(True)
    else:
        log_error(".env file not found")
        checks.append(False)

    # Check BASE_DOWNLOAD_DIR
    global BASE_DOWNLOAD_DIR
    BASE_DOWNLOAD_DIR = read_base_dir()
    if BASE_DOWNLOAD_DIR and BASE_DOWNLOAD_DIR.exists():
        log_ok(f"BASE_DOWNLOAD_DIR: {BASE_DOWNLOAD_DIR}")
        checks.append(True)
    else:
        log_warn(f"BASE_DOWNLOAD_DIR not accessible: {BASE_DOWNLOAD_DIR}")
        checks.append(False)

    # Check Python dependencies
    try:
        import pymongo
        log_ok("pymongo installed")
        checks.append(True)
    except ImportError:
        log_error("pymongo not installed (pip install pymongo --break-system-packages)")
        checks.append(False)

    try:
        from mutagen.id3 import ID3
        log_ok("mutagen installed")
        checks.append(True)
    except ImportError:
        log_error("mutagen not installed (pip install mutagen --break-system-packages)")
        checks.append(False)

    # Check MongoDB
    try:
        from pymongo import MongoClient
        client = MongoClient("mongodb://localhost:27017", serverSelectionTimeoutMS=2000)
        client.server_info()
        log_ok("MongoDB accessible at localhost:27017")
        checks.append(True)
        client.close()
    except Exception as e:
        log_warn(f"MongoDB not accessible: {e}")
        log_warn("  Start MongoDB: mongod (or Docker container)")
        checks.append(False)

    # Check GEMINI_API_KEY
    try:
        env_file = BACKEND_DIR / ".env"
        with open(env_file) as f:
            content = f.read()
            if "GEMINI_API_KEY=" in content and "GEMINI_API_KEY=" in content.split("GEMINI_API_KEY=")[1][:50]:
                log_ok("GEMINI_API_KEY is set")
                checks.append(True)
            else:
                log_warn("GEMINI_API_KEY not set or empty")
                log_warn("  Pass 2 will fail without API key")
                checks.append(False)
    except Exception as e:
        log_error(f"Could not check GEMINI_API_KEY: {e}")
        checks.append(False)

    return all(checks)

def create_test_files(execute=False):
    """Create test MP3 files in NeedsReview/Unknown/"""
    log_step(2, "Create Test Files")

    if not BASE_DOWNLOAD_DIR:
        log_error("BASE_DOWNLOAD_DIR not set")
        return False

    unknown_dir = BASE_DOWNLOAD_DIR / "NeedsReview" / "Unknown"

    # Check if ffmpeg is available
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        log_ok("ffmpeg is available")
    except (FileNotFoundError, subprocess.CalledProcessError):
        log_error("ffmpeg not found (install via: choco install ffmpeg)")
        return False

    if not execute:
        print(f"  [DRY-RUN] Would create test files in: {unknown_dir}")
        print(f"  [DRY-RUN] Create with: --with-files flag")
        return True

    # Create directory
    unknown_dir.mkdir(parents=True, exist_ok=True)
    log_ok(f"Created directory: {unknown_dir}")

    # Create test MP3 files
    for i in range(1, 4):
        test_file = unknown_dir / f"Test_Unknown_{i}.mp3"
        if test_file.exists():
            log_warn(f"File already exists: {test_file.name}")
            continue

        cmd = [
            "ffmpeg", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-t", "3", "-q:a", "9", str(test_file)
        ]
        try:
            subprocess.run(cmd, capture_output=True, check=True)
            log_ok(f"Created: {test_file.name}")
        except subprocess.CalledProcessError as e:
            log_error(f"Failed to create {test_file.name}: {e}")
            return False

    # Add ID3 tags
    try:
        from mutagen.id3 import ID3, ID3NoHeaderError, TIT2, TPE1, TXXX

        for i, test_file in enumerate(sorted(unknown_dir.glob("Test_Unknown_*.mp3")), 1):
            try:
                tags = ID3(str(test_file))
            except ID3NoHeaderError:
                tags = ID3()

            tags["TIT2"] = TIT2(encoding=3, text=[f"Unknown Test Track {i}"])
            tags["TPE1"] = TPE1(encoding=3, text=[f"Test Artist {i}"])
            tags["TXXX:gemini_genre"] = TXXX(encoding=3, desc="gemini_genre", text=[""])
            tags.save(str(test_file))
            log_ok(f"Tagged: {test_file.name}")
    except Exception as e:
        log_error(f"Failed to add ID3 tags: {e}")
        return False

    return True

def run_repair_index(execute=False):
    """Run repair_index.py"""
    log_step(3, "Run repair_index")

    os.chdir(BACKEND_DIR)
    cmd = ["python", "repair_index.py", "--dry-run"]

    if not execute:
        print(f"  [DRY-RUN] Would execute: {' '.join(cmd)}")
        print(f"  [DRY-RUN] Run with: --execute flag")
        return True

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(result.stdout)
        if result.returncode != 0:
            log_error(f"repair_index failed:\n{result.stderr}")
            return False
        log_ok("repair_index completed successfully")
        return True
    except Exception as e:
        log_error(f"Failed to run repair_index: {e}")
        return False

def run_backfill_gemini(execute=False):
    """Run backfill_gemini.py --pass2"""
    log_step(4, "Run backfill_gemini Pass 2")

    os.chdir(BACKEND_DIR)
    cmd = ["python", "backfill_gemini.py", "--pass2", "--dry-run", "--limit", "2"]

    if not execute:
        print(f"  [DRY-RUN] Would execute: {' '.join(cmd)}")
        print(f"  [DRY-RUN] Run with: --execute flag")
        return True

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        print(result.stdout)
        if result.returncode != 0:
            log_error(f"backfill_gemini failed:\n{result.stderr}")
            return False
        log_ok("backfill_gemini Pass 2 completed successfully")
        return True
    except Exception as e:
        log_error(f"Failed to run backfill_gemini: {e}")
        return False

def verify_results():
    """Verify test results"""
    log_step(5, "Verify Results")

    if not BASE_DOWNLOAD_DIR:
        log_error("BASE_DOWNLOAD_DIR not set")
        return False

    # Check NeedsReview/Unknown files
    unknown_dir = BASE_DOWNLOAD_DIR / "NeedsReview" / "Unknown"
    if unknown_dir.exists():
        mp3_files = list(unknown_dir.glob("*.mp3"))
        log_ok(f"NeedsReview/Unknown/ has {len(mp3_files)} MP3 files")
    else:
        log_warn("NeedsReview/Unknown/ does not exist")

    # Check Library files
    lib_dir = BASE_DOWNLOAD_DIR / "Library"
    if lib_dir.exists():
        all_mp3s = list(lib_dir.rglob("*.mp3"))
        log_ok(f"Library/ has {len(all_mp3s)} total MP3 files")
    else:
        log_warn("Library/ does not exist")

    return True

def main():
    """Main test runner"""
    log_section("MAINTENANCE PAGE WORKFLOW TEST")

    execute = "--execute" in sys.argv
    create_files = "--with-files" in sys.argv

    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Mode: {'EXECUTE' if execute else 'DRY-RUN'}")
    print(f"Create test files: {create_files}\n")

    if not check_env():
        log_section("ENVIRONMENT CHECK FAILED")
        log_error("Fix issues above before proceeding")
        return 1

    if create_files and not create_test_files(execute):
        log_section("CREATE TEST FILES FAILED")
        return 1

    if not run_repair_index(execute):
        log_section("REPAIR_INDEX FAILED")
        return 1

    if not run_backfill_gemini(execute):
        log_section("BACKFILL_GEMINI FAILED")
        return 1

    verify_results()

    log_section("TEST COMPLETE")
    print("\nNext steps:")
    if not execute:
        print("  1. Review dry-run output above")
        print("  2. Re-run with --execute flag to commit changes")
    else:
        print("  1. Verify files moved correctly:")
        print(f"     - Check {BASE_DOWNLOAD_DIR}/NeedsReview/Unknown/ is empty")
        print(f"     - Check {BASE_DOWNLOAD_DIR}/Library/<genre>/ has test files")
        print("  2. Open frontend: http://localhost:5173")
        print("  3. Navigate to Maintenance page")
        print("  4. Click 'Organize' task and verify logs stream in real-time")
    print("\nDocumentation: See STAGING_TEST_ANALYSIS.md")

    return 0

if __name__ == "__main__":
    sys.exit(main())
