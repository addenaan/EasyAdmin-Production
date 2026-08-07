"""Read-only Super Admin file browser for the Easy Admin persistent disk.

The browser is deliberately restricted to a configured persistent-disk root and
never permits uploads, edits, moves, renames, extraction, or deletion.
"""
from __future__ import annotations

import json
import os
import secrets
import stat
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import quote

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    stream_with_context,
    url_for,
)
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

FILE_BROWSER_PREFIX = "/admin/file_browser"
DEFAULT_PAGE_SIZE = 250
MAX_PAGE_SIZE = 500
DEFAULT_REAUTH_SECONDS = 600
DEFAULT_MAX_EXPORT_BYTES = 20 * 1024 * 1024 * 1024
DEFAULT_MAX_EXPORT_FILES = 200_000
STREAM_CHUNK_SIZE = 128 * 1024

_FORBIDDEN_ROOTS = {
    Path("/"),
    Path("/bin"),
    Path("/boot"),
    Path("/dev"),
    Path("/etc"),
    Path("/home"),
    Path("/lib"),
    Path("/lib64"),
    Path("/opt"),
    Path("/proc"),
    Path("/root"),
    Path("/run"),
    Path("/sbin"),
    Path("/srv"),
    Path("/sys"),
    Path("/tmp"),
    Path("/usr"),
    Path("/var"),
}

_RATE_BUCKETS: Dict[str, List[float]] = {}
_RATE_LOCK = threading.Lock()


class FileBrowserConfigurationError(RuntimeError):
    """Raised when the persistent-disk root is unavailable or unsafe."""


class FileBrowserPathError(ValueError):
    """Raised when a requested relative path is unsafe or inaccessible."""


class FileBrowserExportLimitError(RuntimeError):
    """Raised when an export exceeds configured file-count or size limits."""


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _persistent_disk_root() -> Path:
    configured = (os.environ.get("EASYADMIN_PERSISTENT_DISK_ROOT") or "").strip()
    if not configured:
        upload_folder = str(current_app.config.get("UPLOAD_FOLDER") or "").strip()
        if upload_folder and os.path.isabs(upload_folder):
            upload_path = Path(upload_folder).expanduser()
            configured = str(upload_path.parent if upload_path.name.lower() == "uploads" else upload_path)

    if not configured:
        raise FileBrowserConfigurationError(
            "Set EASYADMIN_PERSISTENT_DISK_ROOT to the absolute Render persistent-disk mount path."
        )

    root = Path(configured).expanduser()
    if not root.is_absolute():
        raise FileBrowserConfigurationError("EASYADMIN_PERSISTENT_DISK_ROOT must be an absolute path.")

    try:
        root = root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise FileBrowserConfigurationError("The configured persistent-disk directory does not exist.") from exc
    except OSError as exc:
        raise FileBrowserConfigurationError("The configured persistent-disk directory could not be resolved.") from exc

    if root in _FORBIDDEN_ROOTS or len(root.parts) < 3:
        raise FileBrowserConfigurationError("The configured persistent-disk root is too broad or is a protected system path.")
    if not root.is_dir():
        raise FileBrowserConfigurationError("The configured persistent-disk root is not a directory.")
    if not os.access(root, os.R_OK | os.X_OK):
        raise FileBrowserConfigurationError("The configured persistent-disk root is not readable by Easy Admin.")
    return root


def _clean_relative_path(value: Optional[str]) -> str:
    text = (value or "").replace("\\", "/").strip()
    if "\x00" in text:
        raise FileBrowserPathError("The requested path is invalid.")
    while text.startswith("./"):
        text = text[2:]
    text = text.strip("/")
    if not text:
        return ""

    pure = PurePosixPath(text)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise FileBrowserPathError("The requested path is invalid.")
    if len(text) > 4096:
        raise FileBrowserPathError("The requested path is too long.")
    return pure.as_posix()


