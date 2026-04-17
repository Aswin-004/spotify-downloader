import subprocess
results = {}

# Check 1 - map_genre_string exists
try:
    content = open('backend/services/genre_router.py').read()
    assert 'def map_genre_string' in content
    results['map_genre_string function'] = 'PASS'
except Exception as e:
    results['map_genre_string function'] = f'FAIL: {e}'

# Check 2 - staging folder constant exists
try:
    content = open('backend/services/auto_downloader.py').read()
    assert 'STAGING_FOLDER' in content
    assert 'staging_dir' in content
    assert 'tagging_report' in content
    results['Two-pass staging system'] = 'PASS'
except Exception as e:
    results['Two-pass staging system'] = f'FAIL: {e}'

# Check 3 - MusicBrainz genre used for routing
try:
    content = open('backend/services/auto_downloader.py').read()
    assert 'mb_genre' in content
    assert 'map_genre_string(mb_genre)' in content
    results['MB genre routing'] = 'PASS'
except Exception as e:
    results['MB genre routing'] = f'FAIL: {e}'

# Check 4 - all files compile
for f in ['backend/services/genre_router.py',
          'backend/services/auto_downloader.py',
          'backend/services/downloader_service.py',
          'backend/config.py']:
    r = subprocess.run(['python', '-m', 'py_compile', f], capture_output=True)
    name = f.split('/')[-1]
    results[f'Compile {name}'] = 'PASS' if r.returncode == 0 else f'FAIL: {r.stderr.decode()[:100]}'

print('\n' + '=' * 50)
print('    VERIFICATION SUMMARY')
print('=' * 50)
for k, v in results.items():
    status = 'PASS' in v
    icon = '\u2705' if status else '\u274c'
    print(f'{icon}  {k}: {v}')
all_ok = all('PASS' in v for v in results.values())
print(f"\n{'ALL PASSED' if all_ok else 'SOME FAILED'}")
