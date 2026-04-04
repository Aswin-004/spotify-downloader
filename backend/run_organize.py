import sys, os
sys.path.insert(0, r"C:\Users\Aswin-pc\Desktop\Project\spotify-meta-downloader\backend")

from dotenv import load_dotenv
load_dotenv()

from organizer_service import organize_library

mode = os.getenv("ORGANIZE_MODE", "artist")
print(f"Running organize_library in mode: {mode}")

result = organize_library(mode=mode)

print(f"\nDone.")
print(f"  Moved   : {result['moved']}")
print(f"  Skipped : {result['skipped']}")
if result.get("errors"):
    print(f"  Errors  : {len(result['errors'])}")
    for e in result["errors"]:
        print(f"    - {e['file']}: {e['error']}")
else:
    print(f"  Errors  : 0")
