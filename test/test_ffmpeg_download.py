import importlib.util
import io
import os
import pathlib
import tempfile
import unittest
import urllib.error
import urllib.request
import zipfile
from unittest import mock


MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "CialloHEVC.py"
SPEC = importlib.util.spec_from_file_location("ciallo_hevc", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeResponse(io.BytesIO):
    def __init__(self, body, headers=None, status=200):
        super().__init__(body)
        self.headers = headers or {}
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


class FFmpegDownloadHelperTests(unittest.TestCase):
    def test_system_proxy_opener_uses_windows_proxy_mapping(self):
        with mock.patch.object(urllib.request, "getproxies", return_value={
                "http": "http://127.0.0.1:2080",
                "https": "http://127.0.0.1:2080",
        }) as getproxies, mock.patch.object(
                urllib.request, "build_opener", return_value="opener") as build:
            opener, proxies = MODULE.build_system_proxy_opener()

        self.assertEqual(opener, "opener")
        self.assertEqual(proxies["https"], "http://127.0.0.1:2080")
        getproxies.assert_called_once_with()
        build.assert_called_once()
        self.assertIsInstance(build.call_args.args[0], MODULE.urllib.request.ProxyHandler)

    def test_release_page_fallback_finds_full_build_when_api_is_rate_limited(self):
        page = (
            b'<meta name="route-pattern" content="/:user/releases/tag/*name">'
            b'<meta property="og:url" content="/GyanD/codexffmpeg/releases/tag/9.0.1">'
            b'<include-fragment src="https://github.com/GyanD/codexffmpeg/releases/expanded_assets/9.0.1">'
        )
        assets = b'<a href="/GyanD/codexffmpeg/releases/download/9.0.1/ffmpeg-9.0.1-full_build.zip">'

        class Opener:
            def __init__(self):
                self.urls = []

            def open(self, request, timeout=0):
                url = request.full_url
                self.urls.append(url)
                if url.endswith("api.github.com/repos/GyanD/codexffmpeg/releases/latest"):
                    raise urllib.error.HTTPError(
                        url, 403, "rate limit exceeded", {}, io.BytesIO(b"rate limit exceeded"))
                return FakeResponse(page if "releases/latest" in url else assets)

        opener = Opener()
        result = MODULE.resolve_ffmpeg_release(opener, use_proxy=False, proxy_prefix="")

        self.assertEqual(result["tag_name"], "9.0.1")
        self.assertEqual(
            result["download_url"],
            "https://github.com/GyanD/codexffmpeg/releases/download/9.0.1/ffmpeg-9.0.1-full_build.zip",
        )
        self.assertEqual(
            opener.urls,
            [
                "https://api.github.com/repos/GyanD/codexffmpeg/releases/latest",
                "https://github.com/GyanD/codexffmpeg/releases/latest",
                "https://github.com/GyanD/codexffmpeg/releases/expanded_assets/9.0.1",
            ],
        )

    def test_stream_download_reads_with_supplied_opener(self):
        target = pathlib.Path(self.id().replace("/", "_") + ".zip")
        response = FakeResponse(b"abc", {"Content-Length": "3"})

        class Opener:
            def __init__(self):
                self.calls = []

            def open(self, request, timeout=0):
                self.calls.append((request.full_url, timeout))
                return response

        opener = Opener()
        try:
            MODULE.download_with_opener(opener, "https://example.test/file.zip", target, lambda *_: None)
            self.assertEqual(target.read_bytes(), b"abc")
            self.assertEqual(opener.calls, [("https://example.test/file.zip", 60)])
        finally:
            target.unlink(missing_ok=True)


class InstallFFmpegZipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.core_dir = os.path.join(self.tmp.name, "Core")
        os.makedirs(os.path.join(self.core_dir, "old_build", "bin"))
        self.sentinel = os.path.join(self.core_dir, "old_build", "bin", "ffmpeg.exe")
        with open(self.sentinel, "wb") as handle:
            handle.write(b"old ffmpeg")
        self.addCleanup(self.tmp.cleanup)

    def _make_zip(self, names):
        zip_path = os.path.join(self.tmp.name, "download.zip")
        with zipfile.ZipFile(zip_path, "w") as archive:
            for name in names:
                archive.writestr(name, b"new")
        return zip_path

    def test_valid_zip_replaces_core_and_returns_exe_path(self):
        zip_path = self._make_zip(["ffmpeg-9.0.1-full_build/bin/ffmpeg.exe",
                                   "ffmpeg-9.0.1-full_build/bin/ffprobe.exe"])

        result = MODULE.install_ffmpeg_zip(zip_path, self.core_dir)

        self.assertEqual(
            result,
            os.path.join(self.core_dir, "ffmpeg-9.0.1-full_build", "bin", "ffmpeg.exe"),
        )
        self.assertTrue(os.path.exists(result))
        self.assertFalse(os.path.exists(self.sentinel))
        self.assertFalse(os.path.exists(self.core_dir + ".new"))

    def test_broken_zip_keeps_existing_core(self):
        zip_path = os.path.join(self.tmp.name, "broken.zip")
        with open(zip_path, "wb") as handle:
            handle.write(b"not a zip file")

        with self.assertRaises(zipfile.BadZipFile):
            MODULE.install_ffmpeg_zip(zip_path, self.core_dir)

        self.assertTrue(os.path.exists(self.sentinel))
        self.assertFalse(os.path.exists(self.core_dir + ".new"))

    def test_zip_without_ffmpeg_exe_keeps_existing_core(self):
        zip_path = self._make_zip(["ffmpeg-9.0.1-full_build/README.txt"])

        self.assertIsNone(MODULE.install_ffmpeg_zip(zip_path, self.core_dir))
        self.assertTrue(os.path.exists(self.sentinel))
        self.assertFalse(os.path.exists(self.core_dir + ".new"))


if __name__ == "__main__":
    unittest.main()
