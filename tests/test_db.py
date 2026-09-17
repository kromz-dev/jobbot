from jobbot import db


def listing(url, source, title="Aide-Soignant H/F", company="Domino RH", location="Pamiers - 09", **kw):
    base = dict(apply_url=url, source=source, title=title, company=company, location=location, search_hub="Pamiers",
                contract_type="", salary="", description="", posted_date="2026-09-15", distance_km=1.4)
    base.update(kw)
    return base


def setup(tmp_path):
    path = tmp_path / "t.db"
    db.init(path)
    return path


def test_cross_source_duplicates_merge_into_one_offer(tmp_path):
    p = setup(tmp_path)
    run = db.start_run({}, path=p)
    st = db.ingest([
        listing("https://hellowork/1", "Hellowork"),
        listing("https://meteojob/1", "Meteojob", title="Aide-soignant (H/F)", company="Domino Rh", location="Pamiers (09)"),
        listing("https://ft/1", "France Travail", title="Aide-soignant de nuit", company="Korian", location="09 - PAMIERS"),
    ], run, path=p)
    assert st["new_offers"] == 2 and st["merged"] == 1
    offers = db.list_offers(path=p)
    merged = next(o for o in offers if len(o["listings"]) == 2)
    assert merged["sources"] == ["Hellowork", "Meteojob"]
    assert any("sites" in r for r in merged["score_reasons"])


def test_same_site_distinct_posts_stay_separate(tmp_path):
    p = setup(tmp_path)
    run = db.start_run({}, path=p)
    db.ingest([
        listing("https://ft/a", "France Travail", description="A" * 200 + " EHPAD Les Lilas"),
        listing("https://ft/b", "France Travail", description="B" * 200 + " Clinique du Parc"),
        listing("https://hw/a", "Hellowork", description="A" * 200 + " EHPAD Les Lilas"),
    ], run, path=p)
    offers = db.list_offers(path=p)
    assert len(offers) == 2
    assert sorted(len(o["listings"]) for o in offers) == [1, 2]


def test_rerun_updates_instead_of_duplicating(tmp_path):
    p = setup(tmp_path)
    for _ in range(2):
        run = db.start_run({}, path=p)
        st = db.ingest([listing("https://hellowork/1", "Hellowork")], run, path=p)
    assert st["updated"] == 1 and st["new_offers"] == 0
    assert len(db.list_offers(path=p)) == 1


def test_split_blocks_future_merge(tmp_path):
    p = setup(tmp_path)
    run = db.start_run({}, path=p)
    db.ingest([listing("https://hw/1", "Hellowork"), listing("https://mj/1", "Meteojob")], run, path=p)
    offer = db.list_offers(path=p)[0]
    moved = next(lst for lst in offer["listings"] if lst["source"] == "Meteojob")
    new_id = db.split_listing(moved["id"], path=p)
    assert new_id and len(db.list_offers(path=p)) == 2
    assert db.merge_offers(offer["id"], new_id, path=p)
    assert len(db.list_offers(path=p)) == 1


def test_lifecycle_closed_then_reposted(tmp_path):
    p = setup(tmp_path)
    r1 = db.start_run({}, path=p)
    db.ingest([listing("https://hw/1", "Hellowork")], r1, path=p)
    for _ in range(2):
        r = db.start_run({}, path=p)
        db.ingest([], r, path=p)
        db.close_missing(r, ["Pamiers"], ["Hellowork"], path=p)
    o = db.list_offers(path=p)[0]
    assert not o["is_active"] and o["closed_at"]
    r = db.start_run({}, path=p)
    st = db.ingest([listing("https://hw/1", "Hellowork")], r, path=p)
    o = db.list_offers(path=p)[0]
    assert o["is_active"] and o["reposted"] == 1 and st["reposted"] == 1


def test_status_and_note_history(tmp_path):
    p = setup(tmp_path)
    run = db.start_run({}, path=p)
    db.ingest([listing("https://hw/1", "Hellowork")], run, path=p)
    oid = db.list_offers(path=p)[0]["id"]
    o = db.update_offer(oid, status="postule", note="Appeler Mme X", path=p)
    assert o["status"] == "postule" and o["note"] == "Appeler Mme X"
    assert [h["status"] for h in o["status_history"]] == ["postule"]


def test_list_runs_never_exposes_integrations(tmp_path):
    """Même une ligne ancienne (créée avant le correctif) ne doit jamais exposer de secret via /api/runs."""
    p = setup(tmp_path)
    db.start_run({"cities": ["Paris"], "integrations": {"francetravail": {"client_secret": "s3cr3t-value"}}}, path=p)
    runs = db.list_runs(path=p)
    assert "integrations" not in runs[0]["config"]
    assert runs[0]["config"]["cities"] == ["Paris"]
