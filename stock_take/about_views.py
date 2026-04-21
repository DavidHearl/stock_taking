import os
import re
import subprocess
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.db import connection
from django.contrib.auth.models import User


_SCRIPT_RE = re.compile(r'<script(?:\s[^>]*)?>(.+?)</script>', re.DOTALL | re.IGNORECASE)


def _count_lines_of_code():
    """Count lines of code in first-party application directories.

    JavaScript inside <script> tags in .html files is counted as JS, not HTML.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app_dirs = ["stock_take", "stock_taking", "material_generator", "templates", "order_generator_files", "static"]
    extensions = {".py", ".html", ".css", ".js"}
    exclude = {"__pycache__", "migrations", "bootstrap-icons.min.css", "fonts"}

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


def _get_commit_line_stats():
    """Return commit index with per-commit additions/removals from git history."""
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
            ["git", "log", "--pretty=tformat:COMMIT\t%H\t%h", "--numstat"],
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
                    "url": f"{commit_web_base}{current_hash}" if commit_web_base and current_hash else "",
                    "added": current_added,
                    "removed": current_removed,
                    "net": current_added - current_removed,
                })

            marker_parts = line.split("\t")
            current_hash = marker_parts[1] if len(marker_parts) > 1 else ""
            current_short_hash = marker_parts[2] if len(marker_parts) > 2 else current_hash[:8]
            seen_commit_marker = True
            current_added = 0
            current_removed = 0
            continue

        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add_str, rem_str = parts[0], parts[1]
        if add_str.isdigit():
            current_added += int(add_str)
        if rem_str.isdigit():
            current_removed += int(rem_str)

    if seen_commit_marker:
        commits.append({
            "number": len(commits) + 1,
            "hash": current_hash,
            "short_hash": current_short_hash,
            "url": f"{commit_web_base}{current_hash}" if commit_web_base and current_hash else "",
            "added": current_added,
            "removed": current_removed,
            "net": current_added - current_removed,
        })

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
    commit_stats = _get_commit_line_stats()

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
