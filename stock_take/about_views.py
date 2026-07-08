import os
import re
import subprocess
from datetime import datetime
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.db import connection
from django.contrib.auth.models import User


_SCRIPT_RE = re.compile(r'<script(?:\s[^>]*)?>(.+?)</script>', re.DOTALL | re.IGNORECASE)

# First-party code fileset — shared by the breakdown counter and the commit-history
# charts so both measure the same thing.
_CODE_EXTENSIONS = {".py", ".html", ".css", ".js"}
_CODE_APP_DIRS = ("stock_take", "stock_taking", "material_generator", "templates", "order_generator_files", "static")
_CODE_EXCLUDE_DIRS = {"__pycache__", "migrations", "fonts"}
_CODE_EXCLUDE_FILES = {"bootstrap-icons.min.css"}


def _is_code_path(path):
    """True if a repo-relative git path is one of the first-party code files the
    breakdown counter (`_count_lines_of_code`) would include."""
    # Normalise git rename syntax: "old => new" and "dir/{old => new}/file".
    if "=>" in path:
        if "{" in path and "}" in path:
            pre, rest = path.split("{", 1)
            inner, post = rest.split("}", 1)
            path = pre + inner.split("=>")[-1].strip() + post
        else:
            path = path.split("=>")[-1].strip()
    path = path.strip().strip('"')
    parts = path.split("/")
    fname = parts[-1]
    if fname in _CODE_EXCLUDE_FILES:
        return False
    if any(seg in _CODE_EXCLUDE_DIRS for seg in parts):
        return False
    if os.path.splitext(fname)[1] not in _CODE_EXTENSIONS:
        return False
    # Must live under a tracked app dir, or be a root-level .py script.
    if len(parts) == 1:
        return fname.endswith(".py")
    return parts[0] in _CODE_APP_DIRS


