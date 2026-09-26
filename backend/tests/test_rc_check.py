import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

RC_CHECK = Path(__file__).resolve().parents[2] / "scripts" / "rc_check.sh"
BASH = shutil.which("bash")


@unittest.skipIf(BASH is None, "rc_check.sh needs bash")
class RcCheckHealthWaitTest(unittest.TestCase):
    """wait_for_health treats the 503 a degraded API answers as a health failure."""

    def wait_for_health(self, fake_curl: str) -> subprocess.CompletedProcess[str]:
        assert BASH is not None
        with tempfile.TemporaryDirectory() as directory:
            # A Windows checkout may carry CRLF endings, which bash cannot source.
            script = Path(directory) / "rc_check.sh"
            script.write_text(RC_CHECK.read_text(encoding="utf-8").replace("\r\n", "\n"), newline="\n")
            commands = (
                "source ./rc_check.sh\n"
                f"curl() {{ {fake_curl}; }}\n"
                "sleep() { :; }\n"
                "wait_for_health http://api.test/health 3 0\n"
            )
            return subprocess.run(
                [BASH, "-c", commands],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )

    def test_ok_health_is_ready(self) -> None:
        result = self.wait_for_health("""printf '{"status":"ok"}\\n200'""")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('api health ok: {"status":"ok"}', result.stdout)

    def test_persistent_503_fails_as_degraded(self) -> None:
        result = self.wait_for_health("""printf '{"status":"degraded"}\\n503'""")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn('api health is degraded (HTTP 503 {"status":"degraded"})', result.stderr)
        self.assertIn("http://api.test/diagnostics", result.stderr)

    def test_unreachable_api_reports_the_last_status(self) -> None:
        # curl -w still prints 000 when the connection is refused.
        result = self.wait_for_health("printf '\\n000'; return 7")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("api health was not ready after 3 attempts", result.stderr)
        self.assertIn("last HTTP status 000", result.stderr)

    def test_a_200_without_an_ok_body_is_not_ready(self) -> None:
        result = self.wait_for_health("printf '<html>proxy page</html>\\n200'")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("last HTTP status 200", result.stderr)

    def test_sourcing_does_not_run_the_gate(self) -> None:
        result = self.wait_for_health("printf '{\"status\":\"ok\"}\\n200'")

        self.assertNotIn("verify.sh", result.stdout)
        self.assertNotIn("compose", result.stdout)


if __name__ == "__main__":
    unittest.main()
