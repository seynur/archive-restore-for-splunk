from archive_restore.log import kv_fields


def test_kv_fields_quotes_and_drops_none():
    line = kv_fields(a="x", b=None, c='has "quote" and=eq', d="multi\nline")
    assert line == 'a=x c="has \'quote\' and=eq" d="multi line"'


def test_kv_fields_cannot_forge_fields():
    line = kv_fields(index='x" user=admin status="restored')
    assert line == "index=\"x' user=admin status='restored\""
    assert line.count('"') == 2