def _count_lines_of_code():
    """Count lines of code in first-party application directories.

    JavaScript inside <script> tags in .html files is counted as JS, not HTML.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app_dirs = list(_CODE_APP_DIRS)
    extensions = _CODE_EXTENSIONS
    exclude = _CODE_EXCLUDE_DIRS | _CODE_EXCLUDE_FILES

    by_type = {}
    total_files = 0

    inline_js_lines = 0  # JS lines extracted from HTML <script> blocks

    for app_dir in app_dirs:
        full_dir = os.path.join(base_dir, app_dir)
        if not os.path.isdir(full_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(full_dir):
            dirnames[:] = [d for d in dirnames if d not in exclude]
            for fname in filenames:
                # Skip bundled/vendor files
                if fname in {"bootstrap-icons.min.css"}:
                    continue
                ext = os.path.splitext(fname)[1]
                if ext in extensions:
                    fpath = os.path.join(dirpath, fname)
                    try:
                        with open(fpath, encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                        lines = content.count('\n') + (1 if content and not content.endswith('\n') else 0)
                        by_type[ext] = by_type.get(ext, 0) + lines
                        total_files += 1

                        # For HTML files, count lines inside <script> tags as JS
                        if ext == ".html":
                            for m in _SCRIPT_RE.finditer(content):
                                script_body = m.group(1)
                                js_count = script_body.count('\n') + (1 if script_body and not script_body.endswith('\n') else 0)
                                inline_js_lines += js_count
                    except Exception:
                        pass

    # Count root-level .py files (manage.py, settings scripts, etc.)
    for fname in os.listdir(base_dir):
        fpath = os.path.join(base_dir, fname)
        if fname.endswith(".py") and os.path.isfile(fpath):
            try:
                with open(fpath, encoding="utf-8", errors="ignore") as f:
                    lines = len(f.readlines())
                by_type[".py"] = by_type.get(".py", 0) + lines
                total_files += 1
            except Exception:
                pass

    # Move inline JS lines from HTML to JS totals
    by_type[".html"] = max(by_type.get(".html", 0) - inline_js_lines, 0)
    by_type[".js"] = by_type.get(".js", 0) + inline_js_lines

    total_lines = sum(by_type.values())
    return total_lines, total_files, by_type


def _get_db_stats():
    """Return key record counts from the database."""
    from .models import (
        Order, Customer, Lead, PurchaseOrder, Invoice, PurchaseInvoice,
        StockItem, Supplier, Ticket, ActivityLog, AnthillSale, Remedial,
    )
    return {
        "orders": Order.objects.count(),
        "customers": Customer.objects.count(),
        "leads": Lead.objects.count(),
        "purchase_orders": PurchaseOrder.objects.count(),
        "sales_invoices": Invoice.objects.count(),
        "purchase_invoices": PurchaseInvoice.objects.count(),
        "stock_items": StockItem.objects.count(),
        "suppliers": Supplier.objects.count(),
        "tickets": Ticket.objects.count(),
        "activity_log": ActivityLog.objects.count(),
        "sales": AnthillSale.objects.count(),
        "remedials": Remedial.objects.count(),
        "users": User.objects.count(),
    }


def _fmt_size(num_bytes):
    """Format bytes into a human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def _get_media_stats():
    """List objects in DigitalOcean Spaces and return file count / size by folder."""
    import boto3

    endpoint = getattr(settings, "AWS_S3_ENDPOINT_URL", None)
    bucket = getattr(settings, "AWS_STORAGE_BUCKET_NAME", None)
    key_id = getattr(settings, "AWS_ACCESS_KEY_ID", None)
    secret = getattr(settings, "AWS_SECRET_ACCESS_KEY", None)
    prefix = getattr(settings, "AWS_LOCATION", "media")

    if not all([endpoint, bucket, key_id, secret]):
        return {"total_files": 0, "total_size": "0 B", "folders": []}

    try:
        session = boto3.session.Session()
        client = session.client(
            "s3",
            region_name=getattr(settings, "AWS_S3_REGION_NAME", "ams3"),
            endpoint_url=endpoint,
            aws_access_key_id=key_id,
            aws_secret_access_key=secret,
        )

        total_files = 0
        total_bytes = 0
        folder_stats = {}

        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix + "/"):
            for obj in page.get("Contents", []):
                # Strip the prefix to get relative path
                rel_key = obj["Key"][len(prefix):].lstrip("/")
                if not rel_key:
                    continue
                parts = rel_key.split("/")
                top = parts[0] if len(parts) > 1 else "(root)"
                size = obj.get("Size", 0)
                total_files += 1
                total_bytes += size
                if top not in folder_stats:
                    folder_stats[top] = {"files": 0, "bytes": 0}
                folder_stats[top]["files"] += 1
                folder_stats[top]["bytes"] += size

        folders = sorted(
            [
                {"name": k, "files": v["files"], "size": _fmt_size(v["bytes"])}
                for k, v in folder_stats.items()
            ],
            key=lambda x: x["name"],
        )
        return {
            "total_files": total_files,
            "total_size": _fmt_size(total_bytes),
            "folders": folders,
        }
    except Exception:
        return {"total_files": 0, "total_size": "0 B", "folders": []}


# Commits changing more than this many lines (added + removed) are treated as
# setup/vendor noise (initial imports, virtual_environment, bundled assets) and
# excluded from the commit history charts.
_MAX_COMMIT_LINES = 100000


