import sys
sys.path.insert(0, r'C:\Users\Aswin-pc\Desktop\Project\spotify-meta-downloader\backend')
import os
os.environ['FLASK_ENV'] = 'development'
os.environ['REDIRECT_URI'] = 'http://127.0.0.1:8888/callback'
os.environ['ALLOWED_ORIGINS'] = 'http://localhost:5173'

from database import get_library_index_collection, get_download_history_collection

idx_col  = get_library_index_collection()
hist_col = get_download_history_collection()

history_filenames = {d['filename'] for d in hist_col.find({}, {'filename': 1}) if d.get('filename')}
history_sids      = {d['spotify_id'] for d in hist_col.find({'spotify_id': {'$exists': True, '$ne': ''}}, {'spotify_id': 1}) if d.get('spotify_id')}

print(f'download_history filenames: {len(history_filenames)}')
print(f'download_history spotify_ids: {len(history_sids)}')

missing = []
for doc in idx_col.find({}):
    fname = doc.get('filename', '')
    sid   = doc.get('spotify_id', '')
    has   = (fname and fname in history_filenames) or (sid and sid in history_sids)
    if not has:
        missing.append({'filename': fname, 'spotify_id': sid, 'source': doc.get('source', '')})

print(f'\nTruly missing: {len(missing)}')
print('Sample 5:')
for m in missing[:5]:
    print(f'  fname={m["filename"]!r}  sid={m["spotify_id"]!r}  src={m["source"]!r}')

# Check what fields a typical library_index entry has
sample = idx_col.find_one({})
if sample:
    print(f'\nSample index entry keys: {list(sample.keys())}')
