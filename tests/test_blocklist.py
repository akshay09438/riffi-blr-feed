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
        "calendarlabs.com",  # the founder supplied these domains on 4 Oct 2026
        "pockethrms.com",
        "godigit.com",
        "bankbazaar.com",
        "khelnow.com",
    ):
        assert domain in bl.domains, domain
    assert ("x.com", "/cockroachisback") in bl.paths
    assert "x.com" not in bl.domains  # one X account is blocked, not all of X
    assert bl.problems == []


def test_a_row_naming_a_site_without_a_domain_is_reported_not_guessed(tmp_path):
    csv_file = tmp_path / "blocklist.csv"
    csv_file.write_text("name,url,reason\nKhel Now fixture tables,—,Venue error found\n", encoding="utf-8")
    bl = load_blocklist(csv_file)
    assert bl.domains == set() and len(bl.problems) == 1 and "Khel Now" in bl.problems[0]


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


def test_file_names_and_tool_names_are_not_domains():
    bl = Blocklist()
    assert bl.add("see notice.pdf and Node.js docs") == 0
    assert bl.domains == set()


def test_matching_never_raises_on_unreadable_links():
    bl = Blocklist()
    bl.add("bengalurumetro.in")
    for url in ("http://[::1/x", "https://a.in:99999/", "::::", "https://good.com@bengalurumetro.in/x"):
        bl.match(url)
    assert bl.match("https://good.com@bengalurumetro.in/x") == "bengalurumetro.in"


# karnatakavarthe.org was DIPR's news site; by 5 Oct 2026 it was hijacked casino spam (D-014)
def test_the_hijacked_karnatakavarthe_site_is_blocked_however_it_is_linked(repo_root):
    bl = load_blocklist(repo_root / "blocklist.csv")
    for url in (
        "https://karnatakavarthe.org/",
        "http://karnatakavarthe.org/some-post/",
        "https://www.karnatakavarthe.org/2021/02/04/",
        "https://news.karnatakavarthe.org/x",
        "https://KarnatakaVarthe.org/",
        "https://www.google.com/amp/s/karnatakavarthe.org/post",  # AMP copies are checked as the page they copy
        "https://karnatakavarthe-org.cdn.ampproject.org/c/s/karnatakavarthe.org/post",
    ):
        assert bl.match(url) == "karnatakavarthe.org", url


def test_dipr_real_channels_and_karnatakavarthe_lookalikes_are_not_blocked(repo_root):
    bl = load_blocklist(repo_root / "blocklist.csv")
    for url in (
        "https://x.com/KarnatakaVarthe",  # DIPR's X account (S003)
        "https://twitter.com/KarnatakaVarthe",
        "https://x.com/KarnatakaVarthe/status/1",
        "https://www.facebook.com/KarnatakaVarthe.Official/",
        "https://dipr.karnataka.gov.in/",
        "https://cm.karnataka.gov.in/en",
        "https://notkarnatakavarthe.org/",
        "https://karnatakavarthe.org.in/",
        "https://karnatakavarthe.com/",
    ):
        assert bl.match(url) is None, url


def test_the_karnatakavarthe_row_blocks_one_whole_domain_and_no_paths(repo_root):
    bl = load_blocklist(repo_root / "blocklist.csv")
    assert {d for d in bl.domains if "karnatakavarthe" in d} == {"karnatakavarthe.org"}
    assert [(d, p) for d, p in bl.paths if "karnatakavarthe" in d] == []
    assert bl.problems == []
