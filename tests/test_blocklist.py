from riffi_ingest.safety.blocklist import Blocklist, load_blocklist


def test_the_real_blocklist_reads_every_domain_and_reports_rows_without_one(repo_root):
    bl = load_blocklist(repo_root / "blocklist.csv")
    for domain in (
        "bengalurumetro.in",
        "annamalaiparty.online",
        "wetheleaders.store",
        "annamalai.store",
        "cockroachjanata.org",
        "cockroachjantapaarty.org",
        "thecockroachjantaparty.org.in",
        "cockroachjanataparty.pro",
    ):
        assert domain in bl.domains, domain
    assert ("x.com", "/cockroachisback") in bl.paths
    assert "x.com" not in bl.domains  # one X account is blocked, not all of X
    # rows naming sites without a domain are reported for a person to fix, never guessed
    assert len(bl.problems) == 2
    assert any("Generic holiday sites" in p for p in bl.problems)
    assert any("Khel Now" in p for p in bl.problems)


def test_matching_covers_subdomains_but_not_lookalikes():
    bl = Blocklist()
    bl.add("bengalurumetro.in")
    assert bl.match("https://www.bengalurumetro.in/status") == "bengalurumetro.in"
    assert bl.match("http://live.bengalurumetro.in/") == "bengalurumetro.in"
    assert bl.match("https://english.bmrc.co.in/") is None
    assert bl.match("https://notbengalurumetro.in/") is None
    assert bl.match("") is None


def test_a_path_entry_blocks_only_that_path():
    bl = Blocklist()
    bl.add("https://x.com/Cockroachisback")
    assert bl.match("https://x.com/Cockroachisback/status/1") == "x.com/cockroachisback"
    assert bl.match("https://x.com/cockroachisback") == "x.com/cockroachisback"
    assert bl.match("https://x.com/CockroachisbackAgain") is None
    assert bl.match("https://x.com/DKShivakumar") is None


def test_the_real_official_site_is_not_caught_by_its_copycats(repo_root):
    bl = load_blocklist(repo_root / "blocklist.csv")
    assert bl.match("https://www.cockroachjantaparty.org/") is None  # the real one (S015)
    assert bl.match("https://www.wetheleader.org/") is None  # the real one (S018)
    assert bl.match("https://cockroachjanataparty.pro/join") == "cockroachjanataparty.pro"
