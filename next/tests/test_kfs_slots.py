import asyncio

import pytest

from manualreader.stages.finish.kfs_slots import KfsSlots, KfsUnavailable


def test_kfs_instances_share_inflight_budget_and_recover_after_429(tmp_path, monkeypatch):
    import manualreader.stages.finish.kfs_slots as limits

    now = [1000.0]
    monkeypatch.setattr(limits, 'time', lambda: now[0])
    first = KfsSlots('localhost', 'fixture', maximum=20, directory=tmp_path)
    second = KfsSlots('localhost', 'fixture', maximum=20, directory=tmp_path)
    for i in range(20):
        assert (first if i % 2 else second).acquire(str(i)) == i+1
    assert second.acquire('extra') is None
    first.release('0')
    assert second.acquire('extra') == 20

    result = second.adjust([{'status': 429, 'retry_after': '3'}], False)
    assert result['after'] == 14
    for i in range(1, 8):
        first.release(str(i))
    assert first.acquire('after') is None
    now[0] += 3
    assert first.acquire('after') == 14
    for _ in range(14):
        result = first.adjust([{'status': 200}], True)
    assert result['after'] == 15


def test_kfs_slows_down_on_transient_burst_and_stops_dispatch_after_auth_failure(tmp_path, monkeypatch):
    import manualreader.stages.finish.kfs_slots as limits

    now = [1000.0]
    monkeypatch.setattr(limits, 'time', lambda: now[0])
    first = KfsSlots('localhost', 'fixture', directory=tmp_path)
    second = KfsSlots('localhost', 'fixture', directory=tmp_path)
    assert first.adjust([{'status': 503}], False) is None
    assert second.adjust([{'status': 503}], False)['after'] == 10
    assert first.acquire('next') is None
    second.adjust([{'status': 403}], False)
    with pytest.raises(KfsUnavailable):
        first.acquire('after')
    now[0] += 31
    assert second.acquire('after') == 1


def test_kfs_releases_slot_on_exception(tmp_path):
    first = KfsSlots('localhost', 'fixture', maximum=1, directory=tmp_path)
    second = KfsSlots('localhost', 'fixture', maximum=1, directory=tmp_path)

    async def exercise():
        with pytest.raises(ValueError):
            async with first.slot():
                assert second.acquire('other') is None
                raise ValueError('test')
        async with second.slot():
            assert second.peak == 1

    asyncio.run(exercise())


def test_kfs_probes_wider_after_a_quiet_period(tmp_path, monkeypatch):
    import manualreader.stages.finish.kfs_slots as limits

    now = [1000.0]
    monkeypatch.setattr(limits, 'time', lambda: now[0])
    lane = KfsSlots('localhost', 'fixture', directory=tmp_path)
    lane.adjust([{'status': 429, 'retry_after': '3'}], False)
    now[0] += 16
    for i in range(17):
        assert lane.acquire(str(i)) == i+1
    assert lane.acquire('past-window') is None


def test_provider_retries_an_empty_json_response_in_streaming_mode(tmp_path, monkeypatch):
    from manualreader.stages.finish import provider

    monkeypatch.setenv(provider.DS_CREDENTIAL, 'fixture')
    requests = []
    value = {'groups': [], 'unresolved_source_ids': []}

    def ds(folder, stage, prompt, payload, *, stream):
        requests.append(stream)
        response = {'model': provider.DS['model'], 'stream_complete': True, 'empty_content': not stream}
        return {'attempts': [{'status': 200, 'response': response}], 'response': response,
                'result': value if stream else None, 'error': None if stream else 'empty'}

    monkeypatch.setattr(provider, 'invoke_ds', ds)
    monkeypatch.delenv('MANUAL_READER_DS_FALLBACK', raising=False)
    client = provider.Provider(tmp_path / 'run', ds_state_dir=tmp_path / 'state')
    assert asyncio.run(client.scope('example')('B1', {})) == value
    assert requests == [False, True]
    assert client.log[0]['provider'] == 'ds'
    assert client.log[0]['retries'] == 1
