"""Compose files against backend/app/core/config.py.

Compose `--env-file` only feeds `${}` interpolation: a setting that is not
listed in a service's `environment:` never reaches the container, so writing it
into deploy/.env.production silently does nothing. Nineteen tuning settings were
in exactly that state (optimization review 3.5). These tests fail when a name
config.py reads is neither passed to the api/worker containers nor allow-listed
below, when compose passes a name config.py does not read (a typo there is just
as silent), and when the shared tuning block drifts from the code defaults.

They also pin the production shape the deploy kit relies on: postgres and redis
only on the internal `backend` network and a password on Redis (review 3.7), a
memory cap on the worker (3.5), per-commit image tags (3.4).
"""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "backend" / "app" / "core" / "config.py"
BASE_COMPOSE = REPO_ROOT / "docker-compose.yml"
PREVIEW_COMPOSE = REPO_ROOT / "docker-compose.preview.yml"
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"

TUNING_ANCHOR = "x-worker-tuning"
BACKEND_SERVICES = ("api", "worker")
ENV_READERS = {"getenv", "_bool_from_env", "_base_url_from_env"}
TRUTHY = {"1", "true", "yes", "on"}
UNEVALUATED = object()

# Settings config.py reads that are deliberately NOT forwarded into the api or
# worker container, each with the reason. Add a name here only when the code
# default is the only value that makes sense inside the container.
NOT_PASSED_THROUGH: dict[str, str] = {}


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that reads Compose's `!reset` / `!override` tags as plain values."""


def _construct_tagged(loader: yaml.SafeLoader, _suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)


_ComposeLoader.add_multi_constructor("!", _construct_tagged)


