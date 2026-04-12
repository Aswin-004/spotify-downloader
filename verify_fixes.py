import subprocess
import sys

results = {}

# Check 1 — JSON paths
try:
    sys.path.insert(0, 'backend')
    from services.auto_downloader import INGEST_HISTORY_FILE, INGEST_FAILURES_FILE
    assert 'services' not in INGEST_HISTORY_FILE, f"Path still contains 'services': {INGEST_HISTORY_FILE}"
    assert 'services' not in INGEST_FAILURES_FILE, f"Path still contains 'services': {INGEST_FAILURES_FILE}"
    results['Fix 1 — JSON file paths'] = '✅ PASS'
except Exception as e:
    results['Fix 1 — JSON file paths'] = f'❌ FAIL: {e}'

# Check 2 — No duplicate BPM
try:
    content = open('backend/services/auto_downloader.py').read()
    assert 'from bpm_key_service import analyze_and_tag' not in content, "BPM import still present in auto_downloader.py"
    assert '_analyze_and_tag' not in content, "_analyze_and_tag still present in auto_downloader.py"
    results['Fix 2 — BPM duplicate removed'] = '✅ PASS'
except Exception as e:
    results['Fix 2 — BPM duplicate removed'] = f'❌ FAIL: {e}'

# Check 3 — force_redownload not auto-triggered
try:
    content = open('frontend-react/src/services/api.js').read()
    assert 'forceRedownload: Boolean' not in content, "Auto-trigger logic still present in api.js"
    assert 'forceRedownload === true' in content, "Smart check logic missing from api.js"
    results['Fix 3+4 — Smart force_redownload'] = '✅ PASS'
except Exception as e:
    results['Fix 3+4 — Smart force_redownload'] = f'❌ FAIL: {e}'

# Check 4 — New endpoint exists
try:
    content = open('backend/app.py').read()
    assert 'clear-history-for-playlist' in content, "Clear history endpoint missing from app.py"
    assert 'remove_tracks_from_history' in content, "remove_tracks_from_history call missing from app.py"
    results['Fix 5 — Clear history endpoint'] = '✅ PASS'
except Exception as e:
    results['Fix 5 — Clear history endpoint'] = f'❌ FAIL: {e}'

# Check 5 — Telegram path fixed
try:
    content = open('backend/telegram_bot.py').read()
    assert 'backend_root / "ingest_failures.json"' in content, "Telegram path fix not applied"
    results['Fix 6 — Telegram path'] = '✅ PASS'
except Exception as e:
    results['Fix 6 — Telegram path'] = f'❌ FAIL: {e}'

# Check 6 — Afro House in config
try:
    content = open('backend/config.py').read()
    assert 'Afro House' in content, "Afro House genre missing from config"
    assert 'melodic house techno' in content, "'melodic house techno' missing from config"
    assert 'afrotech' in content, "'afrotech' missing from config"
    assert 'tribal house' in content, "'tribal house' missing from config"
    assert 'south african house' in content, "'south african house' missing from config"
    results['Fix 7 — Afro House genre map'] = '✅ PASS'
except Exception as e:
    results['Fix 7 — Afro House genre map'] = f'❌ FAIL: {e}'

# Check 7 — All files compile
for f in ['backend/services/auto_downloader.py', 'backend/app.py', 'backend/telegram_bot.py', 'backend/config.py']:
    r = subprocess.run([sys.executable, '-m', 'py_compile', f], capture_output=True, text=True)
    if r.returncode == 0:
        results[f'Compile — {f.split("/")[-1]}'] = '✅ PASS'
    else:
        results[f'Compile — {f.split("/")[-1]}'] = f'❌ FAIL: {r.stderr}'

# Check 8 — New function exists
try:
    from services.auto_downloader import remove_tracks_from_history
    results['Fix 5 — remove_tracks_from_history function'] = '✅ PASS'
except Exception as e:
    results['Fix 5 — remove_tracks_from_history function'] = f'❌ FAIL: {e}'

# Check 9 — clearHistoryForPlaylist in api.js
try:
    content = open('frontend-react/src/services/api.js').read()
    assert 'clearHistoryForPlaylist' in content, "clearHistoryForPlaylist method missing"
    assert '/api/clear-history-for-playlist' in content, "API endpoint missing from api.js"
    results['Fix 5 — clearHistoryForPlaylist in api.js'] = '✅ PASS'
except Exception as e:
    results['Fix 5 — clearHistoryForPlaylist in api.js'] = f'❌ FAIL: {e}'

# Check 10 — Clear & Retry button in Header
try:
    content = open('frontend-react/src/components/Header.jsx').read()
    assert 'Clear history & re-download all' in content, "Clear & Retry button missing"
    assert 'clearHistoryForPlaylist' in content, "Clear history call missing from Header"
    results['Fix 5 — Clear & Retry button in Header'] = '✅ PASS'
except Exception as e:
    results['Fix 5 — Clear & Retry button in Header'] = f'❌ FAIL: {e}'

# Print summary
print('\n' + '═' * 70)
print('         ✨ VERIFICATION SUMMARY ✨')
print('═' * 70)
for check, result in results.items():
    print(f'{check}: {result}')
print('═' * 70)

all_passed = all('✅ PASS' in v for v in results.values())
if all_passed:
    print('\n🎉 ALL CHECKS PASSED — ready to start server!')
else:
    print('\n⚠️  SOME CHECKS FAILED — review above before starting server')
    sys.exit(1)
