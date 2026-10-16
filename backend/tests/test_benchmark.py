import json
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from app.benchmark import assess, main, DEFAULT_MANIFEST

CASE = dict(name='fixture', type='Static', url='https://site.test/docs/',
            expected_urls=['https://site.test/docs/guide'])


def stats(**kwargs):
    return dict(selected_pages=[dict(url=CASE['expected_urls'][0], title='Guide',
                                     content_characters=500)], errors=[], **kwargs)


def test_candidate_is_not_quality_pass_until_review_matches_output():
    row = assess(CASE, stats(), 0)
    assert row['outcome'] == 'candidate' and row['automated_checks_passed']
    assert not row['quality_pass'] and row['manual_review_status'] == 'pending'
    review = dict(meaningful=True, accurate=True, irrelevant_urls=[], output_sha256='hash')
    assert assess(CASE, stats(), 0, review=review, output_sha256='hash')['quality_pass']
    assert not assess(CASE, stats(), 0, review=review, output_sha256='new')['quality_pass']


def test_nonempty_title_only_output_fails_content_gate():
    data = stats()
    data['selected_pages'][0]['content_characters'] = 10
    row = assess(CASE, data, 0)
    assert row['outcome'] == 'insufficient_content' and not row['quality_pass']


def test_expected_coverage_duplicates_and_out_of_scope():
    data = stats()
    data['selected_pages'] *= 2
    assert assess(CASE, data, 0)['duplicate_urls'] == 1
    data['selected_pages'] = [dict(url='https://site.test/other', content_characters=500)]
    row = assess(CASE, data, 0)
    assert row['expected_coverage'] == 0 and row['out_of_scope_urls']
    assert row['outcome'] == 'needs_review'


def test_partial_and_failed_results_classified_without_excluding_access_failures():
    data = stats()
    data['errors'] = ['HTTP 429: retry later']
    row = assess(CASE, data, 0)
    assert row['outcome'] == 'partial' and row['signals'] == ['rate_limit']
    row = assess(CASE, {}, 1, 'Cannot establish robots.txt permission: robots.txt returned 403')
    assert row['outcome'] == 'failed' and set(row['signals']) == {'robots', 'access_denied'}
    assert assess(CASE, {}, 124)['signals'] == ['timeout']
    assert assess(CASE, stats(), 0, '"content_characters": 429, "requests": 403')['signals'] == []


def test_manifest_covers_multiple_categories_and_valid_expected_scope():
    from app.crawler.urls import in_crawl_scope
    cases = json.loads(DEFAULT_MANIFEST.read_text())
    assert len(cases) >= 12 and len({case['type'] for case in cases}) >= 10
    for case in cases:
        assert case['expected_urls']
        assert all(in_crawl_scope(url, case['url']) for url in case['expected_urls'])


def test_runner_writes_evidence_and_does_not_reuse_stale_stats(tmp_path):
    manifest = tmp_path / 'sites.json'
    manifest.write_text(json.dumps([CASE]))
    args = ['--manifest', str(manifest), '--output', str(tmp_path / 'runs')]

    def success(command, **kwargs):
        target = Path(command[command.index('--output') + 1])
        target.write_text('# Fixture\n')
        target.with_suffix('.stats.json').write_text(json.dumps(stats(crawled=1, selected=1)))
        return subprocess.CompletedProcess(command, 0, 'done', '')

    with patch('app.benchmark.subprocess.run', side_effect=success):
        assert main(args) == 0
    with patch('app.benchmark.subprocess.run', side_effect=subprocess.TimeoutExpired('crawl', 1, output=b'partial log')):
        assert main(args) == 1
    reports = sorted((tmp_path / 'runs').glob('*/results.json'))
    assert len(reports) == 2
    good, failed = [json.loads(path.read_text()) for path in reports]
    assert good['total'] == 1 and good['quality_passes'] == 0
    assert failed['results'][0]['stats'] == {} and failed['outcomes'] == {'failed': 1}
    assert 'partial log' in (reports[1].parent / 'fixture.log').read_text()
    assert (reports[0].parent / 'manifest.json').exists()
    assert 'pending' in (reports[0].parent / 'report.md').read_text()


def test_runner_rejects_unknown_filter_and_unsafe_names(tmp_path):
    with pytest.raises(SystemExit):
        main(['--only', 'unknown'])
    manifest = tmp_path / 'unsafe.json'
    manifest.write_text(json.dumps([dict(CASE, name='../escape')]))
    with pytest.raises(SystemExit):
        main(['--manifest', str(manifest)])


def test_zero_exit_without_file_is_still_execution_failure(tmp_path):
    manifest = tmp_path / 'sites.json'
    manifest.write_text(json.dumps([CASE]))
    with patch('app.benchmark.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'done', '')):
        assert main(['--manifest', str(manifest), '--output', str(tmp_path / 'runs')]) == 1
    report = json.loads(next((tmp_path / 'runs').glob('*/results.json')).read_text())
    assert report['results'][0]['exit_code'] == 1
