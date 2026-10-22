from callgraph.fixes import _vkey, edits_for, lock_blocks

LOCK = '''version = 1

[[package]]
name = "certifi"
version = "2026.6.17"
source = { registry = "https://pypi.org/simple" }
wheels = [
    { url = "https://x/certifi-2026.6.17.whl", hash = "sha256:aa" },
]

[[package]]
name = "idna"
version = "3.10"
source = { registry = "https://pypi.org/simple" }
'''


def test_blocks_and_single_package_edit():
    assert set(lock_blocks(LOCK)) == {"certifi@2026.6.17", "idna@3.10"}
    new = LOCK.replace("2026.6.17", "2026.10.19").replace("sha256:aa", "sha256:bb")
    (old_block, new_block), = edits_for(LOCK, new, "certifi")
    assert 'version = "2026.6.17"' in old_block and 'version = "2026.10.19"' in new_block
    assert "idna" not in old_block
    assert LOCK.replace(old_block, new_block, 1) == new  # edits reproduce the new lock exactly


def test_no_edit_when_package_absent_or_unchanged():
    assert edits_for(LOCK, LOCK, "certifi") == []
    assert edits_for(LOCK, LOCK, "nope") == []


def test_version_ordering():
    assert _vkey("2026.10.19") > _vkey("2026.6.17")
    assert _vkey("3.1.6") > _vkey("3.1.4")