def _resolve_inside_root(root: Path, relative_path: Optional[str], *, require_exists: bool = True) -> Tuple[Path, str]:
    clean = _clean_relative_path(relative_path)
    candidate = root.joinpath(*PurePosixPath(clean).parts) if clean else root
    try:
        resolved = candidate.resolve(strict=require_exists)
    except FileNotFoundError as exc:
        raise FileBrowserPathError("The requested file or folder no longer exists.") from exc
    except OSError as exc:
        raise FileBrowserPathError("The requested path could not be accessed.") from exc

    try:
        common = os.path.commonpath((str(root), str(resolved)))
    except ValueError as exc:
        raise FileBrowserPathError("The requested path is outside the persistent disk.") from exc
    if common != str(root):
        raise FileBrowserPathError("The requested path is outside the persistent disk.")

    # Do not traverse or download symbolic links, even when they currently point
    # inside the disk. This prevents later link-target changes from widening access.
    cursor = root
    for part in PurePosixPath(clean).parts:
        cursor = cursor / part
        try:
            if cursor.is_symlink():
                raise FileBrowserPathError("Symbolic links cannot be opened through the File Browser.")
        except OSError as exc:
            raise FileBrowserPathError("The requested path could not be inspected safely.") from exc
    return resolved, clean


def _relative_posix(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _safe_filename(name: str, fallback: str = "download") -> str:
    cleaned = secure_filename(name or "")
    return cleaned or fallback


def _format_timestamp(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _format_size(size: int) -> str:
    value = float(max(0, size or 0))
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value):,} {unit}"
            return f"{value:,.2f} {unit}"
        value /= 1024
    return f"{int(size):,} B"


def _client_identity() -> str:
    forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
    ip = forwarded or request.remote_addr or "unknown-ip"
    return f"{ip[:100]}|{str(session.get('username') or '').lower()}"


def _rate_limit(action: str, limit: int, window_seconds: int) -> Optional[Response]:
    now = time.time()
    key = f"{action}|{_client_identity()}"
    with _RATE_LOCK:
        recent = [ts for ts in _RATE_BUCKETS.get(key, []) if ts >= now - window_seconds]
        if len(recent) >= limit:
            retry_after = max(1, int(window_seconds - (now - recent[0])))
            _RATE_BUCKETS[key] = recent
            response = jsonify({
                "status": "error",
                "message": "Too many File Browser requests. Please wait and try again.",
                "retry_after": retry_after,
            })
            response.status_code = 429
            response.headers["Retry-After"] = str(retry_after)
            return response
        recent.append(now)
        _RATE_BUCKETS[key] = recent
        if len(_RATE_BUCKETS) > 2000:
            for stale_key in list(_RATE_BUCKETS)[:500]:
                _RATE_BUCKETS.pop(stale_key, None)
    return None


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers.setdefault("Vary", "Cookie")
    return response


def _list_directory(root: Path, target: Path, page: int, page_size: int) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    try:
        scanner = os.scandir(target)
    except OSError as exc:
        raise FileBrowserPathError("This folder could not be read.") from exc

    with scanner:
        for entry in scanner:
            try:
                info = entry.stat(follow_symlinks=False)
                is_link = stat.S_ISLNK(info.st_mode)
                is_dir = stat.S_ISDIR(info.st_mode)
                is_file = stat.S_ISREG(info.st_mode)
                relative = _relative_posix(root, Path(entry.path))
                rows.append({
                    "name": entry.name,
                    "relative_path": relative,
                    "is_directory": bool(is_dir and not is_link),
                    "is_file": bool(is_file and not is_link),
                    "is_symlink": is_link,
                    "size": int(info.st_size or 0) if is_file else 0,
                    "size_display": _format_size(int(info.st_size or 0)) if is_file else "—",
                    "modified": _format_timestamp(info.st_mtime),
                })
            except (FileNotFoundError, PermissionError, OSError):
                rows.append({
                    "name": entry.name,
                    "relative_path": _relative_posix(root, Path(entry.path)),
                    "is_directory": False,
                    "is_file": False,
                    "is_symlink": False,
                    "size": 0,
                    "size_display": "—",
                    "modified": "Unavailable",
                    "unavailable": True,
                })

    rows.sort(key=lambda item: (not item.get("is_directory", False), item["name"].casefold()))
    total = len(rows)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(1, page), total_pages)
    start = (page - 1) * page_size
    return {
        "entries": rows[start:start + page_size],
        "page": page,
        "page_size": page_size,
        "total_entries": total,
        "total_pages": total_pages,
    }