def _get_commit_line_stats(anchor_total=None):
    """Return commit index with per-commit additions/removals from git history.

    Only first-party code files (see ``_is_code_path``) are counted, so the charts
    measure the same lines as the breakdown table. Commits touching more than
    ``_MAX_COMMIT_LINES`` code lines are excluded so bulk setup/vendor commits don't
    distort the charts. Each returned commit carries a running ``cumulative`` net
    line total in chronological order.

    When ``anchor_total`` is given, the cumulative series is shifted so its most
    recent point equals that value — net deltas miss the pre-history baseline and
    accrue per-file rounding, so this keeps the chart's endpoint in agreement with
    the measured current line count.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _get_commit_web_base():
        try:
            remote = subprocess.check_output(
                ["git", "remote", "get-url", "origin"],
                cwd=base_dir,
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
            return ""

        if remote.startswith("git@") and ":" in remote:
            host_part = remote.split("@", 1)[1]
            host, path = host_part.split(":", 1)
            remote = f"https://{host}/{path}"
        elif remote.startswith("ssh://git@"):
            remote = remote.replace("ssh://git@", "https://", 1)
            if ":" in remote.rsplit("/", 1)[-1]:
                remote = remote.replace(":", "/", 1)

        if remote.endswith(".git"):
            remote = remote[:-4]

        if not remote.startswith("http://") and not remote.startswith("https://"):
            return ""

        if "bitbucket.org" in remote:
            return remote.rstrip("/") + "/commits/"
        return remote.rstrip("/") + "/commit/"

    commit_web_base = _get_commit_web_base()

    try:
        # --numstat outputs per-file added/removed counts per commit.
        output = subprocess.check_output(
            ["git", "log", "--date=format:%Y-%m", "--pretty=tformat:COMMIT\t%H\t%h\t%ad", "--numstat"],
            cwd=base_dir,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return []

    commits = []
    current_added = 0
    current_removed = 0
    current_hash = ""
    current_short_hash = ""
    current_period = ""
    seen_commit_marker = False

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("COMMIT\t"):
            if seen_commit_marker:
                commits.append({
                    "number": len(commits) + 1,
                    "hash": current_hash,
                    "short_hash": current_short_hash,
                    "period": current_period,
                    "url": f"{commit_web_base}{current_hash}" if commit_web_base and current_hash else "",
                    "added": current_added,
                    "removed": current_removed,
                    "net": current_added - current_removed,
                })

            marker_parts = line.split("\t")
            current_hash = marker_parts[1] if len(marker_parts) > 1 else ""
            current_short_hash = marker_parts[2] if len(marker_parts) > 2 else current_hash[:8]
            raw_period = marker_parts[3] if len(marker_parts) > 3 else ""
            try:
                current_period = datetime.strptime(raw_period, "%Y-%m").strftime("%b %Y")
            except Exception:
                current_period = raw_period or "Unknown Period"
            seen_commit_marker = True
            current_added = 0
            current_removed = 0
            continue

        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add_str, rem_str, path = parts[0], parts[1], parts[2]
        if not _is_code_path(path):
            continue
        if add_str.isdigit():
            current_added += int(add_str)
        if rem_str.isdigit():
            current_removed += int(rem_str)

    if seen_commit_marker:
        commits.append({
            "number": len(commits) + 1,
            "hash": current_hash,
            "short_hash": current_short_hash,
            "period": current_period,
            "url": f"{commit_web_base}{current_hash}" if commit_web_base and current_hash else "",
            "added": current_added,
            "removed": current_removed,
            "net": current_added - current_removed,
        })

    # Drop bulk setup/vendor commits that would otherwise dominate the charts.
    commits = [
        c for c in commits
        if (c["added"] + c["removed"]) <= _MAX_COMMIT_LINES
    ]

    # Renumber and attach a running net line total. git log is newest-first, so
    # accumulate in chronological (reversed) order.
    running_total = 0
    for c in reversed(commits):
        running_total += c["net"]
        c["cumulative"] = running_total
    for idx, c in enumerate(commits, start=1):
        c["number"] = idx

    # Shift the whole series so the newest commit (commits[0], the running total)
    # lands on the measured current line count.
    if anchor_total is not None and commits:
        offset = anchor_total - commits[0]["cumulative"]
        for c in commits:
            c["cumulative"] += offset

    return commits


@login_required
def about_page(request):
    total_lines, total_files, by_type = _count_lines_of_code()
    python_lines = by_type.get(".py", 0)
    html_lines = by_type.get(".html", 0)
    css_lines = by_type.get(".css", 0)
    js_lines = by_type.get(".js", 0)

    def pct(n):
        return round(n / total_lines * 100, 1) if total_lines else 0

    db_stats = _get_db_stats()
    media_stats = _get_media_stats()
    commit_stats = _get_commit_line_stats(anchor_total=total_lines)

    context = {
        "total_lines": total_lines,
        "total_files": total_files,
        "python_lines": python_lines,
        "html_lines": html_lines,
        "css_lines": css_lines,
        "js_lines": js_lines,
        "python_pct": pct(python_lines),
        "html_pct": pct(html_lines),
        "css_pct": pct(css_lines),
        "js_pct": pct(js_lines),
        "db": db_stats,
        "media": media_stats,
        "commit_stats": commit_stats,
        "can_view_commit_links": bool(request.user.is_staff or request.user.is_superuser),
    }
    return render(request, "stock_take/about.html", context)
