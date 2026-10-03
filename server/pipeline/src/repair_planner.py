"""Read-only quarantine inventory. Planning never changes sources or releases credentials."""
import argparse
import collections
import hashlib
import json
import sqlite3
import time
from pathlib import Path

STRATEGIES = {
    'credential': {'action': 'vault_review', 'resource': 'admin_only', 'auto_publish': False},
    'ambiguous': {'action': 'reconcile_api_marker', 'resource': 'business_api', 'auto_publish': False},
    'image_asset': {'action': 'bind_original_assets_then_check', 'resource': 'cpu_then_gpu_if_text_missing', 'auto_publish': False},
    'oversize': {'action': 'choose_office_conversion_or_page_tiling_or_content_parts', 'resource': 'cpu_then_existing_gpu_pool', 'auto_publish': False},
    'office': {'action': 'isolated_office_conversion', 'resource': 'cpu', 'auto_publish': False},
    'quality': {'action': 'failed_page_targeted_reparse', 'resource': 'cpu_then_existing_gpu_pool', 'auto_publish': False},
    'parser': {'action': 'inspect_integrity_and_engine_failure', 'resource': 'cpu_then_existing_gpu_pool', 'auto_publish': False},
    'other': {'action': 'diagnose_before_retry', 'resource': 'manual', 'auto_publish': False},
}


def classify(state, error):
    codes = {c.strip().lower() for c in (error or '').split(',')}
    if any('sensitive' in c or 'credential' in c for c in codes): return 'credential'
    if state == 'ambiguous': return 'ambiguous'
    if 'image_assets_require_review' in codes: return 'image_asset'
    if any('oversize' in c or 'manual_limit' in c for c in codes): return 'oversize'
    if 'anydoc_conversion_failed' in codes: return 'office'
    if codes.intersection({'image_text_requires_ocr', 'native_text_coverage_low', 'nonblank_page_empty_or_short', 'empty_table'}): return 'quality'
    if 'mineru_page_failed' in codes: return 'parser'
    return 'other'


def inventory(db, now):
    counts = collections.Counter(); formats = {}; samples = {}
    for path, state, error in db.execute("SELECT path,state,error FROM sources WHERE missing=0 AND state IN ('review','ambiguous') ORDER BY path"):
        family = classify(state, error); counts[family] += 1
        formats.setdefault(family, collections.Counter())[Path(path).suffix.lower()] += 1
        # Identifier only. Never output raw names, paths, error bodies, OCR text or credentials.
        selected = samples.setdefault(family, [])
        if len(selected) < 5: selected.append(hashlib.sha256(path.encode()).hexdigest()[:20])
    return {'at': now, 'mode': 'read_only_plan', 'total': sum(counts.values()),
            'families': {f: {'count': counts[f], 'formats': dict(formats.get(f, {})),
                           'sample_ids': samples.get(f, []), **strategy} for f, strategy in STRATEGIES.items()},
            'requires': ['candidate_output_separate_from_current_version', 'source_permission_recheck',
                         'changed_strategy_before_retry', 'quality_check', 'credential_check',
                         'idempotent_api_publish_and_index_check'],
            'limits': {'actual_gpu_slots': 2, 'nas_write': False, 'cloud_original_content': False,
                       'production_enqueue': False}}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--db', type=Path, required=True)
    args = parser.parse_args()
    with sqlite3.connect(args.db.resolve().as_uri()+'?mode=ro', uri=True, timeout=10) as db:
        db.execute('PRAGMA query_only=ON')
        print(json.dumps(inventory(db, time.time()), ensure_ascii=False))


if __name__ == '__main__': main()
