"""
Management command: merge_duplicate_orders
───────────────────────────────────────────
Collapse duplicate ``Order`` rows that share the same ``sale_number``.

Some sales ended up with TWO order records for one Anthill activity: the order
the live ``AnthillSale`` points to (which carries the correct fit date and the
calendar appointment) and an older orphan order that holds the actual work — the
purchase orders (``po_projects``) and CAD number — but a STALE fit date and its
own appointment. Because the calendar draws a card for every ``FitAppointment``,
the orphan shows up as a phantom card on the wrong day (e.g. James Breslin's
1/3 July jobs appearing on 13/27 June).

Resolution (per the chosen policy): KEEP THE ORDER WITH THE PURCHASE ORDERS and
copy the correct fit date onto it, re-home every other relation from the
duplicate onto the survivor, repoint the ``AnthillSale`` at the survivor, keep a
single appointment on the correct date, then delete the duplicate.

Survivor selection per ``sale_number`` group (exactly two orders expected):
  1. the order with more ``po_projects`` wins; otherwise
  2. the order linked to the ``AnthillSale`` (it holds the live fit date); else
  3. the lowest id.

Correct fit date = the live ``AnthillSale.fit_date`` if set, else the linked
order's fit date, else the survivor's, else the loser's. Finished jobs
(``survivor.job_finished``) keep their existing date untouched.

Usage:
    python manage.py merge_duplicate_orders                 # dry-run (default)
    python manage.py merge_duplicate_orders --sale-number 425306
    python manage.py merge_duplicate_orders --fix           # apply changes
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Count

from stock_take.models import Order, AnthillSale, FitAppointment


class Command(BaseCommand):
    help = (
        'Merge duplicate Order rows sharing a sale_number — keep the order with '
        'the purchase orders, copy the correct fit date onto it, delete the rest.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--sale-number',
            type=str,
            default=None,
            help='Only process this single sale_number',
        )
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Actually apply the merges (default: dry-run)',
        )

    def handle(self, *args, **options):
        fix = options['fix']
        sn_filter = options.get('sale_number')

        groups = (
            Order.objects.exclude(sale_number='')
            .values('sale_number')
            .annotate(n=Count('id'))
            .filter(n__gt=1)
            .order_by('sale_number')
        )
        if sn_filter:
            groups = groups.filter(sale_number=sn_filter)

        mode = self.style.SUCCESS('APPLY') if fix else self.style.WARNING('DRY-RUN')
        self.stdout.write(f'{mode} — {groups.count()} duplicate sale_number group(s)\n')

        merged = skipped = 0
        for g in groups:
            sn = g['sale_number']
            orders = list(Order.objects.filter(sale_number=sn).order_by('id'))

            if len(orders) != 2:
                self.stdout.write(self.style.WARNING(
                    f'SKIP {sn}: {len(orders)} orders (expected 2) — needs manual review'
                ))
                skipped += 1
                continue

            sale = AnthillSale.objects.filter(order__in=orders).first()
            linked = sale.order if sale else None

            o_a, o_b = orders
            pa, pb = o_a.po_projects.count(), o_b.po_projects.count()
            if pa > pb:
                survivor, loser = o_a, o_b
            elif pb > pa:
                survivor, loser = o_b, o_a
            elif linked is not None:
                survivor = linked
                loser = o_b if survivor.id == o_a.id else o_a
            else:
                survivor, loser = o_a, o_b

            if survivor.job_finished:
                canonical_date = survivor.fit_date
            elif sale and sale.fit_date:
                canonical_date = sale.fit_date
            elif linked and linked.fit_date:
                canonical_date = linked.fit_date
            elif survivor.fit_date:
                canonical_date = survivor.fit_date
            else:
                canonical_date = loser.fit_date

            self.stdout.write(
                f'{sn}: KEEP Order {survivor.id} (pos={survivor.po_projects.count()}, '
                f'fit_date {survivor.fit_date} → {canonical_date}) | '
                f'DELETE Order {loser.id} (pos={loser.po_projects.count()}, fit_date {loser.fit_date})'
            )

            if not fix:
                self._describe(survivor, loser)
                continue

            with transaction.atomic():
                self._merge(sale, survivor, loser, canonical_date)
            merged += 1
            self.stdout.write(self.style.SUCCESS(f'  ✓ merged {sn}'))

        self.stdout.write('')
        if fix:
            self.stdout.write(self.style.SUCCESS(
                f'Done — merged {merged} group(s), skipped {skipped}.'
            ))
        else:
            self.stdout.write(self.style.WARNING(
                f'Dry-run — would merge {groups.count() - skipped} group(s), '
                f'skipped {skipped}. Re-run with --fix to apply.'
            ))

    # ── helpers ──────────────────────────────────────────────────────────────
    # Reverse FK accessors whose FK uses on_delete=SET_NULL: deleting the loser
    # would orphan these rows (order set to NULL), so ALWAYS move them onto the
    # survivor. A union is safe — each row is a distinct, still-valid link.
    SET_NULL_ACCESSORS = [
        'po_projects', 'timesheets', 'expenses', 'gallery_images',
        'fitter_upload_submissions',
    ]
    # Reverse FK accessors whose FK uses on_delete=CASCADE: these are per-order
    # content (line items, workflow stage dates, …). The survivor usually has its
    # own complete set, so only adopt the loser's when the survivor has NONE —
    # otherwise the loser's are duplicates and get cascade-deleted with it.
    CASCADE_ACCESSORS = [
        'notes', 'os_doors', 'accessories', 'po_allocations', 'remedials',
        'csv_skip_items', 'stage_dates', 'validation_requests',
    ]

    def _describe(self, survivor, loser):
        """Print the relations that would be re-homed off the duplicate order."""
        for accessor in self.SET_NULL_ACCESSORS:
            n = getattr(loser, accessor).count()
            if n:
                self.stdout.write(f'    move {n} × {accessor} (preserve)')
        for accessor in self.CASCADE_ACCESSORS:
            n = getattr(loser, accessor).count()
            if n and getattr(survivor, accessor).count() == 0:
                self.stdout.write(f'    adopt {n} × {accessor}')
            elif n:
                self.stdout.write(f'    drop {n} × {accessor} (survivor already has '
                                  f'{getattr(survivor, accessor).count()})')

    def _merge(self, sale, survivor, loser, canonical_date):
        # 1a. SET_NULL relations — always move onto the survivor so deleting the
        #     loser cannot orphan them.
        for accessor in self.SET_NULL_ACCESSORS:
            manager = getattr(loser, accessor)
            manager.all().update(**{manager.field.name: survivor})

        # 1b. CASCADE relations — adopt only when the survivor has none of that
        #     relation; otherwise leave the loser's to be cascade-deleted.
        for accessor in self.CASCADE_ACCESSORS:
            if getattr(survivor, accessor).count() == 0:
                manager = getattr(loser, accessor)
                manager.all().update(**{manager.field.name: survivor})

        # 2. workflow_progress is OneToOne — move it only if the survivor lacks
        #    one, otherwise the loser's gets cascade-deleted with the order.
        loser_wf = getattr(loser, 'workflow_progress', None)
        if loser_wf is not None:
            survivor_has_wf = hasattr(survivor, 'workflow_progress') and \
                getattr(survivor, 'workflow_progress', None) is not None
            if not survivor_has_wf:
                loser_wf.order = survivor
                loser_wf.save(update_fields=['order'])

        # 3. Forward fields that identify work on the loser — copy onto the
        #    survivor only where the survivor has nothing, so we never overwrite
        #    the survivor's own data.
        scalar_backfill = [
            'customer_number', 'workguru_id', 'original_csv', 'processed_csv',
            'original_csv_uploaded_at', 'processed_csv_created_at',
        ]
        changed_fields = []
        for fname in scalar_backfill:
            if not getattr(survivor, fname) and getattr(loser, fname):
                setattr(survivor, fname, getattr(loser, fname))
                changed_fields.append(fname)
        if survivor.boards_po_id is None and loser.boards_po_id is not None:
            survivor.boards_po_id = loser.boards_po_id
            changed_fields.append('boards_po')

        # 4. Forward M2M — union the loser's into the survivor's.
        survivor.additional_boards_pos.add(*loser.additional_boards_pos.all())
        survivor.additional_os_doors_pos.add(*loser.additional_os_doors_pos.all())

        # 5. Correct fit date onto the survivor.
        if canonical_date and survivor.fit_date != canonical_date:
            survivor.fit_date = canonical_date
            changed_fields.append('fit_date')
        if changed_fields:
            survivor.save(update_fields=list(set(changed_fields)))

        # 6. Keep exactly one appointment on the survivor at the correct date.
        appts = list(FitAppointment.objects.filter(order__in=[survivor, loser]))
        best = None
        if canonical_date:
            best = next((a for a in appts if a.fit_date == canonical_date), None)
        if best is None:
            best = next((a for a in appts if a.order_id == survivor.id),
                        appts[0] if appts else None)
        if best is not None:
            updates = []
            if best.order_id != survivor.id:
                best.order = survivor
                updates.append('order')
            if canonical_date and best.fit_date != canonical_date:
                best.fit_date = canonical_date
                updates.append('fit_date')
            if updates:
                best.save(update_fields=updates)
            for a in appts:
                if a.id != best.id:
                    a.delete()

        # 7. Repoint every AnthillSale that referenced either order at the survivor.
        for s in AnthillSale.objects.filter(order__in=[survivor, loser]):
            s_updates = []
            if s.order_id != survivor.id:
                s.order = survivor
                s_updates.append('order')
            if canonical_date and s.fit_date != canonical_date:
                s.fit_date = canonical_date
                s_updates.append('fit_date')
            if s_updates:
                s.save(update_fields=s_updates)

        # 8. Delete the now-emptied duplicate order.
        loser.delete()
