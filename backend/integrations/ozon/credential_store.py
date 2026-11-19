"""File-backed Ozon credentials compatible with legacy .env.<store> readers."""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping


_STORE_ID = re.compile(r"[a-zA-Z0-9_-]{1,48}\Z")
_ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _current_user_sid() -> str:
    try:
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            check=True, capture_output=True,
        )
        output=result.stdout
        if not isinstance(output,bytes):
            raise ValueError('Missing native SID output')
        matches=re.findall(rb'S-1-(?:[0-9]+-)*[0-9]+',output)
        if len(matches)!=1:
            raise ValueError("invalid SID")
        return matches[0].decode('ascii')
    except (OSError, ValueError, IndexError, StopIteration, subprocess.CalledProcessError):
        raise OSError("Could not identify current Windows user for private file permissions") from None


def restrict_private_file(
    path: Path, *, platform: str | None = None,
    acl_runner: Callable[[list[str]], Any] | None = None,
    principal: str | None = None,
) -> None:
    if (platform or os.name) != "nt":
        path.chmod(0o600)
        return
    try:
        sid = principal or _current_user_sid()
        args = ["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(F)"]
        result = (acl_runner or _run_acl)(args)
        if getattr(result, "returncode", 0) != 0:
            raise OSError("icacls failed")
    except Exception:
        raise OSError("Could not restrict local file permissions") from None


def _run_acl(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, capture_output=True, check=False)


class CredentialStore:
    def __init__(
        self, root: Path, *, acl_runner: Callable[[list[str]], Any] | None = None,
        platform: str | None = None, principal: str | None = None,
    ) -> None:
        self.root = Path(root)
        self.acl_runner = acl_runner
        self.platform = platform
        self.principal = principal

    def path(self, shop: Mapping[str, Any]) -> Path:
        store_id = str(shop.get("id") or "")
        if not _STORE_ID.fullmatch(store_id):
            raise ValueError("Invalid store ID")
        return self.root / "ozon-adapter" / f".env.{store_id}"

    def _secure(self, path: Path) -> None:
        restrict_private_file(
            path, platform=self.platform, acl_runner=self.acl_runner,
            principal=self.principal,
        )

    def read(self, shop: Mapping[str, Any]) -> dict[str, str]:
        path = self.path(shop)
        values: dict[str, str] = {}
        if path.is_file():
            self._secure(path)
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
        for name in ("client_id_env", "api_key_env"):
            key = str(shop.get(name) or "")
            if key and os.getenv(key):
                values.setdefault(key, str(os.getenv(key)))
        return values

    def write(self, shop: Mapping[str, Any], client_id: str, api_key: str) -> None:
        path = self.path(shop)
        keys = [str(shop.get(name) or "") for name in ("client_id_env", "api_key_env")]
        values = [client_id.strip(), api_key.strip()]
        if any(not _ENV_KEY.fullmatch(key) for key in keys):
            raise ValueError("Invalid credential environment key")
        if any(not value or "\n" in value or "\r" in value for value in values):
            raise ValueError("Invalid credential value")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, prefix=".env-tmp-", delete=False,
            ) as handle:
                handle.write(f"{keys[0]}={values[0]}\n{keys[1]}={values[1]}\n")
                temporary = Path(handle.name)
            self._secure(temporary)
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def delete(self, shop: Mapping[str, Any]) -> None:
        self.path(shop).unlink(missing_ok=True)
