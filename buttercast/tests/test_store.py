from buttercast.store import Store


def test_latest_reads_tail_and_matches_full_read(tmp_path):
    store = Store(tmp_path)
    assert store.latest("t") is None
    tricky = {"a": 'q"uo,te\nline'}
    store.append("t", tricky)
    assert store.latest("t") == tricky
    big = {"rows": ["y"*100]*5000}  # > one 64 KiB read block
    for payload in (big, {"b": 2}, big):
        store.append("t", payload)
        assert store.latest("t") == payload == store.read("t")[-1]


def _append_many(root, tag, n):
    store = Store(root)
    for i in range(n):
        store.append("shared", {"tag": tag, "i": i, "pad": "x"*2000})  # long rows make torn writes likely


def test_two_processes_append_without_interleaving(tmp_path):
    """API 컨테이너와 워커 컨테이너가 같은 표에 동시에 써도 줄이 섞이거나 깨지지 않는다."""
    import multiprocessing

    context = multiprocessing.get_context("spawn")
    workers = [context.Process(target=_append_many, args=(tmp_path, tag, 150)) for tag in ("api", "worker")]
    for process in workers:
        process.start()
    for process in workers:
        process.join(60)
        assert process.exitcode == 0
    rows = Store(tmp_path).read("shared")
    assert len(rows) == 300
    for tag in ("api", "worker"):
        assert [r["i"] for r in rows if r["tag"] == tag] == list(range(150))
