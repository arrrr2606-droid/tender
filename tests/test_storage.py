from monitor.models import Lot
from monitor.storage import Storage


def test_dedup_and_cross_source(tmp_path):
    st = Storage(tmp_path / "db.sqlite")
    a = Lot(source="eis", id="0194200000526005085", eis_number="0194200000526005085", title="x", url="u")
    assert not st.is_seen(a)
    st.mark(a, notified=True)
    assert st.is_seen(a)
    dup = Lot(source="fabrikant", id="679621327", eis_number="0194200000526005085", title="x", url="u")
    assert st.is_seen(dup)
    other = Lot(source="fabrikant", id="1", title="y", url="u")
    assert not st.is_seen(other)
