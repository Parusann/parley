# Probe classification with injected primitives — no sockets, no network.

from parley import hub_probes


def test_vulkan_up_and_down():
    assert hub_probes.vulkan_server_up('127.0.0.1', 8181, connect=lambda h, p: 0)
    assert not hub_probes.vulkan_server_up('127.0.0.1', 8181, connect=lambda h, p: 10061)


def test_ollama_up_uses_tags_endpoint():
    seen = {}

    def fake_get(url, timeout):
        seen['url'] = url
        return 200

    assert hub_probes.ollama_up('http://127.0.0.1:11434', get_status=fake_get)
    assert seen['url'] == 'http://127.0.0.1:11434/api/tags'
    assert not hub_probes.ollama_up('http://127.0.0.1:11434',
                                    get_status=lambda u, timeout: (_ for _ in ()).throw(OSError()))


def test_amadeus_health_derived_from_ask_endpoint():
    seen = {}

    def fake_get(url, timeout):
        seen['url'] = url
        return 200

    cfg = {'endpoint': 'http://127.0.0.1:7780/ask'}
    assert hub_probes.amadeus_up(cfg, get_status=fake_get)
    assert seen['url'] == 'http://127.0.0.1:7780/health'


def test_prober_snapshot_single_cycle():
    prober = hub_probes.Prober(probes={'a': lambda: (True, 'fine'),
                                       'b': lambda: (False, 'down')})
    prober.refresh_once()
    snap = prober.snapshot()
    assert snap['a'] == (True, 'fine')
    assert snap['b'] == (False, 'down')


def test_prober_survives_a_raising_probe():
    prober = hub_probes.Prober(probes={'bad': lambda: (_ for _ in ()).throw(RuntimeError('x'))})
    prober.refresh_once()
    ok, detail = prober.snapshot()['bad']
    assert ok is False