def _breadcrumbs(relative_path: str) -> List[Dict[str, str]]:
    crumbs = [{"name": "Persistent Disk", "path": ""}]
    cumulative: List[str] = []
    for part in PurePosixPath(relative_path).parts:
        cumulative.append(part)
        crumbs.append({"name": part, "path": "/".join(cumulative)})
    return crumbs


def _scan_export(root: Path, selected: Path) -> Dict[str, Any]:
    selected_relative = _relative_posix(root, selected) if selected != root else ""
    files: List[Dict[str, Any]] = []
    directories: List[str] = []
    skipped: List[Dict[str, str]] = []
    total_bytes = 0

    for current_root, dirnames, filenames in os.walk(selected, topdown=True, followlinks=False):
        current = Path(current_root)

        safe_dirs: List[str] = []
        for dirname in sorted(dirnames, key=str.casefold):
            path = current / dirname
            relative = _relative_posix(root, path)
            try:
                if path.is_symlink():
                    skipped.append({"path": relative, "reason": "symbolic link skipped"})
                    continue
                resolved = path.resolve(strict=True)
                if os.path.commonpath((str(root), str(resolved))) != str(root):
                    skipped.append({"path": relative, "reason": "path outside persistent disk skipped"})
                    continue
                directories.append(relative)
                safe_dirs.append(dirname)
            except (OSError, ValueError):
                skipped.append({"path": relative, "reason": "directory could not be inspected"})
        dirnames[:] = safe_dirs

        for filename in sorted(filenames, key=str.casefold):
            path = current / filename
            relative = _relative_posix(root, path)
            try:
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode):
                    skipped.append({"path": relative, "reason": "symbolic link skipped"})
                    continue
                if not stat.S_ISREG(info.st_mode):
                    skipped.append({"path": relative, "reason": "non-regular file skipped"})
                    continue
                resolved = path.resolve(strict=True)
                if os.path.commonpath((str(root), str(resolved))) != str(root):
                    skipped.append({"path": relative, "reason": "path outside persistent disk skipped"})
                    continue
                size = int(info.st_size or 0)
                total_bytes += size
                files.append({
                    "path": relative,
                    "size": size,
                    "modified_utc": datetime.fromtimestamp(info.st_mtime, tz=timezone.utc).isoformat(),
                })
            except (FileNotFoundError, PermissionError, OSError, ValueError):
                skipped.append({"path": relative, "reason": "file could not be inspected"})

    max_files = _env_int("EASYADMIN_FILE_BROWSER_MAX_EXPORT_FILES", DEFAULT_MAX_EXPORT_FILES, 1)
    max_bytes = _env_int("EASYADMIN_FILE_BROWSER_MAX_EXPORT_BYTES", DEFAULT_MAX_EXPORT_BYTES, 1)
    if len(files) > max_files:
        raise FileBrowserExportLimitError(
            f"The export contains {len(files):,} files, above the configured limit of {max_files:,}."
        )
    if total_bytes > max_bytes:
        raise FileBrowserExportLimitError(
            f"The export contains {_format_size(total_bytes)}, above the configured limit of {_format_size(max_bytes)}."
        )

    return {
        "selected_path": selected_relative,
        "files": files,
        "directories": directories,
        "skipped": skipped,
        "file_count": len(files),
        "folder_count": len(directories) + 1,
        "total_bytes": total_bytes,
        "total_size_display": _format_size(total_bytes),
    }


def _archive_prefix(selected_relative: str, full_disk: bool) -> str:
    if full_disk:
        return "persistent_disk"
    name = PurePosixPath(selected_relative).name or "persistent_disk"
    return _safe_filename(name, "folder")


