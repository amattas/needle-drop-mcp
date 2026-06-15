import pytest

from needledrop.connectors.base import MusicConnector


def test_cannot_instantiate_abstract():
    with pytest.raises(TypeError):
        MusicConnector()


def test_concrete_subclass_must_implement_all():
    class Incomplete(MusicConnector):
        def get_storefront(self):
            return "us"

    with pytest.raises(TypeError):
        Incomplete()


def test_full_subclass_instantiates():
    class Stub(MusicConnector):
        def get_storefront(self):
            return "us"

        def iter_library_albums(self):
            return iter(())

        def iter_library_songs(self):
            return iter(())

        def iter_library_playlists(self):
            return iter(())

        def search_catalog(self, storefront, term, types=("albums", "songs"), limit=25):
            return None

    stub = Stub()
    assert stub.get_storefront() == "us"
    assert list(stub.iter_library_albums()) == []
