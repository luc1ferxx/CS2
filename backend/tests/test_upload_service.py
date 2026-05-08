import unittest

from app.services.upload_service import (
    DemoUploadValidationError,
    MAX_DEMO_UPLOAD_BYTES,
    validate_demo_upload,
)


class DemoUploadValidationTest(unittest.TestCase):
    def test_accepts_dem_and_zip_under_size_limit(self) -> None:
        self.assertEqual(validate_demo_upload("match.dem", 1024), ".dem")
        self.assertEqual(validate_demo_upload("MATCH.ZIP", 1024), ".zip")

    def test_rejects_unsupported_extensions(self) -> None:
        with self.assertRaises(DemoUploadValidationError):
            validate_demo_upload("match.rar", 1024)

    def test_rejects_files_over_size_limit(self) -> None:
        with self.assertRaises(DemoUploadValidationError):
            validate_demo_upload("match.dem", MAX_DEMO_UPLOAD_BYTES + 1)


if __name__ == "__main__":
    unittest.main()
