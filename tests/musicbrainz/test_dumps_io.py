import io
import tarfile

import httpx

from needledrop.musicbrainz.dumps import download_file, extract_tarball


def test_download_file_streams(tmp_path):
    def handler(request):
        return httpx.Response(200, content=b"hello-dump")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    dest = tmp_path / "sub" / "file.bin"
    out = download_file("https://example.test/file.bin", dest, client=client)
    assert out == dest
    assert dest.read_bytes() == b"hello-dump"
    assert not client.is_closed  # caller-provided client must stay open


def test_download_file_raises_on_error(tmp_path):
    import pytest

    def handler(request):
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        download_file("https://example.test/missing", tmp_path / "x", client=client)


def test_extract_tarball_returns_mbdump_dir(tmp_path):
    tarball = tmp_path / "mbdump.tar.bz2"
    with tarfile.open(tarball, "w:bz2") as tar:
        data = b"1\tNine Inch Nails\n"
        info = tarfile.TarInfo("mbdump/artist")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    mbdump = extract_tarball(tarball, tmp_path / "out")
    assert mbdump.name == "mbdump"
    assert (mbdump / "artist").read_bytes() == b"1\tNine Inch Nails\n"
