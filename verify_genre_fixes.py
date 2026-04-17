#!/usr/bin/env python3
"""Verification script for genre router fixes."""
import subprocess
import sys
from pathlib import Path

results = {}

# Check 1 — genre_router fallback changed
try:
    content = open('backend/services/genre_router.py').read()
    assert 'genres[0].title()' in content, "raw genre fallback not added"
    assert 'clear_genre_cache' in content, "cache clear function missing"
    assert 'raw-genre' in content, "raw genre logging not present"
    results['Fix 1 — genre_router fallback'] = '✅ PASS'
except Exception as e:
    results['Fix 1 — genre_router fallback'] = f'❌ FAIL: {e}'

# Check 2 — cleanup script exists
try:
    assert Path('backend/cleanup_uncategorized.py').exists(), "cleanup script missing"
    content = open('backend/cleanup_uncategorized.py').read()
    assert 'MANUAL_GENRE_MAP' in content, "MANUAL_GENRE_MAP not found"
    assert 'def cleanup' in content, "cleanup function not found"
    assert 'def main' in content, "main function not found"
    results['Fix 2 — cleanup_uncategorized.py'] = '✅ PASS'
except Exception as e:
    results['Fix 2 — cleanup_uncategorized.py'] = f'❌ FAIL: {e}'

# Check 3 — new endpoint exists
try:
    content = open('backend/app.py').read()
    assert 'clear-genre-cache' in content, "cache endpoint missing"
    assert 'clear_genre_cache_endpoint' in content, "endpoint function not found"
    assert 'from services.genre_router import clear_genre_cache as _clear' in content, "import missing"
    results['Fix 3 — /api/clear-genre-cache endpoint'] = '✅ PASS'
except Exception as e:
    results['Fix 3 — /api/clear-genre-cache endpoint'] = f'❌ FAIL: {e}'

# Check 4 — all files compile
for f in ['backend/services/genre_router.py', 'backend/app.py', 'backend/cleanup_uncategorized.py']:
    r = subprocess.run([sys.executable, '-m', 'py_compile', f], capture_output=True, text=True)
    if r.returncode == 0:
        results[f'Compile — {Path(f).name}'] = '✅ PASS'
    else:
        results[f'Compile — {Path(f).name}'] = f'❌ FAIL: {r.stderr[:100]}'

# Print summary
print('\n' + '═' * 70)
print('         ✨ GENRE ROUTER FIX VERIFICATION ✨')
print('═' * 70)
for check, result in results.items():
    print(f'{result:60} {check}')
print('═' * 70)

all_passed = all('✅' in v for v in results.values())
if all_passed:
    print('\n🎉 ALL VERIFICATIONS PASSED!')
else:
    print('\n⚠️  SOME CHECKS FAILED — review above')
    sys.exit(1)
