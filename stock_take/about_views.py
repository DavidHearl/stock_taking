import os
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.db import connection
from django.contrib.auth.models import User


def _count_lines_of_code():
    """Count lines of code in first-party application directories."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app_dirs = ["stock_take", "stock_taking", "material_generator", "templates", "order_generator_files", "static"]
    extensions = {".py", ".html", ".css", ".js"}
    exclude = {"__pycache__", "migrations", "bootstrap-icons.min.css", "fonts"}

    by_type = {}
    total_files = 0

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
                            lines = len(f.readlines())
                        by_type[ext] = by_type.get(ext, 0) + lines
                        total_files += 1
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
    """Walk the MEDIA_ROOT and return file count and total size by subfolder."""
    media_root = getattr(settings, "MEDIA_ROOT", None)
    if not media_root or not os.path.isdir(media_root):
        return {"total_files": 0, "total_size": "0 B", "folders": []}

    total_files = 0
    total_bytes = 0
    folder_stats = {}

    for dirpath, dirnames, filenames in os.walk(media_root):
        # Compute top-level subfolder name
        rel = os.path.relpath(dirpath, media_root)
        top = rel.split(os.sep)[0] if rel != "." else "(root)"
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            try:
                size = os.path.getsize(fpath)
            except OSError:
                size = 0
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
    }
    return render(request, "stock_take/about.html", context)