def load_compose(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        # A SafeLoader subclass: only the Compose tags are added.
        document = yaml.load(handle, Loader=_ComposeLoader)
    assert isinstance(document, dict), path
    return document


def service_environment(document: dict[str, Any], service: str) -> dict[str, str]:
    environment = document.get("services", {}).get(service, {}).get("environment") or {}
    assert isinstance(environment, dict), f"{service}.environment must be a mapping"
    return {str(key): str(value) for key, value in environment.items()}


def config_env_defaults() -> dict[str, Any]:
    """Every env name config.py reads, mapped to its code default.

    The default is the string `os.getenv` falls back to (or None), and a bool
    for `_bool_from_env`. A default built from module names (paths, size
    constants) is recorded as UNEVALUATED; only the tuning block compares
    defaults, and its settings all use literals.
    """
    tree = ast.parse(CONFIG_PATH.read_text(encoding="utf-8"))
    names: dict[str, Any] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        reader = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if reader not in ENV_READERS:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        default_node = node.args[1] if len(node.args) > 1 else None
        if default_node is None:
            default: Any = False if reader == "_bool_from_env" else None
        else:
            # Our own source, evaluated with no names but `str`: literals and
            # `str(<constant arithmetic>)` evaluate, anything else does not.
            try:
                default = eval(
                    compile(ast.Expression(default_node), str(CONFIG_PATH), "eval"),
                    {"__builtins__": {}, "str": str},
                )
            except (NameError, TypeError, AttributeError):
                default = UNEVALUATED
        names.setdefault(first.value, default)
    return names


class ComposePassthroughTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config_defaults = config_env_defaults()
        cls.base = load_compose(BASE_COMPOSE)
        cls.preview = load_compose(PREVIEW_COMPOSE)
        cls.prod = load_compose(PROD_COMPOSE)

    def passed_through(self) -> set[str]:
        return {name for service in BACKEND_SERVICES for name in service_environment(self.base, service)}

    def test_config_reads_settings(self) -> None:
        # Guards the parser above: a refactor of config.py must not leave the
        # other tests checking an empty set.
        self.assertGreater(len(self.config_defaults), 50)
        self.assertIn("PARSE_TIMEOUT_SECONDS", self.config_defaults)

    def test_every_setting_reaches_the_api_or_worker(self) -> None:
        missing = sorted(set(self.config_defaults) - self.passed_through() - set(NOT_PASSED_THROUGH))
        self.assertEqual(
            missing,
            [],
            "config.py reads these, but no api/worker `environment:` in docker-compose.yml "
            "passes them, so an operator's value never reaches the container. Add them to "
            f"`{TUNING_ANCHOR}` (or the service's environment), or to NOT_PASSED_THROUGH "
            "with the reason.",
        )

    def test_allow_list_entries_are_live(self) -> None:
        for name in NOT_PASSED_THROUGH:
            with self.subTest(name=name):
                self.assertIn(name, self.config_defaults, "config.py no longer reads it: drop the entry")
                self.assertNotIn(name, self.passed_through(), "compose passes it now: drop the entry")

    def test_compose_passes_only_settings_config_reads(self) -> None:
        for path, document in (
            (BASE_COMPOSE, self.base),
            (PREVIEW_COMPOSE, self.preview),
            (PROD_COMPOSE, self.prod),
        ):
            for service in BACKEND_SERVICES:
                with self.subTest(file=path.name, service=service):
                    unknown = sorted(set(service_environment(document, service)) - set(self.config_defaults))
                    self.assertEqual(unknown, [], "config.py never reads these: a typo, or a dead setting")

    def test_tuning_block_reaches_api_and_worker_with_code_defaults(self) -> None:
        tuning = self.base.get(TUNING_ANCHOR)
        self.assertIsInstance(tuning, dict)
        assert isinstance(tuning, dict)
        self.assertGreaterEqual(len(tuning), 19)
        for service in BACKEND_SERVICES:
            environment = service_environment(self.base, service)
            for name, value in tuning.items():
                with self.subTest(service=service, name=name):
                    self.assertEqual(environment.get(name), value, f"merge `<<: *worker-tuning` into {service}")

        for name, value in tuning.items():
            with self.subTest(name=name):
                match = re.fullmatch(r"\$\{([A-Z0-9_]+):-(.*)\}", str(value))
                self.assertIsNotNone(match, f"{name} must read `${{{name}:-<code default>}}`")
                assert match is not None
                self.assertEqual(match.group(1), name, "the interpolated variable must be the setting itself")
                self.assertIn(name, self.config_defaults)
                code_default = self.config_defaults[name]
                self.assertIsNot(code_default, UNEVALUATED, "give the tuning setting a literal default")
                compose_default = match.group(2)
                if isinstance(code_default, bool):
                    self.assertEqual(compose_default.strip().lower() in TRUTHY, code_default)
                else:
                    self.assertEqual(compose_default, code_default, "keep the fallback equal to config.py's")


class ProductionComposeIsolationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.preview = load_compose(PREVIEW_COMPOSE)
        cls.prod = load_compose(PROD_COMPOSE)

    def service(self, name: str) -> dict[str, Any]:
        service = self.prod["services"].get(name) or {}
        assert isinstance(service, dict)
        return service

    def test_database_and_redis_sit_only_on_the_internal_network(self) -> None:
        self.assertIs(self.prod["networks"]["backend"].get("internal"), True)
        for name in ("postgres", "redis"):
            with self.subTest(service=name):
                self.assertEqual(self.service(name).get("networks"), ["backend"])
        for name in BACKEND_SERVICES:
            with self.subTest(service=name):
                self.assertEqual(sorted(self.service(name).get("networks") or []), ["backend", "default"])
        for name in ("caddy", "frontend"):
            with self.subTest(service=name):
                self.assertNotIn("backend", self.service(name).get("networks") or [])

    def test_redis_requires_the_password_the_backend_urls_carry(self) -> None:
        for path, document in ((PREVIEW_COMPOSE, self.preview), (PROD_COMPOSE, self.prod)):
            with self.subTest(file=path.name):
                command = [str(part) for part in document["services"]["redis"]["command"]]
                self.assertIn("--requirepass", command)
                password = command[command.index("--requirepass") + 1]
                self.assertTrue(password.startswith("${REDIS_PASSWORD:?"), password)
        for name in BACKEND_SERVICES:
            with self.subTest(service=name):
                redis_url = service_environment(self.preview, name).get("REDIS_URL", "")
                self.assertRegex(redis_url, r"^redis://:\$\{REDIS_PASSWORD:\?[^}]*\}@redis:6379/")

    def test_images_are_tagged_per_commit(self) -> None:
        # scripts/deploy/deploy.sh exports DEPLOY_SHA and reuses these tags for
        # a rollback; `latest` is what it moves to every deploy that passed.
        expected = {
            "api": "cs2coach-backend:${DEPLOY_SHA:-latest}",
            "worker": "cs2coach-backend:${DEPLOY_SHA:-latest}",
            "frontend": "cs2coach-frontend:${DEPLOY_SHA:-latest}",
        }
        for name, image in expected.items():
            with self.subTest(service=name):
                self.assertEqual(self.service(name).get("image"), image)

    def test_worker_memory_is_capped_without_swap(self) -> None:
        worker = self.service("worker")
        self.assertIn("WORKER_MEM_LIMIT", str(worker.get("mem_limit")))
        self.assertEqual(worker.get("memswap_limit"), worker.get("mem_limit"))


if __name__ == "__main__":
    unittest.main()