def _zip_stream(
    root: Path,
    scan: Dict[str, Any],
    *,
    full_disk: bool,
    completion_state: Dict[str, Any],
) -> Iterable[bytes]:
    read_fd, write_fd = os.pipe()
    prefix = _archive_prefix(scan["selected_path"], full_disk)
    started_utc = datetime.now(timezone.utc).isoformat()

    def producer() -> None:
        skipped = list(scan["skipped"])
        written_files = 0
        written_bytes = 0
        try:
            with os.fdopen(write_fd, "wb", buffering=0) as sink:
                with zipfile.ZipFile(sink, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                    root_info = zipfile.ZipInfo(f"{prefix}/")
                    root_info.external_attr = (0o40755 & 0xFFFF) << 16
                    archive.writestr(root_info, b"")

                    for relative_dir in scan["directories"]:
                        archive_relative = PurePosixPath(relative_dir)
                        if scan["selected_path"]:
                            try:
                                archive_relative = archive_relative.relative_to(PurePosixPath(scan["selected_path"]))
                            except ValueError:
                                continue
                        archive_name = f"{prefix}/{archive_relative.as_posix().rstrip('/')}/"
                        info = zipfile.ZipInfo(archive_name)
                        info.external_attr = (0o40755 & 0xFFFF) << 16
                        archive.writestr(info, b"")

                    for metadata in scan["files"]:
                        relative = metadata["path"]
                        try:
                            source, _ = _resolve_inside_root(root, relative)
                            if not source.is_file():
                                raise FileNotFoundError(relative)
                            archive_relative = PurePosixPath(relative)
                            if scan["selected_path"]:
                                archive_relative = archive_relative.relative_to(PurePosixPath(scan["selected_path"]))
                            archive_name = f"{prefix}/{archive_relative.as_posix()}"
                            archive.write(source, archive_name)
                            written_files += 1
                            written_bytes += int(metadata.get("size") or 0)
                        except Exception as exc:  # file may change while the archive is generated
                            skipped.append({"path": relative, "reason": f"skipped during archive generation: {type(exc).__name__}"})

                    completed_utc = datetime.now(timezone.utc).isoformat()
                    manifest = {
                        "export_type": "entire_persistent_disk" if full_disk else "folder",
                        "persistent_disk_root": "Persistent Disk",
                        "selected_relative_path": scan["selected_path"],
                        "started_utc": started_utc,
                        "completed_utc": completed_utc,
                        "files_discovered": scan["file_count"],
                        "files_written": written_files,
                        "folders_discovered": scan["folder_count"],
                        "bytes_discovered": scan["total_bytes"],
                        "bytes_written_estimate": written_bytes,
                        "files": scan["files"],
                        "skipped": skipped,
                        "database_included": False,
                        "note": "This archive contains persistent-disk files only. The PostgreSQL database is not included.",
                    }
                    archive.writestr(
                        "_easyadmin_export_manifest.json",
                        json.dumps(manifest, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
                    )
            completion_state.update({
                "status": "success",
                "written_files": written_files,
                "written_bytes": written_bytes,
                "skipped_count": len(skipped),
            })
        except BrokenPipeError:
            completion_state.update({"status": "cancelled", "error": "client disconnected"})
        except Exception as exc:
            completion_state.update({"status": "failure", "error": f"{type(exc).__name__}: {exc}"})
            try:
                os.close(write_fd)
            except OSError:
                pass

    thread = threading.Thread(target=producer, name="easyadmin-file-browser-zip", daemon=True)
    thread.start()

    try:
        with os.fdopen(read_fd, "rb", buffering=0) as source:
            while True:
                chunk = source.read(STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass
        thread.join(timeout=10)


def register_file_browser(
    app: Any,
    *,
    log_action: Callable[..., Any],
    log_security_event: Callable[..., Any],
    get_db_connection: Callable[[], Any],
) -> None:
    """Register the read-only Super Admin File Browser on the main Flask app."""
    if app.extensions.get("easyadmin_file_browser_registered"):
        return

    blueprint = Blueprint("file_browser", __name__, template_folder="../templates")

    def require_superadmin(json_response: bool = False):
        if session.get("is_superadmin"):
            return None
        details = {
            "path": request.path,
            "method": request.method,
            "endpoint": request.endpoint,
            "message": "Super Admin access is required for the persistent-disk File Browser.",
        }
        try:
            log_security_event("File Browser Access Denied", details, result="blocked")
        except Exception:
            pass
        if json_response or request.is_json:
            return jsonify({"status": "error", "message": "Super Admin access is required."}), 403
        return Response("Access Denied: Super Admin privileges are required.", status=403, mimetype="text/plain")

    def config_error_response(exc: Exception, json_response: bool = False):
        message = str(exc)
        try:
            log_action(
                "File Browser",
                "File Browser Configuration Error",
                message,
                result="failure",
                event_type="security",
            )
        except Exception:
            pass
        if json_response:
            return jsonify({"status": "error", "message": message}), 503
        return render_template(
            "file_browser.html",
            session=session,
            configuration_error=message,
            entries=[],
            breadcrumbs=[{"name": "Persistent Disk", "path": ""}],
            current_path="",
            parent_path=None,
            page=1,
            total_pages=1,
            total_entries=0,
            page_size=DEFAULT_PAGE_SIZE,
            reauth_valid=False,
            reauth_seconds=DEFAULT_REAUTH_SECONDS,
        ), 503

    @blueprint.route(FILE_BROWSER_PREFIX, methods=["GET"])
    def file_browser_index():
        denied = require_superadmin()
        if denied:
            return denied
        try:
            root = _persistent_disk_root()
            target, current_relative = _resolve_inside_root(root, request.args.get("path"))
            if not target.is_dir():
                return redirect(url_for("file_browser.file_browser_index", path=str(PurePosixPath(current_relative).parent)))
            page = max(1, int(request.args.get("page", "1") or 1))
            page_size = min(MAX_PAGE_SIZE, max(25, int(request.args.get("page_size", DEFAULT_PAGE_SIZE) or DEFAULT_PAGE_SIZE)))
            listing = _list_directory(root, target, page, page_size)
            parent_path = None
            if current_relative:
                parent = PurePosixPath(current_relative).parent
                parent_path = "" if str(parent) == "." else parent.as_posix()
            reauth_until = float(session.get("file_browser_reauth_until") or 0)
            reauth_seconds = _env_int("EASYADMIN_FILE_BROWSER_REAUTH_SECONDS", DEFAULT_REAUTH_SECONDS, 60)
            try:
                log_action(
                    "File Browser",
                    "File Browser Opened",
                    {"relative_path": current_relative or "/", "entries": listing["total_entries"]},
                    record_type="persistent_disk_folder",
                    record_id=current_relative or "/",
                )
            except Exception:
                pass
            return render_template(
                "file_browser.html",
                session=session,
                configuration_error=None,
                entries=listing["entries"],
                breadcrumbs=_breadcrumbs(current_relative),
                current_path=current_relative,
                parent_path=parent_path,
                page=listing["page"],
                total_pages=listing["total_pages"],
                total_entries=listing["total_entries"],
                page_size=listing["page_size"],
                reauth_valid=reauth_until >= time.time(),
                reauth_seconds=reauth_seconds,
            )
        except (FileBrowserConfigurationError, FileBrowserPathError) as exc:
            if isinstance(exc, FileBrowserConfigurationError):
                return config_error_response(exc)
            try:
                log_security_event(
                    "Blocked File Browser Path",
                    {"requested_path": request.args.get("path"), "reason": str(exc)},
                    result="blocked",
                )
            except Exception:
                pass
            return Response(str(exc), status=400, mimetype="text/plain")

    @blueprint.route(f"{FILE_BROWSER_PREFIX}/summary", methods=["GET"])
    def file_browser_summary():
        denied = require_superadmin(json_response=True)
        if denied:
            return denied
        limited = _rate_limit("summary", 30, 300)
        if limited:
            return limited
        try:
            root = _persistent_disk_root()
            target, relative = _resolve_inside_root(root, request.args.get("path"))
            if not target.is_dir():
                return jsonify({"status": "error", "message": "Only folders can be summarised."}), 400
            scan = _scan_export(root, target)
            return _no_store(jsonify({
                "status": "success",
                "relative_path": relative,
                "file_count": scan["file_count"],
                "folder_count": scan["folder_count"],
                "total_bytes": scan["total_bytes"],
                "total_size": scan["total_size_display"],
                "skipped_count": len(scan["skipped"]),
                "database_included": False,
            }))
        except (FileBrowserConfigurationError, FileBrowserPathError, FileBrowserExportLimitError) as exc:
            return _no_store(jsonify({"status": "error", "message": str(exc)})), 400

    @blueprint.route(f"{FILE_BROWSER_PREFIX}/download", methods=["GET"])
    def file_browser_download_file():
        denied = require_superadmin()
        if denied:
            return denied
        limited = _rate_limit("download_file", 120, 300)
        if limited:
            return limited
        try:
            root = _persistent_disk_root()
            target, relative = _resolve_inside_root(root, request.args.get("path"))
            if not target.is_file():
                return Response("The requested path is not a downloadable file.", status=400, mimetype="text/plain")
            info = target.stat()
            try:
                log_action(
                    "File Browser",
                    "File Downloaded",
                    {"relative_path": relative, "size": int(info.st_size or 0)},
                    record_type="persistent_disk_file",
                    record_id=relative,
                )
            except Exception:
                pass
            response = send_file(
                target,
                as_attachment=True,
                download_name=_safe_filename(target.name, "file"),
                conditional=False,
                max_age=0,
            )
            return _no_store(response)
        except (FileBrowserConfigurationError, FileBrowserPathError) as exc:
            try:
                log_security_event(
                    "Blocked File Browser Path",
                    {"requested_path": request.args.get("path"), "reason": str(exc)},
                    result="blocked",
                )
            except Exception:
                pass
            return Response(str(exc), status=400, mimetype="text/plain")

    @blueprint.route(f"{FILE_BROWSER_PREFIX}/reauthenticate", methods=["POST"])
    def file_browser_reauthenticate():
        denied = require_superadmin(json_response=True)
        if denied:
            return denied
        limited = _rate_limit("reauthenticate", 5, 900)
        if limited:
            try:
                log_security_event("File Browser Reauthentication Rate Limited", {"username": session.get("username")}, result="blocked")
            except Exception:
                pass
            return limited

        data = request.get_json(silent=True) or request.form
        password = str(data.get("password") or "")
        if not password:
            return _no_store(jsonify({"status": "error", "message": "Enter your Easy Admin password."})), 400

        conn = get_db_connection()
        try:
            user = conn.execute(
                "SELECT username, password_hash, is_superadmin FROM users WHERE username = ?",
                (session.get("username"),),
            ).fetchone()
        finally:
            conn.close()

        valid = bool(user and user["is_superadmin"] and check_password_hash(user["password_hash"], password))
        if not valid:
            try:
                log_security_event(
                    "File Browser Reauthentication Failed",
                    {"username": session.get("username")},
                    result="failure",
                )
            except Exception:
                pass
            return _no_store(jsonify({"status": "error", "message": "Password verification failed."})), 401

        reauth_seconds = _env_int("EASYADMIN_FILE_BROWSER_REAUTH_SECONDS", DEFAULT_REAUTH_SECONDS, 60)
        session["file_browser_reauth_until"] = time.time() + reauth_seconds
        session.modified = True
        try:
            log_action(
                "File Browser",
                "File Browser Reauthenticated",
                {"valid_for_seconds": reauth_seconds},
                result="success",
                event_type="security",
            )
        except Exception:
            pass
        return _no_store(jsonify({
            "status": "success",
            "message": "Password verified.",
            "valid_for_seconds": reauth_seconds,
        }))

    @blueprint.route(f"{FILE_BROWSER_PREFIX}/archive", methods=["POST"])
    def file_browser_archive():
        denied = require_superadmin()
        if denied:
            return denied
        limited = _rate_limit("archive", 10, 900)
        if limited:
            return limited

        full_disk = str(request.form.get("full_disk") or "").strip().lower() in {"1", "true", "yes", "on"}
        requested_path = "" if full_disk else request.form.get("path")
        if full_disk and float(session.get("file_browser_reauth_until") or 0) < time.time():
            try:
                log_security_event(
                    "Persistent Disk Export Reauthentication Required",
                    {"username": session.get("username")},
                    result="blocked",
                )
            except Exception:
                pass
            return Response("Password reauthentication is required before downloading the entire persistent disk.", status=403, mimetype="text/plain")

        try:
            root = _persistent_disk_root()
            target, relative = _resolve_inside_root(root, requested_path)
            if not target.is_dir():
                return Response("Only folders can be downloaded as archives.", status=400, mimetype="text/plain")
            if target == root:
                full_disk = True
                if float(session.get("file_browser_reauth_until") or 0) < time.time():
                    return Response("Password reauthentication is required before downloading the entire persistent disk.", status=403, mimetype="text/plain")
            scan = _scan_export(root, target)
        except (FileBrowserConfigurationError, FileBrowserPathError, FileBrowserExportLimitError) as exc:
            try:
                log_action(
                    "File Browser",
                    "Persistent Disk Export Failed" if full_disk else "Folder Archive Download Failed",
                    {"relative_path": requested_path or "/", "reason": str(exc)},
                    result="failure",
                )
            except Exception:
                pass
            return Response(str(exc), status=400, mimetype="text/plain")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if full_disk:
            filename = f"easyadmin_persistent_disk_{timestamp}.zip"
            action_started = "Persistent Disk Export Started"
            action_completed = "Persistent Disk Export Completed"
            action_failed = "Persistent Disk Export Failed"
        else:
            folder_name = _safe_filename(PurePosixPath(relative).name, "folder")
            filename = f"{folder_name}_{timestamp}.zip"
            action_started = "Folder Archive Download Started"
            action_completed = "Folder Archive Download Completed"
            action_failed = "Folder Archive Download Failed"

        try:
            log_action(
                "File Browser",
                action_started,
                {
                    "relative_path": relative or "/",
                    "file_count": scan["file_count"],
                    "folder_count": scan["folder_count"],
                    "total_bytes": scan["total_bytes"],
                },
                record_type="persistent_disk_export",
                record_id=relative or "/",
            )
        except Exception:
            pass

        completion_state: Dict[str, Any] = {"status": "running"}

        @stream_with_context
        def generate():
            try:
                yield from _zip_stream(root, scan, full_disk=full_disk, completion_state=completion_state)
            finally:
                status = completion_state.get("status")
                try:
                    if status == "success":
                        log_action(
                            "File Browser",
                            action_completed,
                            {
                                "relative_path": relative or "/",
                                "files_written": completion_state.get("written_files", 0),
                                "bytes_written_estimate": completion_state.get("written_bytes", 0),
                                "skipped_count": completion_state.get("skipped_count", 0),
                            },
                            record_type="persistent_disk_export",
                            record_id=relative or "/",
                        )
                    else:
                        log_action(
                            "File Browser",
                            action_failed,
                            {
                                "relative_path": relative or "/",
                                "reason": completion_state.get("error", "archive stream did not complete"),
                                "status": status,
                            },
                            result="failure",
                            record_type="persistent_disk_export",
                            record_id=relative or "/",
                        )
                except Exception:
                    pass

        response = Response(generate(), mimetype="application/zip", direct_passthrough=True)
        response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(filename)}"
        response.headers["X-Accel-Buffering"] = "no"
        return _no_store(response)

    app.register_blueprint(blueprint)
    app.extensions["easyadmin_file_browser_registered"] = True
